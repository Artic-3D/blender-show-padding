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
        from . import overlay

        layout = self.layout
        settings = context.scene.uv_padding_overlay
        layout.prop(settings, "enabled")

        body = layout.column()
        body.active = settings.enabled
        body.prop(settings, "margin_px", text="Margin (px)")
        outline_row = body.row()
        outline_row.enabled = False
        outline_row.prop(
            settings,
            "outline_width_px",
            text="Outline Width (px)",
        )
        body.prop(settings, "texture_resolution", text="Resolution")
        body.prop(settings, "corner_segments", text="Roundness")
        body.prop(settings, "selected_only")
        body.prop(settings, "render_mode", text="Mode")
        body.prop(settings, "color")

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
