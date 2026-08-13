"""UV Editor sidebar panel."""

import bpy
from bpy.types import Panel


class UVPADDING_PT_overlay(Panel):
    bl_label = "Padding Overlay"
    bl_idname = "UVPADDING_PT_overlay"
    bl_space_type = "IMAGE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Padding"

    @classmethod
    def poll(cls, context):
        area = context.area
        if area is None or area.type != "IMAGE_EDITOR":
            return False
        space = context.space_data
        return area.ui_type == "UV" or getattr(space, "ui_mode", "") == "UV"

    def draw(self, context):
        from . import overlay, settings as settings_module

        layout = self.layout
        scene_settings = context.scene.uv_padding_overlay
        global_settings = settings_module.get_preferences(context)
        if global_settings is None:
            layout.label(text="Global preferences unavailable", icon="INFO")
            return
        layout.prop(global_settings, "enabled")

        body = layout.column()
        body.active = global_settings.enabled
        body.prop(scene_settings, "margin_px", text="Margin (px)")
        body.label(
            text=(
                "Outline Width: "
                f"{scene_settings.outline_width_px:.2f} px"
            )
        )
        body.prop(scene_settings, "texture_resolution", text="Resolution")
        body.separator()
        body.label(text="Settings")
        body.prop(global_settings, "selected_only")
        body.prop(global_settings, "render_mode", text="Mode")
        body.prop(global_settings, "corner_segments", text="Roundness")
        body.prop(global_settings, "color")

        status, icon = overlay.context_status(context)
        if status is not None:
            body.label(text=status, icon=icon)


_CLASSES = (UVPADDING_PT_overlay,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
