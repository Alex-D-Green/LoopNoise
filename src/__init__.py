# SPDX-FileCopyrightText: 2026 Alex D Green
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Loop Noise: Noise for F-Curves that loops seamlessly over a frame range."""

if "bpy" in locals():
    import importlib
    for _module in (core, props, operators, ui):  # noqa: F821 (defined on first import)
        importlib.reload(_module)
    del _module
else:
    from . import core, props, operators, ui

import bpy
from bpy.app.handlers import persistent

_classes = (
    props.LoopNoiseSettings,
    operators.GRAPH_OT_loop_noise_add,
    operators.GRAPH_OT_loop_noise_remove,
    operators.GRAPH_OT_loop_noise_copy_to_selected,
    operators.GRAPH_OT_loop_noise_match_cycle,
    operators.ANIM_OT_loop_noise_add_to_property,
    ui.GRAPH_PT_loop_noise,
)

# Repeating Loop Noise covers the scene range, so follow changes to it.
_SCENE_RANGE_PROPS = ("frame_start", "frame_end", "use_preview_range", "frame_preview_start", "frame_preview_end")
_msgbus_owner = object()


def _on_scene_range_changed():
    core.rebuild_all(bpy.context.scene, repeating_only=True)


def _subscribe():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    for prop in _SCENE_RANGE_PROPS:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.Scene, prop),
            owner=_msgbus_owner,
            args=(),
            notify=_on_scene_range_changed,
        )


@persistent
def _on_load_post(*_args):
    # Loading a file drops all message bus subscriptions.
    _subscribe()


def register():
    for cls in _classes:
        bpy.utils.register_class(cls)
    bpy.types.Action.loop_noise_items = bpy.props.CollectionProperty(type=props.LoopNoiseSettings)
    bpy.types.GRAPH_MT_channel.append(ui.draw_channel_menu)
    bpy.types.UI_MT_button_context_menu.append(ui.draw_button_context_menu)
    bpy.app.handlers.load_post.append(_on_load_post)
    _subscribe()


def unregister():
    bpy.msgbus.clear_by_owner(_msgbus_owner)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    bpy.types.UI_MT_button_context_menu.remove(ui.draw_button_context_menu)
    bpy.types.GRAPH_MT_channel.remove(ui.draw_channel_menu)
    del bpy.types.Action.loop_noise_items
    for cls in reversed(_classes):
        bpy.utils.unregister_class(cls)
