# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "ANIM_OT_loop_noise_add_to_property",
    "GRAPH_OT_loop_noise_add",
    "GRAPH_OT_loop_noise_copy_to_selected",
    "GRAPH_OT_loop_noise_match_cycle",
    "GRAPH_OT_loop_noise_remove",
    "button_target",
)

import random

import bpy
from bpy.props import BoolProperty, FloatProperty, IntProperty, StringProperty
from bpy_extras import anim_utils

from . import core


def _in_fcurve_graph(context):
    space = context.space_data
    return space is not None and space.type == 'GRAPH_EDITOR' and space.mode == 'FCURVES'


def _active_action_fcurve(context):
    fcurve = context.active_editable_fcurve
    if fcurve is None or not core.is_action_fcurve(fcurve):
        return None
    return fcurve


def _find_pair(fcurve, modifier_name):
    return next(((mod, settings) for mod, settings in core.carriers_with_settings(fcurve)
                 if mod.name == modifier_name), (None, None))


class GRAPH_OT_loop_noise_add(bpy.types.Operator):
    bl_idname = "graph.loop_noise_add"
    bl_label = "Add Loop Noise"
    bl_description = "Add noise that loops seamlessly over a frame range"
    bl_options = {'REGISTER', 'UNDO'}

    only_active: BoolProperty(
        name="Only Active",
        description="Only add Loop Noise to the active F-Curve, otherwise to all selected ones",
        default=False,
        options={'SKIP_SAVE'},
    )

    @classmethod
    def poll(cls, context):
        if not _in_fcurve_graph(context):
            cls.poll_message_set("Only available for animation F-Curves in the Graph Editor")
            return False
        if context.active_editable_fcurve is None and not context.selected_editable_fcurves:
            cls.poll_message_set("No editable F-Curves selected")
            return False
        return True

    def execute(self, context):
        if self.only_active:
            fcurves = [context.active_editable_fcurve]
        else:
            fcurves = list(context.selected_editable_fcurves)
        fcurves = [fcurve for fcurve in fcurves if fcurve is not None and core.is_action_fcurve(fcurve)]
        if not fcurves:
            self.report({'WARNING'}, "No editable animation F-Curves")
            return {'CANCELLED'}
        # A different Phase (noise seed) per curve, so X, Y and Z don't move in unison.
        rng = random.Random()
        for fcurve in fcurves:
            core.add_loop_noise(fcurve, context.scene, phase=round(rng.uniform(0.0, 1000.0), 3))
        return {'FINISHED'}


class GRAPH_OT_loop_noise_remove(bpy.types.Operator):
    bl_idname = "graph.loop_noise_remove"
    bl_label = "Remove Loop Noise"
    bl_description = "Remove this Loop Noise from the active F-Curve"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    modifier_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _in_fcurve_graph(context) and _active_action_fcurve(context) is not None

    def execute(self, context):
        fcurve = _active_action_fcurve(context)
        carrier, settings = _find_pair(fcurve, self.modifier_name)
        if carrier is None:
            return {'CANCELLED'}
        core.remove_loop_noise(fcurve, carrier, settings)
        return {'FINISHED'}


class GRAPH_OT_loop_noise_copy_to_selected(bpy.types.Operator):
    bl_idname = "graph.loop_noise_copy_to_selected"
    bl_label = "Copy to Selected"
    bl_description = (
        "Give the other selected F-Curves this Loop Noise's settings. "
        "Curves without a Loop Noise get one, others have their first one updated"
    )
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    modifier_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    keep_phase: BoolProperty(
        name="Keep Phase",
        description="Keep each curve's own Phase (new Loop Noises get a random one), "
        "so the curves don't move in unison",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if not _in_fcurve_graph(context) or _active_action_fcurve(context) is None:
            return False
        if len(context.selected_editable_fcurves) < 2:
            cls.poll_message_set("Select the F-Curves to copy to as well")
            return False
        return True

    def execute(self, context):
        source = _active_action_fcurve(context)
        carrier, settings = _find_pair(source, self.modifier_name)
        if carrier is None:
            return {'CANCELLED'}
        snap = core.snapshot(carrier, settings)
        targets = [fcurve for fcurve in context.selected_editable_fcurves
                   if fcurve != source and core.is_action_fcurve(fcurve)]
        if not targets:
            self.report({'WARNING'}, "Select the F-Curves to copy to as well")
            return {'CANCELLED'}
        rng = random.Random()
        for fcurve in targets:
            phase = None
            if self.keep_phase:
                pairs = core.carriers_with_settings(fcurve)
                phase = pairs[0][1].phase if pairs else round(rng.uniform(0.0, 1000.0), 3)
            core.apply_snapshot(snap, fcurve, context.scene, phase=phase)
        self.report({'INFO'}, "Copied Loop Noise to {:d} F-Curve(s)".format(len(targets)))
        return {'FINISHED'}


# -----------------------------------------------------------------------------
# Right-click on any property


def button_target(pointer, prop):
    """(id_data, data_path) for a property button that can take Loop Noise, else None."""
    if pointer is None or prop is None:
        return None
    if prop.type != 'FLOAT' or prop.is_readonly or not prop.is_animatable:
        return None
    id_data = pointer.id_data
    if not isinstance(id_data, bpy.types.ID) or id_data.library is not None:
        return None
    try:
        data_path = pointer.path_from_id(prop.identifier)
    except ValueError:
        return None
    return id_data, data_path


# Data-blocks that embed a node tree (it has no entry in bpy.data of its own).
_NODE_TREE_OWNERS = ("materials", "worlds", "lights", "textures", "linestyles", "scenes")


def _id_locator(id_data):
    """(id_type, name, attribute) to find id_data again after undo, e.g. when redoing."""
    if not id_data.is_embedded_data:
        return id_data.id_type, id_data.name, ""
    for attr in _NODE_TREE_OWNERS:
        for owner in getattr(bpy.data, attr, ()):
            if owner.library is None and getattr(owner, "node_tree", None) == id_data:
                return owner.id_type, owner.name, "node_tree"
    return None


def _find_id(id_type, name, attribute):
    for prop in bpy.data.bl_rna.properties:
        if prop.type != 'COLLECTION':
            continue
        try:
            id_data = getattr(bpy.data, prop.identifier).get((name, None))
        except (TypeError, KeyError, AttributeError):
            continue
        if id_data is not None and id_data.id_type == id_type:
            return getattr(id_data, attribute, None) if attribute else id_data
    return None


def _action_fcurve(id_data, data_path, index):
    anim = id_data.animation_data
    if anim is None or anim.action is None:
        return None
    channelbag = anim_utils.action_get_channelbag_for_slot(anim.action, anim.action_slot)
    return channelbag.fcurves.find(data_path, index=index) if channelbag else None


def _ensure_fcurve(id_data, data_path, index, keyed_index, frame):
    """The property's animation F-Curve, keyed once at its current value if it has none.

    Returns (fcurve, reason) where fcurve is None if it can't take Loop Noise.
    """
    anim = id_data.animation_data
    if anim is not None and anim.drivers.find(data_path, index=index) is not None:
        return None, "driven"
    fcurve = _action_fcurve(id_data, data_path, index)
    if fcurve is None:
        try:
            id_data.keyframe_insert(data_path, index=keyed_index, frame=frame)
        except (TypeError, RuntimeError):
            return None, "not keyable"
        fcurve = _action_fcurve(id_data, data_path, index)
    if fcurve is None or not core.is_action_fcurve(fcurve):
        return None, "not keyable"
    return fcurve, ""


def _default_strength(prop, value):
    # Strength is in the property's units: radians and meters want small values,
    # large-valued properties (focal length, light power...) a share of their value.
    if prop.unit in {'ROTATION', 'LENGTH'}:
        return 0.2
    return round(0.2 * max(1.0, abs(value)), 3)


class ANIM_OT_loop_noise_add_to_property(bpy.types.Operator):
    bl_idname = "anim.loop_noise_add_to_property"
    bl_label = "Add Loop Noise"
    bl_description = (
        "Add noise that loops seamlessly over a frame range to this property. "
        "Creates its F-Curve with one key if it isn't animated yet"
    )
    bl_options = {'REGISTER', 'UNDO'}

    # The right-clicked property, stored so the operation can be redone (F9).
    id_type: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    id_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    id_attribute: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    data_path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    array_length: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    # Fixed per invocation so redoing keeps the same noise pattern.
    seed: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    scale: FloatProperty(
        name="Scale", description="Scaling (in time) of the noise",
        default=10.0, min=0.001, soft_max=100.0, step=10, precision=3, options={'SKIP_SAVE'},
    )
    strength: FloatProperty(
        name="Strength", description="Amplitude of the noise, in the property's units",
        default=0.2, soft_min=0.0, soft_max=10.0, step=10, precision=3, options={'SKIP_SAVE'},
    )
    loop_start: IntProperty(name="Loop Start", description="First frame of the loop", options={'SKIP_SAVE'})
    loop_end: IntProperty(
        name="End", description="Last frame of the loop. The frame after it repeats Loop Start exactly",
        options={'SKIP_SAVE'},
    )
    all_components: BoolProperty(
        name="All Components",
        description="Add Loop Noise to every component (e.g. X, Y and Z), each with its own Phase",
        default=True, options={'SKIP_SAVE'},
    )

    def invoke(self, context, event):
        target = button_target(getattr(context, "button_pointer", None), getattr(context, "button_prop", None))
        locator = _id_locator(target[0]) if target else None
        if locator is None:
            self.report({'WARNING'}, "This property can't take Loop Noise")
            return {'CANCELLED'}
        id_data, data_path = target
        prop = context.button_prop
        self.id_type, self.id_name, self.id_attribute = locator
        self.data_path = data_path
        self.array_length = prop.array_length
        hovered = getattr(context, "property", None)
        self.index = hovered[2] if hovered and len(hovered) == 3 and hovered[2] is not None else -1
        self.seed = random.randrange(1 << 30)

        value = id_data.path_resolve(data_path)
        if self.array_length:
            value = max((abs(v) for v in value), default=0.0)
        self.strength = _default_strength(prop, value)
        existing = _action_fcurve(id_data, data_path, max(self.index, 0))
        if existing is not None:
            self.loop_start, self.loop_end = core.default_range(existing, context.scene)
        else:
            self.loop_start, self.loop_end = core.scene_range(context.scene)
        return context.window_manager.invoke_props_dialog(self, title="Add Loop Noise", confirm_text="Add")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()
        col.prop(self, "scale")
        col.prop(self, "strength")
        col = layout.column(align=True)
        col.prop(self, "loop_start")
        col.prop(self, "loop_end")
        if self.array_length > 1:
            layout.prop(self, "all_components")

    def execute(self, context):
        id_data = _find_id(self.id_type, self.id_name, self.id_attribute)
        if id_data is None or not self.data_path:
            self.report({'WARNING'}, "Property not found")
            return {'CANCELLED'}
        scene = context.scene
        if self.array_length == 0:
            components = [(0, -1)]  # (F-Curve index, keyframe_insert index)
        elif self.all_components or self.index < 0:
            components = [(i, i) for i in range(self.array_length)]
        else:
            components = [(self.index, self.index)]

        added, problems = 0, set()
        for fcurve_index, keyed_index in components:
            fcurve, reason = _ensure_fcurve(id_data, self.data_path, fcurve_index, keyed_index, scene.frame_current)
            if fcurve is None:
                problems.add(reason)
                continue
            phase = round(random.Random(self.seed * 64 + fcurve_index).uniform(0.0, 1000.0), 3)
            settings = core.add_loop_noise(fcurve, scene, phase=phase)
            with core.suspend_updates():
                settings.scale = self.scale
                settings.strength = self.strength
                settings.loop_start = self.loop_start
                settings.loop_end = self.loop_end
            core.rebuild(settings, scene)
            added += 1

        if "driven" in problems:
            self.report({'WARNING'}, "Skipped driven components: a driver overrides animation")
        elif problems:
            self.report({'WARNING'}, "Some components can't be keyframed")
        return {'FINISHED'} if added else {'CANCELLED'}


class GRAPH_OT_loop_noise_match_cycle(bpy.types.Operator):
    bl_idname = "graph.loop_noise_match_cycle"
    bl_label = "Match Cycle"
    bl_description = "Set the loop range to the cycle the Cycles modifier repeats (first to last key)"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    modifier_name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _in_fcurve_graph(context) and _active_action_fcurve(context) is not None

    def execute(self, context):
        fcurve = _active_action_fcurve(context)
        _carrier, settings = _find_pair(fcurve, self.modifier_name)
        period = core.cycles_period(fcurve)
        if settings is None or period is None or round(period) < 2:
            return {'CANCELLED'}
        start = round(fcurve.keyframe_points[0].co.x)
        with core.suspend_updates():
            settings.loop_start = start
            settings.loop_end = start + round(period) - 1
        core.rebuild(settings, context.scene)
        return {'FINISHED'}
