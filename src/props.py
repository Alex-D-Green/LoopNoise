# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Loop Noise settings, stored on the Action (F-Curves and modifiers cannot hold custom data)."""

__all__ = (
    "LoopNoiseSettings",
)

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from . import core


def _update(self, context):
    if not core.updates_suspended():
        core.rebuild(self, context.scene)


# The noise controls copy name, tooltip, default and ranges from the native Noise
# modifier so they behave the same.

def _native(name):
    return bpy.types.FModifierNoise.bl_rna.properties[name]


def _mirror_float(name, default=None):
    prop = _native(name)
    return FloatProperty(
        name=prop.name,
        description=prop.description,
        default=prop.default if default is None else default,
        min=prop.hard_min,
        max=prop.hard_max,
        soft_min=prop.soft_min,
        soft_max=prop.soft_max,
        step=prop.step,
        precision=prop.precision,
        options=set(),
        update=_update,
    )


def _mirror_int(name):
    prop = _native(name)
    return IntProperty(
        name=prop.name,
        description=prop.description,
        default=prop.default,
        min=prop.hard_min,
        max=prop.hard_max,
        soft_min=prop.soft_min,
        soft_max=prop.soft_max,
        options=set(),
        update=_update,
    )


def _mirror_enum(name):
    prop = _native(name)
    return EnumProperty(
        name=prop.name,
        description=prop.description,
        items=[(item.identifier, item.name, item.description) for item in prop.enum_items],
        default=prop.default,
        options=set(),
        update=_update,
    )


class LoopNoiseSettings(bpy.types.PropertyGroup):
    # Where the carrier modifier lives; kept up to date by core.rebuild().
    slot_handle: IntProperty(options={'HIDDEN'})
    data_path: StringProperty(options={'HIDDEN'})
    array_index: IntProperty(options={'HIDDEN'})
    modifier_name: StringProperty(options={'HIDDEN'})

    loop_start: IntProperty(
        name="Start",
        description="First frame of the loop",
        default=1,
        options=set(),
        update=_update,
    )
    loop_end: IntProperty(
        name="End",
        description="Last frame of the loop. The frame after it repeats Start exactly, "
        "so playing Start to End in a loop is seamless",
        default=250,
        options=set(),
        update=_update,
    )
    repeat: BoolProperty(
        name="Repeat",
        description="Keep looping before and after the frame range, across the scene range",
        default=True,
        options=set(),
        update=_update,
    )
    samples_per_frame: IntProperty(
        name="Samples per Frame",
        description="Noise samples stored per frame. Values between samples are interpolated linearly",
        default=1,
        min=1,
        max=16,
        options=set(),
        update=_update,
    )

    randomness: FloatProperty(
        name="Randomness",
        description="Blend between a pure sine wave (0) and pure noise (1). "
        "The overall amount of motion stays the same",
        default=1.0,
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        options=set(),
        update=_update,
    )
    cycles: IntProperty(
        name="Cycles",
        description="Sine waves per loop. 0 picks the count closest to the noise's tempo, set by Scale",
        default=0,
        min=0,
        soft_max=50,
        options=set(),
        update=_update,
    )

    blend_type: _mirror_enum("blend_type")
    # Native Noise defaults to Scale 1, which is frame-to-frame jitter; 10 is a usable start.
    scale: _mirror_float("scale", default=10.0)
    strength: _mirror_float("strength")
    offset: _mirror_float("offset")
    phase: _mirror_float("phase")
    depth: _mirror_int("depth")
    lacunarity: _mirror_float("lacunarity")
    roughness: _mirror_float("roughness")

    # Open/closed state of this Loop Noise's panel and its collapsible sections.
    show_expanded: BoolProperty(default=True, options=set())
    show_sine: BoolProperty(default=False, options=set())
    show_restrict: BoolProperty(default=False, options=set())
