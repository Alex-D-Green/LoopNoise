# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Loop Noise engine.

Python add-ons cannot define new F-Curve modifier types, so a Loop Noise is a
native Envelope modifier (the "carrier") plus its settings stored on the Action.
Blender evaluates the carrier itself, so files play and export correctly even
without this add-on.

The noise comes from Blender's own Noise modifier, sampled around a circle in the
(time, phase) plane: the modifier divides time by Scale but uses Phase as an
unscaled second noise dimension. A closed path through the noise field returns to
its start, so the result repeats exactly every loop and keeps the character of a
Noise modifier with the same settings.

Each carrier point maps the curve value as ``v' = a * v + b``, which covers every
Noise blend type. With reference value 0 and default range [-1, 1], an Envelope
point (min, max) maps v to ``min + (v + 1) / 2 * (max - min)``, so
``min = b - a`` and ``max = b + a`` give exactly ``a * v + b``.
"""

__all__ = (
    "CARRIER_NAME",
    "add_loop_noise",
    "apply_snapshot",
    "carriers_with_settings",
    "cycles_period",
    "default_range",
    "is_action_fcurve",
    "rebuild",
    "rebuild_all",
    "remove_loop_noise",
    "scene_range",
    "sine_cycles",
    "snapshot",
    "suspend_updates",
    "updates_suspended",
)

import math
from contextlib import contextmanager

import bpy

CARRIER_NAME = "Loop Noise"
SAMPLER_NAME = ".LoopNoiseSampler"
# Envelope points are inserted one at a time; keep pathological ranges bounded.
MAX_POINTS = 20000

_NOISE_PROPS = ("blend_type", "scale", "strength", "depth", "lacunarity", "roughness")
# What Copy to Selected transfers: the Loop Noise settings, and the native modifier
# options of the carrier (restricted range last, so start/end clamping works out).
_COPY_PROPS = _NOISE_PROPS + (
    "offset", "phase", "loop_start", "loop_end", "repeat", "samples_per_frame", "randomness", "cycles",
)

# Measured: native Noise (Depth 0) completes one wave about every 2.08 x Scale frames.
NOISE_WAVE_FRAMES_PER_SCALE = 2.08
# Spreads the sine start of nearby Phase values (1, 2, 3...) around the circle.
_GOLDEN_RATIO_FRACTION = 0.6180339887498949
_CARRIER_COPY_PROPS = (
    "mute", "use_influence", "influence",
    "use_restricted_range", "frame_start", "frame_end", "blend_in", "blend_out",
)

_suspend_depth = 0


@contextmanager
def suspend_updates():
    """Set several settings without rebuilding the carrier after each one."""
    global _suspend_depth
    _suspend_depth += 1
    try:
        yield
    finally:
        _suspend_depth -= 1


def updates_suspended():
    return _suspend_depth > 0


# -----------------------------------------------------------------------------
# Locating curves and carriers


def is_action_fcurve(fcurve):
    """True for F-Curves stored in an editable Action (not drivers)."""
    action = fcurve.id_data
    return isinstance(action, bpy.types.Action) and action.is_editable


def _channelbags(action):
    for layer in action.layers:
        for strip in layer.strips:
            yield from getattr(strip, "channelbags", ())


def _slot_handle(fcurve):
    for channelbag in _channelbags(fcurve.id_data):
        if channelbag.fcurves.find(fcurve.data_path, index=fcurve.array_index) == fcurve:
            return channelbag.slot_handle
    return 0


def _carrier(fcurve, name):
    return next((mod for mod in fcurve.modifiers if mod.type == 'ENVELOPE' and mod.name == name), None)


def _hinted_fcurve(settings):
    """The curve at the location last recorded in settings, or (None, None)."""
    for channelbag in _channelbags(settings.id_data):
        if channelbag.slot_handle == settings.slot_handle:
            fcurve = channelbag.fcurves.find(settings.data_path, index=settings.array_index)
            if fcurve is not None:
                return channelbag, fcurve
    return None, None


def _resolve(settings):
    """Return (channelbag, fcurve, carrier) for settings, or three Nones if the carrier is gone."""
    name = settings.modifier_name
    channelbag, fcurve = _hinted_fcurve(settings)
    if fcurve is not None:
        carrier = _carrier(fcurve, name)
        if carrier is not None:
            return channelbag, fcurve, carrier
    # The curve moved, e.g. renaming a bone changes its data path.
    for channelbag in _channelbags(settings.id_data):
        for fcurve in channelbag.fcurves:
            carrier = _carrier(fcurve, name)
            if carrier is not None:
                return channelbag, fcurve, carrier
    return None, None, None


def carriers_with_settings(fcurve):
    """(carrier, settings) pairs for the Loop Noise modifiers on fcurve, in stack order."""
    items = fcurve.id_data.loop_noise_items
    if not len(items):
        return []
    slot_handle = _slot_handle(fcurve)
    pairs = []
    for mod in fcurve.modifiers:
        if mod.type != 'ENVELOPE':
            continue
        match = None
        for settings in items:
            if settings.modifier_name != mod.name:
                continue
            if (settings.slot_handle, settings.data_path, settings.array_index) == (
                    slot_handle, fcurve.data_path, fcurve.array_index):
                match = settings
                break
            if match is None:
                # Accept a moved curve, but not a copy of a carrier that still exists elsewhere.
                _channelbag, hinted = _hinted_fcurve(settings)
                if hinted is None or _carrier(hinted, settings.modifier_name) is None:
                    match = settings
        if match is not None:
            pairs.append((mod, match))
    return pairs


def cycles_period(fcurve):
    """Length of the cycle a Cycles modifier repeats on fcurve, or None without one."""
    mods = fcurve.modifiers
    if not len(mods) or mods[0].type != 'CYCLES' or mods[0].mute:
        return None
    keys = fcurve.keyframe_points
    if len(keys) < 2:
        return None
    return keys[-1].co.x - keys[0].co.x


def scene_range(scene):
    """The preview range when enabled, otherwise the scene range (inclusive)."""
    if scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    return scene.frame_start, scene.frame_end


def default_range(fcurve, scene):
    """The cycle of a cyclic curve, otherwise the preview or scene range (both inclusive)."""
    period = cycles_period(fcurve)
    if period is not None:
        start = round(fcurve.keyframe_points[0].co.x)
        end = start + round(period) - 1
        if end > start:
            return start, end
    return scene_range(scene)


def _unique_name(action):
    used = {mod.name for channelbag in _channelbags(action) for fcurve in channelbag.fcurves for mod in fcurve.modifiers}
    if CARRIER_NAME not in used:
        return CARRIER_NAME
    number = 1
    while "{:s}.{:03d}".format(CARRIER_NAME, number) in used:
        number += 1
    return "{:s}.{:03d}".format(CARRIER_NAME, number)


# -----------------------------------------------------------------------------
# Sampling


def _sampler():
    """Two scratch curves with base values 0 and 1, each carrying a native Noise modifier.

    They live in a hidden Action without users, so it is never saved.
    """
    action = bpy.data.actions.get((SAMPLER_NAME, None))
    if action is None:
        action = bpy.data.actions.new(SAMPLER_NAME)
    slot = action.slots[0] if action.slots else action.slots.new(id_type='OBJECT', name="Sampler")
    layer = action.layers[0] if action.layers else action.layers.new("Layer")
    strip = layer.strips[0] if layer.strips else layer.strips.new(type='KEYFRAME')
    channelbag = strip.channelbag(slot, ensure=True)
    pairs = []
    for index, base in enumerate((0.0, 1.0)):
        fcurve = channelbag.fcurves.find("location", index=index)
        if fcurve is None:
            fcurve = channelbag.fcurves.new("location", index=index)
            fcurve.keyframe_points.insert(0.0, base)
        noise = next((mod for mod in fcurve.modifiers if mod.type == 'NOISE'), None)
        if noise is None:
            noise = fcurve.modifiers.new('NOISE')
            noise.use_legacy_noise = False
        pairs.append((fcurve, noise))
    return pairs


def _sample_period(settings, period, count):
    """Slope a and offset b of the native Noise at count evenly spaced points of one loop."""
    (base0, noise0), (base1, noise1) = _sampler()
    for noise in (noise0, noise1):
        for prop in _NOISE_PROPS:
            setattr(noise, prop, getattr(settings, prop))
        noise.offset = 0.0

    # The circle's circumference in noise space equals the loop length, so the noise
    # moves at the same speed as a regular Noise modifier with this Scale.
    time_radius = period / math.tau
    phase_radius = time_radius / max(abs(settings.scale), 1e-6)
    shift = settings.offset / period

    slopes, offsets = [], []
    for j in range(count):
        angle = math.tau * (j / count - shift)
        noise0.phase = noise1.phase = settings.phase + phase_radius * math.sin(angle)
        time = time_radius * math.cos(angle)
        offset = base0.evaluate(time)
        offsets.append(offset)
        slopes.append(base1.evaluate(time) - offset)
    return slopes, offsets


def sine_cycles(settings, period, count):
    """Sine waves per loop: the Cycles setting, or the count matching the noise tempo when 0."""
    cycles = settings.cycles
    if cycles == 0:
        cycles = round(period / (NOISE_WAVE_FRAMES_PER_SCALE * max(abs(settings.scale), 1e-6)))
    # At least one wave, and at least two samples per wave.
    return min(max(cycles, 1), max(1, (count - 1) // 2))


def _rms(values):
    return math.sqrt(math.fsum(v * v for v in values) / len(values))


def _blend_with_sine(series, settings, period, count):
    """Mix a sine into one loop of samples, keeping their mean and overall size (RMS)."""
    mean = math.fsum(series) / count
    deviations = [value - mean for value in series]
    rms = _rms(deviations)
    if rms == 0.0:
        return series  # e.g. the constant slope of additive blend types
    # Equal-power weights (cos^2 + sin^2 = 1) with a sine of the same RMS as the noise.
    angle = settings.randomness * math.pi / 2.0
    noise_weight = math.sin(angle)
    amplitude = math.sqrt(2.0) * rms * math.cos(angle)  # a sine's RMS is amplitude / sqrt(2)
    cycles = sine_cycles(settings, period, count)
    shift = settings.offset / period
    start = math.tau * ((settings.phase * _GOLDEN_RATIO_FRACTION) % 1.0)
    mixed = [
        amplitude * math.sin(math.tau * cycles * (j / count - shift) + start) + noise_weight * deviation
        for j, deviation in enumerate(deviations)
    ]
    # Within one loop the sine and the noise are partly correlated by chance, so the mix
    # can come out bigger or smaller; rescale it to the noise's size exactly.
    mixed_rms = _rms(mixed)
    scale = rms / mixed_rms if mixed_rms > 1e-12 else 1.0
    return [mean + value * scale for value in mixed]


def _coverage(settings, scene, period, per_frame):
    """First sample index (relative to the loop start) and the number of samples to write."""
    count = period * per_frame
    if not settings.repeat:
        return 0, count + 1
    start = settings.loop_start
    low = min(start, scene.frame_start)
    high = max(start + period, scene.frame_end)
    if scene.use_preview_range:
        low = min(low, scene.frame_preview_start)
        high = max(high, scene.frame_preview_end)
    # One spare loop on each side covers small range changes and motion-blur subframes.
    first = math.floor((low - start) / period) - 1
    last = math.ceil((high - start) / period) + 1
    budget = max(1, (MAX_POINTS - 1) // count)
    if last - first > budget:
        spare = budget - 1
        first, last = -(spare // 2), 1 + spare - spare // 2
    return first * count, (last - first) * count + 1


def _write_points(carrier, frames, mins, maxs):
    points = carrier.control_points
    old_count, new_count = len(points), len(frames)
    old_frames = [0.0] * old_count
    points.foreach_get("frame", old_frames)
    if old_count != new_count or any(abs(a - b) > 1e-4 for a, b in zip(old_frames, frames)):
        if old_count < new_count:
            # Add unique placeholder frames past the end, then set all frames at once.
            after = (old_frames[-1] if old_count else 0.0) + 1.0
            for i in range(new_count - old_count):
                points.add(after + i)
        else:
            for _ in range(old_count - new_count):
                points.remove(points[-1])
        points.foreach_set("frame", frames)
    points.foreach_set("min", mins)
    points.foreach_set("max", maxs)
    carrier.default_min = -1.0
    carrier.default_max = 1.0
    # Assigned last: the RNA update re-evaluates the animation.
    carrier.reference_value = 0.0
    _redraw_editors()


def _redraw_editors():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


# -----------------------------------------------------------------------------
# Public operations


def rebuild(settings, scene):
    """Regenerate the carrier from settings. Returns False if the carrier no longer exists."""
    channelbag, fcurve, carrier = _resolve(settings)
    if carrier is None:
        return False
    if settings.data_path != fcurve.data_path:
        settings.data_path = fcurve.data_path
    if settings.array_index != fcurve.array_index:
        settings.array_index = fcurve.array_index
    if settings.slot_handle != channelbag.slot_handle:
        settings.slot_handle = channelbag.slot_handle

    period = settings.loop_end - settings.loop_start + 1
    if period < 2:
        _write_points(carrier, [], [], [])  # An empty Envelope leaves the curve unchanged.
        return True

    per_frame = settings.samples_per_frame
    count = period * per_frame
    slopes, offsets = _sample_period(settings, period, count)
    if settings.randomness < 1.0:
        slopes = _blend_with_sine(slopes, settings, period, count)
        offsets = _blend_with_sine(offsets, settings, period, count)
    first, total = _coverage(settings, scene, period, per_frame)
    loop_index = [(first + i) % count for i in range(total)]
    frames = [settings.loop_start + (first + i) / per_frame for i in range(total)]
    mins = [offsets[k] - slopes[k] for k in loop_index]
    maxs = [offsets[k] + slopes[k] for k in loop_index]
    _write_points(carrier, frames, mins, maxs)
    return True


def cleanup_orphans(action):
    """Drop settings whose carrier was deleted (or renamed) in the modifier stack."""
    items = action.loop_noise_items
    for index in reversed(range(len(items))):
        if _resolve(items[index])[2] is None:
            items.remove(index)


def rebuild_all(scene, repeating_only=False):
    for action in bpy.data.actions:
        if not action.is_editable or not len(action.loop_noise_items):
            continue
        cleanup_orphans(action)
        for settings in action.loop_noise_items:
            if settings.repeat or not repeating_only:
                rebuild(settings, scene)


def add_loop_noise(fcurve, scene, phase):
    """Add a Loop Noise to the end of fcurve's modifier stack and return its settings."""
    action = fcurve.id_data
    cleanup_orphans(action)
    carrier = fcurve.modifiers.new('ENVELOPE')
    carrier.name = _unique_name(action)
    carrier.show_expanded = False  # Hundreds of points; edit the settings in the Loop Noise panel.
    start, end = default_range(fcurve, scene)
    # Native modifiers leave the restricted range at 0-0, so ticking Restrict Frame Range
    # makes the effect vanish; start from the loop range instead (still switched off).
    carrier.frame_end = end
    carrier.frame_start = start
    settings = action.loop_noise_items.add()
    with suspend_updates():
        settings.slot_handle = _slot_handle(fcurve)
        settings.data_path = fcurve.data_path
        settings.array_index = fcurve.array_index
        settings.modifier_name = carrier.name
        settings.loop_start = start
        settings.loop_end = end
        settings.phase = phase
    rebuild(settings, scene)
    return settings


def snapshot(carrier, settings):
    """Plain-value copy of a Loop Noise.

    Adding settings can reallocate the collection and invalidate references to its
    items, so copy values out before touching other curves.
    """
    return (
        {prop: getattr(settings, prop) for prop in _COPY_PROPS},
        {prop: getattr(carrier, prop) for prop in _CARRIER_COPY_PROPS},
    )


def apply_snapshot(snap, fcurve, scene, phase=None):
    """Give fcurve's first Loop Noise (added if it has none) the values in snap.

    phase, when given, replaces the copied Phase.
    """
    values, carrier_values = snap
    pairs = carriers_with_settings(fcurve)
    if pairs:
        carrier, settings = pairs[0]
    else:
        settings = add_loop_noise(fcurve, scene, phase=0.0)
        carrier = _resolve(settings)[2]
    with suspend_updates():
        for prop, value in values.items():
            setattr(settings, prop, value)
        if phase is not None:
            settings.phase = phase
    for prop, value in carrier_values.items():
        setattr(carrier, prop, value)
    rebuild(settings, scene)


def remove_loop_noise(fcurve, carrier, settings):
    fcurve.modifiers.remove(carrier)
    items = fcurve.id_data.loop_noise_items
    for index, item in enumerate(items):
        if item == settings:
            items.remove(index)
            break
    # Removing a modifier from Python neither re-evaluates the animation nor redraws
    # the editors (the native X button does both).
    fcurve.update()
    _redraw_editors()
