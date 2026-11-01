# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Headless tests for the Loop Noise engine.

    blender --background --factory-startup --python-exit-code 1 --python tests/test_loop_noise.py
"""

import importlib.util
import math
import os
import statistics
import sys
import tempfile

import bpy
from bpy_extras import anim_utils

# The add-on's source lives in src/; load it under its extension id, as Blender does.
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
_spec = importlib.util.spec_from_file_location(
    "loop_noise", os.path.join(SRC, "__init__.py"), submodule_search_locations=[SRC])
loop_noise = importlib.util.module_from_spec(_spec)
sys.modules["loop_noise"] = loop_noise
_spec.loader.exec_module(loop_noise)

from loop_noise import core  # noqa: E402

loop_noise.register()

FAILED = []


def check(name, ok, detail=""):
    print("{:s} {:s}{:s}".format("PASS" if ok else "FAIL", name, "  [{}]".format(detail) if detail else ""))
    if not ok:
        FAILED.append(name)


def fcurve_of(ob, data_path, index):
    ad = ob.animation_data
    return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot).fcurves.find(data_path, index=index)


def keyed_empty(name, value=0.5, index=2):
    ob = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(ob)
    ob.location[index] = value
    ob.keyframe_insert("location", index=index, frame=1)
    return ob, fcurve_of(ob, "location", index)


def configure(settings, **values):
    with core.suspend_updates():
        for key, value in values.items():
            setattr(settings, key, value)
    core.rebuild(settings, bpy.context.scene)


def frange(start, stop, step):
    count = int(round((stop - start) / step))
    return [start + i * step for i in range(count + 1)]


scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 250


# --- Seamless loop -----------------------------------------------------------
ob, fc = keyed_empty("Probe")
s = core.add_loop_noise(fc, scene, phase=1.0)
check("default range is the scene range", (s.loop_start, s.loop_end) == (1, 250), (s.loop_start, s.loop_end))
new_carrier = fc.modifiers[-1]
check("restrict range pre-filled with the loop range, but off",
      (new_carrier.frame_start, new_carrier.frame_end) == (1.0, 250.0) and not new_carrier.use_restricted_range,
      (new_carrier.frame_start, new_carrier.frame_end))
configure(s, loop_start=1, loop_end=100)
carrier = fc.modifiers[-1]
check("carrier is a native Envelope named Loop Noise", carrier.type == 'ENVELOPE' and carrier.name == "Loop Noise")
v = fc.evaluate
diff = max(abs(v(t) - v(t + 100)) for t in frange(-99, 150, 0.25))
check("repeats exactly every 100 frames (quarter-frame check)", diff == 0.0, diff)
check("frame 101 equals frame 1", v(101) == v(1), (v(1), v(101)))
check("slope into the seam equals slope out of the start", (v(101) - v(100)) == (v(1) - v(0)))
check("noise actually moves", max(v(t) for t in range(1, 101)) - min(v(t) for t in range(1, 101)) > 0.2)

# --- Non-destructive ---------------------------------------------------------
check("keyframe value untouched", abs(fc.keyframe_points[0].co.y - 0.5) < 1e-7)
carrier.mute = True
check("muted carrier gives the original curve", all(abs(v(t) - 0.5) < 1e-7 for t in range(-50, 300, 7)))
carrier.mute = False

# --- Same noise as the native modifier, sample by sample ---------------------
ref_ob, ref_fc = keyed_empty("Reference", value=0.0)
ref_noise = ref_fc.modifiers.new('NOISE')
ref_noise.use_legacy_noise = False


def expected(settings, base, j):
    period = settings.loop_end - settings.loop_start + 1
    count = period * settings.samples_per_frame
    for prop in ("blend_type", "scale", "strength", "depth", "lacunarity", "roughness"):
        setattr(ref_noise, prop, getattr(settings, prop))
    ref_noise.offset = 0.0
    radius = period / math.tau
    angle = math.tau * (j / count - settings.offset / period)
    ref_noise.phase = settings.phase + radius / abs(settings.scale) * math.sin(angle)
    ref_fc.keyframe_points[0].co.y = base
    return ref_fc.evaluate(radius * math.cos(angle))


for blend in ('REPLACE', 'ADD', 'SUBTRACT', 'MULTIPLY'):
    configure(s, blend_type=blend, depth=2, roughness=0.6, lacunarity=2.3, strength=1.5, offset=7.0)
    err = 0.0
    for base in (0.5, 2.0):
        fc.keyframe_points[0].co.y = base
        fc.update()
        for j in (0, 13, 50, 99):
            err = max(err, abs(v(1 + j) - expected(s, base, j)))
    check("{:s}: matches native Noise on the loop path".format(blend), err < 1e-5, err)
fc.keyframe_points[0].co.y = 0.5
fc.update()
configure(s, blend_type='REPLACE', depth=0, roughness=0.5, lacunarity=2.0, strength=1.0, offset=0.0)

# --- Same character as a regular Noise modifier ------------------------------
# Native Noise is calmer at whole-number Phase values (the noise lattice), and the
# loop path crosses many phases, so compare against native pooled over one Phase cell.
stat_ob, stat_fc = keyed_empty("Stats", value=0.0)
stat_s = core.add_loop_noise(stat_fc, scene, phase=3.0)
native_ob, native_fc = keyed_empty("Native", value=0.0)
native = native_fc.modifiers.new('NOISE')
native.use_legacy_noise = False


def speed(xs):
    return statistics.mean(abs(b - a) for a, b in zip(xs, xs[1:]))


for scale, depth in ((10.0, 0), (20.0, 3)):
    configure(stat_s, loop_start=1, loop_end=2000, scale=scale, depth=depth, repeat=False)
    looped = [stat_fc.evaluate(t) for t in range(1, 2001)]
    native.scale, native.depth = scale, depth
    runs = []
    for step in range(10):
        native.phase = 3.0 + step / 10
        runs.append([native_fc.evaluate(t) for t in range(1, 2001)])
    std_ratio = statistics.pstdev(looped) / statistics.pstdev([x for run in runs for x in run])
    speed_ratio = speed(looped) / statistics.mean(speed(run) for run in runs)
    check("scale {:g} depth {:d}: amplitude like native".format(scale, depth), 0.8 < std_ratio < 1.25, round(std_ratio, 3))
    check("scale {:g} depth {:d}: speed like native".format(scale, depth), 0.8 < speed_ratio < 1.25, round(speed_ratio, 3))

# --- Settings update the carrier live ----------------------------------------
before = v(50)
s.scale = 4.0  # through the property update callback
check("changing Scale rebuilds the carrier", abs(v(50) - before) > 1e-6)
s.loop_end = 60
diff = max(abs(v(t) - v(t + 60)) for t in frange(-50, 150, 0.5))
check("changing End changes the period (60)", diff == 0.0, diff)
s.loop_end = 0
check("End before Start leaves the curve unchanged", all(abs(v(t) - 0.5) < 1e-7 for t in range(-20, 120, 3)))
configure(s, loop_start=1, loop_end=100, scale=10.0)

# --- Repeat off holds the boundary value --------------------------------------
configure(s, repeat=False)
check("repeat off: one loop of points", len(carrier.control_points) == 101, len(carrier.control_points))
check("repeat off: holds before and after", v(-30) == v(1) and v(180) == v(1))
configure(s, repeat=True)

# --- Scene range changes extend the repeats -----------------------------------
scene.frame_end = 600
core.rebuild_all(scene, repeating_only=True)
diff = max(abs(v(t) - v(t + 100)) for t in frange(400, 560, 0.5))
check("longer scene range is covered after rebuild_all", diff == 0.0, diff)
scene.frame_end = 250
core.rebuild_all(scene, repeating_only=True)

# --- Several Loop Noises on one curve ----------------------------------------
s2 = core.add_loop_noise(fc, scene, phase=5.0)
pairs = core.carriers_with_settings(fc)
check("second Loop Noise gets a unique name", [m.name for m, _ in pairs] == ["Loop Noise", "Loop Noise.001"],
      [m.name for m, _ in pairs])
first_mod, first_settings = pairs[0]
core.remove_loop_noise(fc, first_mod, first_settings)
pairs = core.carriers_with_settings(fc)
check("removing one keeps the other", len(pairs) == 1 and pairs[0][0].name == "Loop Noise.001")
check("remaining one still rebuilds", core.rebuild(pairs[0][1], scene))
s = pairs[0][1]
carrier = pairs[0][0]

# --- Deleting the modifier in the stack orphans its settings ------------------
orphan_s = core.add_loop_noise(fc, scene, phase=9.0)
fc.modifiers.remove(fc.modifiers[-1])
core.cleanup_orphans(fc.id_data)
check("settings of a deleted carrier are cleaned up", len(fc.id_data.loop_noise_items) == 1,
      [i.modifier_name for i in fc.id_data.loop_noise_items])

# --- Renaming a bone keeps the link -------------------------------------------
arm = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", arm)
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode='EDIT')
bone = arm.edit_bones.new("Bone")
bone.tail = (0.0, 0.0, 1.0)
bpy.ops.object.mode_set(mode='OBJECT')
rig.pose.bones["Bone"].keyframe_insert("location", index=1, frame=1)
bone_fc = fcurve_of(rig, 'pose.bones["Bone"].location', 1)
bone_s = core.add_loop_noise(bone_fc, scene, phase=2.0)
arm.bones["Bone"].name = "Spine"
check("bone rename updates the F-Curve path", bone_fc.data_path == 'pose.bones["Spine"].location', bone_fc.data_path)
check("Loop Noise follows the renamed bone", core.rebuild(bone_s, scene) and bone_s.data_path == bone_fc.data_path)

# --- Cycles helpers ------------------------------------------------------------
cyc_ob, cyc_fc = keyed_empty("Cyclic")
cyc_ob.location.z = 0.5
cyc_ob.keyframe_insert("location", index=2, frame=121)
cyc_fc.modifiers.new('CYCLES')
check("cycles_period reads the key range", core.cycles_period(cyc_fc) == 120.0)
cyc_s = core.add_loop_noise(cyc_fc, scene, phase=4.0)
check("default range follows the cycle (1-120)", (cyc_s.loop_start, cyc_s.loop_end) == (1, 120),
      (cyc_s.loop_start, cyc_s.loop_end))
diff = max(abs(cyc_fc.evaluate(t) - cyc_fc.evaluate(t + 120)) for t in frange(1, 240, 0.5))
check("cyclic curve with Loop Noise repeats every 120", diff < 1e-6, diff)

# --- Step 2 controls -------------------------------------------------------------
ctl_ob, ctl_fc = keyed_empty("Controls", value=0.25)
ctl_s = core.add_loop_noise(ctl_fc, scene, phase=11.0)
configure(ctl_s, loop_start=1, loop_end=80)
ctl_carrier = core.carriers_with_settings(ctl_fc)[0][0]
cv = ctl_fc.evaluate
base_run = [cv(t) for t in range(-40, 200)]

ctl_s.offset = 7.0
shifted = max(abs(cv(t) - base_run[t - 7 + 40]) for t in range(-33, 200))
check("Offset 7 shifts the loop by 7 frames", shifted < 1e-6, shifted)
ctl_s.offset = 0.0

ctl_s.samples_per_frame = 4
check("4 samples per frame: 4x the points", len(ctl_carrier.control_points) > 4 * 80, len(ctl_carrier.control_points))
same = max(abs(cv(t) - base_run[t + 40]) for t in range(-40, 200))
check("4 samples per frame: same values on whole frames", same < 1e-6, same)
diff = max(abs(cv(t) - cv(t + 80)) for t in frange(-40, 110, 0.25))
check("4 samples per frame: still loops exactly", diff == 0.0, diff)
ctl_s.samples_per_frame = 1

ctl_carrier.use_influence = True
ctl_carrier.influence = 0.5
half = max(abs((cv(t) - 0.25) - 0.5 * (base_run[t + 40] - 0.25)) for t in range(-40, 200))
check("native Influence 0.5 halves the noise", half < 1e-6, half)
ctl_carrier.use_influence = False

ctl_carrier.use_restricted_range = True
ctl_carrier.frame_start, ctl_carrier.frame_end = 20.0, 60.0
outside = all(abs(cv(t) - 0.25) < 1e-7 for t in list(range(-40, 19)) + list(range(61, 200)))
inside = abs(cv(40) - base_run[80]) < 1e-6
check("native Restrict Frame Range limits the noise", outside and inside)
ctl_carrier.use_restricted_range = False

# Copy to Selected: X and Y of the same object.
for index in (0, 1):
    ctl_ob.keyframe_insert("location", index=index, frame=1)
x_fc, y_fc = fcurve_of(ctl_ob, "location", 0), fcurve_of(ctl_ob, "location", 1)
configure(ctl_s, scale=6.0, strength=0.7, depth=2, blend_type='ADD')
ctl_carrier.use_influence, ctl_carrier.influence = True, 0.8
snap = core.snapshot(ctl_carrier, core.carriers_with_settings(ctl_fc)[0][1])
core.apply_snapshot(snap, x_fc, scene, phase=123.0)
core.apply_snapshot(snap, y_fc, scene, phase=None)
x_carrier, x_s = core.carriers_with_settings(x_fc)[0]
y_carrier, y_s = core.carriers_with_settings(y_fc)[0]
src = core.carriers_with_settings(ctl_fc)[0][1]
check("copy: settings transferred", (x_s.scale, x_s.strength, x_s.depth, x_s.blend_type, x_s.loop_end)
      == (src.scale, src.strength, src.depth, src.blend_type, src.loop_end))
check("copy: native Influence transferred", x_carrier.use_influence and abs(x_carrier.influence - 0.8) < 1e-6)
check("copy: phase override kept, plain copy matches source", x_s.phase == 123.0 and y_s.phase == src.phase)
# Same settings and phase: Y moves exactly like Z, around its own key value.
same =max(abs((y_fc.evaluate(t) - y_fc.keyframe_points[0].co.y) - (cv(t) - 0.25)) for t in range(0, 160))
check("copy: identical settings give identical motion", same < 1e-6, same)
configure(ctl_s, scale=9.0)
core.apply_snapshot(core.snapshot(ctl_carrier, core.carriers_with_settings(ctl_fc)[0][1]), x_fc, scene, phase=123.0)
check("copy again updates instead of adding", len(core.carriers_with_settings(x_fc)) == 1
      and core.carriers_with_settings(x_fc)[0][1].scale == 9.0)

# --- Sine Blend ---------------------------------------------------------------------
def rms(xs):
    mean = statistics.fmean(xs)
    return math.sqrt(statistics.fmean([(x - mean) ** 2 for x in xs]))


sb_ob, sb_fc = keyed_empty("SineBlend", value=0.0)
sb_s = core.add_loop_noise(sb_fc, scene, phase=21.0)
configure(sb_s, loop_start=1, loop_end=100, scale=10.0)
sv = sb_fc.evaluate
noise_run = [sv(t) for t in range(1, 101)]
check("Randomness defaults to 1 (pure noise)", sb_s.randomness == 1.0)
check("auto Cycles follows Scale (100 frames at Scale 10 -> 5)", core.sine_cycles(sb_s, 100, 100) == 5)
configure(sb_s, scale=5.0)
check("auto Cycles follows Scale (Scale 5 -> 10)", core.sine_cycles(sb_s, 100, 100) == 10)
configure(sb_s, scale=10.0)

configure(sb_s, randomness=0.0, cycles=4)
sine_run = [sv(t) for t in range(1, 101)]
err = max(abs(sv(t) - sv(t + 25)) for t in range(-99, 150))
check("Randomness 0 is a pure sine: 4 cycles repeat every 25 frames", err < 1e-5, err)
check("Randomness 0 keeps the overall size", abs(rms(sine_run) / rms(noise_run) - 1.0) < 1e-3,
      rms(sine_run) / rms(noise_run))
check("Randomness 0 keeps the average", abs(statistics.fmean(sine_run) - statistics.fmean(noise_run)) < 1e-5)
peak_to_peak = max(sine_run) - min(sine_run)
check("Randomness 0 has a sine's peak-to-peak (2*sqrt(2)*RMS)",
      abs(peak_to_peak / (2 * math.sqrt(2) * rms(sine_run)) - 1.0) < 0.02)
diff = max(abs(sv(t) - sv(t + 100)) for t in frange(-99, 150, 0.25))
check("Randomness 0 still loops exactly", diff == 0.0, diff)

configure(sb_s, randomness=0.5)
half_run = [sv(t) for t in range(1, 101)]
ratio = rms(half_run) / rms(noise_run)
check("Randomness 0.5 keeps the overall size", abs(ratio - 1.0) < 1e-3, ratio)
for w in (0.1, 0.3, 0.7, 0.9):
    configure(sb_s, randomness=w)
    ratio_w = rms([sv(t) for t in range(1, 101)]) / rms(noise_run)
    check("Randomness {:g} keeps the overall size".format(w), abs(ratio_w - 1.0) < 1e-3, ratio_w)
configure(sb_s, randomness=0.5)
check("Randomness 0.5 differs from both ends",
      max(abs(a - b) for a, b in zip(half_run, noise_run)) > 1e-3 and max(abs(a - b) for a, b in zip(half_run, sine_run)) > 1e-3)
diff = max(abs(sv(t) - sv(t + 100)) for t in frange(-99, 150, 0.25))
check("Randomness 0.5 still loops exactly", diff == 0.0, diff)

configure(sb_s, randomness=0.3)
before_shift = [sv(t) for t in range(-60, 200)]
configure(sb_s, offset=7.0)
shifted = max(abs(sv(t) - before_shift[t - 7 + 60]) for t in range(-53, 200))
check("Offset shifts the blended loop too", shifted < 1e-5, shifted)
configure(sb_s, offset=0.0)

configure(sb_s, randomness=1.0)
check("Randomness back to 1 restores the noise exactly", [sv(t) for t in range(1, 101)] == noise_run)

sb_fc.keyframe_points[0].co.y = 2.0
sb_fc.update()
configure(sb_s, blend_type='MULTIPLY', randomness=0.0, cycles=4)
err = max(abs(sv(t) - sv(t + 25)) for t in range(-99, 150))
check("Multiply: Randomness 0 gives a sine multiplier", err < 1e-5 and rms([sv(t) for t in range(1, 101)]) > 1e-3, err)

configure(sb_s, blend_type='REPLACE', cycles=500)
diff = max(abs(sv(t) - sv(t + 100)) for t in frange(-99, 150, 0.5))
check("huge Cycles is clamped and still loops", core.sine_cycles(sb_s, 100, 100) == 49 and diff == 0.0)

configure(sb_s, randomness=0.4, cycles=3)
sb_snap = core.snapshot(core.carriers_with_settings(sb_fc)[0][0], core.carriers_with_settings(sb_fc)[0][1])
check("Copy to Selected carries Randomness and Cycles",
      sb_snap[0]["randomness"] == sb_s.randomness and sb_snap[0]["cycles"] == 3)

# --- Right-click "Add Loop Noise" on a property ---------------------------------
from loop_noise import operators  # noqa: E402


def add_to_property(id_data, data_path, array_length, index=-1, seed=7, **values):
    id_type, id_name, id_attribute = operators._id_locator(id_data)
    return bpy.ops.anim.loop_noise_add_to_property(
        id_type=id_type, id_name=id_name, id_attribute=id_attribute, data_path=data_path,
        index=index, array_length=array_length, seed=seed, **values)


def loop_noises(id_data, data_path, index):
    fcurve = operators._action_fcurve(id_data, data_path, index)
    return (fcurve, core.carriers_with_settings(fcurve)) if fcurve else (None, [])


scene.frame_set(5)
pt_ob = bpy.data.objects.new("PropTarget", None)
scene.collection.objects.link(pt_ob)
pt_ob.location = (1.0, 2.0, 3.0)
ret = add_to_property(pt_ob, "location", 3, scale=8.0, strength=0.3, loop_start=1, loop_end=48)
curves = [loop_noises(pt_ob, "location", i) for i in range(3)]
check("property: operator finished", ret == {'FINISHED'})
check("property: X, Y and Z each got one Loop Noise", all(len(pairs) == 1 for _, pairs in curves))
check("property: values from the dialog applied",
      all((s.scale, round(s.strength, 3), s.loop_start, s.loop_end) == (8.0, 0.3, 1, 48)
          for _, pairs in curves for _, s in pairs))
check("property: each component has its own Phase", len({pairs[0][1].phase for _, pairs in curves}) == 3)
check("property: one key at the current frame and value",
      all(len(fc.keyframe_points) == 1 and fc.keyframe_points[0].co.x == 5.0
          and abs(fc.keyframe_points[0].co.y - (1.0, 2.0, 3.0)[i]) < 1e-6 for i, (fc, _) in enumerate(curves)))
diff = max(abs(curves[2][0].evaluate(t) - curves[2][0].evaluate(t + 48)) for t in frange(-40, 150, 0.5))
check("property: loops every 48 frames", diff == 0.0, diff)

pt2 = bpy.data.objects.new("PropTarget2", None)
scene.collection.objects.link(pt2)
add_to_property(pt2, "location", 3, scale=8.0, strength=0.3, loop_start=1, loop_end=48)
check("property: same seed gives the same phases (redo keeps the pattern)",
      [loop_noises(pt2, "location", i)[1][0][1].phase for i in range(3)]
      == [pairs[0][1].phase for _, pairs in curves])

one = bpy.data.objects.new("OneComponent", None)
scene.collection.objects.link(one)
add_to_property(one, "location", 3, index=1, all_components=False, loop_start=1, loop_end=48)
check("property: only the clicked component when All Components is off",
      [len(loop_noises(one, "location", i)[1]) for i in range(3)] == [0, 1, 0])

keyed = bpy.data.objects.new("AlreadyKeyed", None)
scene.collection.objects.link(keyed)
keyed.keyframe_insert("location", index=0, frame=1)
keyed.location.x = 4.0
keyed.keyframe_insert("location", index=0, frame=20)
add_to_property(keyed, "location", 3, index=0, all_components=False, loop_start=1, loop_end=48)
kfc, kpairs = loop_noises(keyed, "location", 0)
check("property: existing keys are kept, none added", len(kfc.keyframe_points) == 2 and len(kpairs) == 1)

bone_path = 'pose.bones["Spine"].rotation_euler'
rig.pose.bones["Spine"].rotation_mode = 'XYZ'
add_to_property(rig, bone_path, 3, loop_start=1, loop_end=60, strength=0.2)
check("property: bone rotation gets Loop Noise on X, Y, Z",
      all(len(loop_noises(rig, bone_path, i)[1]) == 1 for i in range(3)))

mesh_ob = bpy.data.objects.new("Face", bpy.data.meshes.new("FaceMesh"))
scene.collection.objects.link(mesh_ob)
mesh_ob.shape_key_add(name="Basis")
mesh_ob.shape_key_add(name="Smile")
key = mesh_ob.data.shape_keys
add_to_property(key, 'key_blocks["Smile"].value', 0, loop_start=1, loop_end=40, strength=0.2)
check("property: shape key value gets Loop Noise", len(loop_noises(key, 'key_blocks["Smile"].value', 0)[1]) == 1)

mat = bpy.data.materials.new("Shiny")
if mat.node_tree is None:
    mat.use_nodes = True
socket = mat.node_tree.nodes["Principled BSDF"].inputs["Roughness"]
check("property: material node tree found through its owner",
      operators._id_locator(mat.node_tree) == ('MATERIAL', "Shiny", "node_tree"))
sock_path = socket.path_from_id("default_value")
add_to_property(mat.node_tree, sock_path, 0, loop_start=1, loop_end=40, strength=0.1)
check("property: material node value gets Loop Noise", len(loop_noises(mat.node_tree, sock_path, 0)[1]) == 1)

driven = bpy.data.objects.new("Driven", None)
scene.collection.objects.link(driven)
driven.driver_add("location", 2)
add_to_property(driven, "location", 3, loop_start=1, loop_end=48)
check("property: driven component skipped, others get Loop Noise",
      [len(loop_noises(driven, "location", i)[1]) for i in range(3)] == [1, 1, 0])

obj_props = bpy.types.Object.bl_rna.properties
check("menu: shown for float properties",
      operators.button_target(pt_ob, obj_props["location"]) == (pt_ob, "location"))
check("menu: hidden for on/off and whole-number properties",
      operators.button_target(pt_ob, obj_props["hide_render"]) is None
      and operators.button_target(pt_ob, obj_props["pass_index"]) is None)
cam = bpy.data.cameras.new("Cam")
check("default Strength follows the property",
      operators._default_strength(obj_props["rotation_euler"], 3.0) == 0.2
      and operators._default_strength(obj_props["location"], 12.0) == 0.2
      and operators._default_strength(bpy.types.Camera.bl_rna.properties["lens"], cam.lens) == 10.0)
scene.frame_set(1)

# --- Hidden sampler -------------------------------------------------------------
sampler = bpy.data.actions.get(core.SAMPLER_NAME)
check("sampler action is hidden and unused", sampler is not None and sampler.name.startswith(".") and sampler.users == 0)

# --- Saved files work without the add-on --------------------------------------
probe_values = [v(t) for t in range(-20, 260, 3)]
path = os.path.join(tempfile.gettempdir(), "loop_noise_test.blend")
bpy.ops.wm.save_as_mainfile(filepath=path, check_existing=False)
loop_noise.unregister()
bpy.ops.wm.open_mainfile(filepath=path)
fc = fcurve_of(bpy.data.objects["Probe"], "location", 2)
reloaded = [fc.evaluate(t) for t in range(-20, 260, 3)]
check("reloaded without add-on: identical motion", reloaded == probe_values)
check("sampler action was not saved", bpy.data.actions.get(core.SAMPLER_NAME) is None)
loop_noise.register()
items = fc.id_data.loop_noise_items
check("settings survive save/reload", len(items) == 1 and items[0].modifier_name == "Loop Noise.001",
      [i.modifier_name for i in items])
check("reloaded settings still rebuild", core.rebuild(items[0], bpy.context.scene))
os.remove(path)

loop_noise.unregister()
print("\n{:d} FAILED".format(len(FAILED)) if FAILED else "\nALL PASSED")
sys.exit(1 if FAILED else 0)
