# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = (
    "GRAPH_PT_loop_noise",
    "draw_button_context_menu",
    "draw_channel_menu",
)

import bpy

from . import core
from .operators import (
    ANIM_OT_loop_noise_add_to_property,
    GRAPH_OT_loop_noise_add,
    GRAPH_OT_loop_noise_copy_to_selected,
    GRAPH_OT_loop_noise_match_cycle,
    GRAPH_OT_loop_noise_remove,
    button_target,
)

# Same order as the native Noise modifier panel (minus Legacy Noise).
_NOISE_FIELDS = ("scale", "strength", "offset", "phase", "depth", "lacunarity", "roughness")


class GRAPH_PT_loop_noise(bpy.types.Panel):
    bl_space_type = 'GRAPH_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Modifiers"
    bl_label = "Loop Noise"

    @classmethod
    def poll(cls, context):
        space = context.space_data
        fcurve = context.active_editable_fcurve
        return space.mode == 'FCURVES' and fcurve is not None and core.is_action_fcurve(fcurve)

    def draw(self, context):
        layout = self.layout
        fcurve = context.active_editable_fcurve
        layout.operator(GRAPH_OT_loop_noise_add.bl_idname, icon='ADD').only_active = True

        for carrier, settings in core.carriers_with_settings(fcurve):
            header, body = layout.panel_prop(settings, "show_expanded")
            _draw_header(header, carrier)
            if body is not None:
                _draw_body(body, fcurve, carrier, settings)


def _draw_header(layout, carrier):
    row = layout.row(align=True)
    row.label(text=carrier.name, icon='FORCE_TURBULENCE')
    # Mute has a checkbox icon in RNA; unembossed it matches the native modifier headers.
    row.prop(carrier, "mute", text="", emboss=False)
    row.operator(GRAPH_OT_loop_noise_remove.bl_idname, text="", icon='X', emboss=False).modifier_name = carrier.name


def _draw_body(layout, fcurve, carrier, settings):
    layout.use_property_split = True
    layout.use_property_decorate = False
    layout.active = not carrier.mute

    layout.prop(settings, "blend_type")
    col = layout.column()
    for prop in _NOISE_FIELDS:
        col.prop(settings, prop)

    layout.separator()
    col = layout.column(align=True)
    col.prop(settings, "loop_start", text="Loop Start")
    col.prop(settings, "loop_end", text="End")
    col = layout.column()
    col.prop(settings, "repeat")
    col.prop(settings, "samples_per_frame", text="Samples")
    _draw_loop_warnings(layout, fcurve, carrier, settings)

    layout.separator()
    _draw_sine_blend(layout, carrier, settings)

    # The native options of the carrier modifier, drawn like on native panels.
    header, body = layout.panel_prop(settings, "show_restrict")
    header.use_property_split = False
    header.prop(carrier, "use_restricted_range", text="Restrict Frame Range")
    if body is not None:
        body.active = carrier.use_restricted_range and not carrier.mute
        col = body.column(align=True)
        col.prop(carrier, "frame_start", text="Start")
        col.prop(carrier, "frame_end", text="End")
        col = body.column(align=True)
        col.prop(carrier, "blend_in", text="Blend In")
        col.prop(carrier, "blend_out", text="Out")

    layout.separator()
    row = layout.row(heading="Influence", align=True)
    row.prop(carrier, "use_influence", text="")
    sub = row.row(align=True)
    sub.active = carrier.use_influence
    sub.prop(carrier, "influence", text="")

    layout.separator()
    layout.operator(
        GRAPH_OT_loop_noise_copy_to_selected.bl_idname, icon='PASTEDOWN',
    ).modifier_name = carrier.name


def _draw_sine_blend(layout, carrier, settings):
    header, body = layout.panel_prop(settings, "show_sine")
    row = header.row()
    row.label(text="Sine Blend")
    if settings.randomness < 1.0:
        # Visible while collapsed, so an active blend isn't forgotten.
        hint = row.row()
        hint.alignment = 'RIGHT'
        hint.label(text="Randomness {:.2f}".format(settings.randomness))
    if body is None:
        return
    body.active = not carrier.mute
    body.prop(settings, "randomness", slider=True)
    col = body.column()
    col.active = settings.randomness < 1.0
    col.prop(settings, "cycles")
    if settings.cycles == 0:
        period = settings.loop_end - settings.loop_start + 1
        if period >= 2:
            count = period * settings.samples_per_frame
            split = col.split(factor=0.4)
            split.label()
            split.label(text="Auto: {:d} per loop".format(core.sine_cycles(settings, period, count)))


def _draw_loop_warnings(layout, fcurve, carrier, settings):
    period = settings.loop_end - settings.loop_start + 1
    if period < 2:
        layout.label(text="End must be after Loop Start", icon='ERROR')
        return
    cycle = core.cycles_period(fcurve)
    if cycle is not None and abs(cycle - period) > 1e-3:
        col = layout.column(align=True)
        col.label(text="Cycles repeats {:g} frames, loop is {:d}".format(cycle, period), icon='ERROR')
        col.operator(GRAPH_OT_loop_noise_match_cycle.bl_idname).modifier_name = carrier.name


def draw_channel_menu(self, context):
    layout = self.layout
    layout.separator()
    layout.operator(GRAPH_OT_loop_noise_add.bl_idname).only_active = False


def draw_button_context_menu(self, context):
    """Right-click menu of any property field: shown for animatable float properties."""
    if button_target(getattr(context, "button_pointer", None), getattr(context, "button_prop", None)) is None:
        return
    layout = self.layout
    layout.separator()
    layout.operator(ANIM_OT_loop_noise_add_to_property.bl_idname, icon='FORCE_TURBULENCE')
