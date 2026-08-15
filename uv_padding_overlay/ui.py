"""UV Editor sidebar panel."""

import math

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Operator, Panel


def _adjacent_power_of_two(value, direction):
    """Return the neighboring power of two, with 2 as the UI-step floor."""

    value = max(0.0, float(value))
    if direction > 0:
        if value < 2.0:
            return 2.0
        exponent = math.floor(math.log2(value)) + 1
    else:
        if value <= 2.0:
            return 2.0
        exponent = math.ceil(math.log2(value)) - 1
    return float(2**exponent)


class UVPADDING_OT_step_power_of_two(Operator):
    bl_idname = "uv_padding_overlay.step_power_of_two"
    bl_label = "Step to Adjacent Power of Two"
    bl_description = "Set the value to the adjacent power of two"
    bl_options = {"INTERNAL", "UNDO"}

    property_name: StringProperty(options={"HIDDEN"})
    direction: IntProperty(default=1, min=-1, max=1, options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        scene = getattr(context, "scene", None)
        return scene is not None and hasattr(scene, "uv_padding_overlay")

    def execute(self, context):
        if self.property_name not in {"margin_px", "texture_resolution"}:
            return {"CANCELLED"}
        settings = context.scene.uv_padding_overlay
        current = getattr(settings, self.property_name)
        target = _adjacent_power_of_two(current, self.direction)
        if self.property_name == "texture_resolution":
            target = int(target)
        setattr(settings, self.property_name, target)
        return {"FINISHED"}


def _draw_power_of_two_field(layout, settings, property_name, text):
    row = layout.row(align=True)
    previous = row.operator(
        UVPADDING_OT_step_power_of_two.bl_idname,
        text="",
        icon="TRIA_LEFT",
    )
    previous.property_name = property_name
    previous.direction = -1
    row.prop(settings, property_name, text=text)
    following = row.operator(
        UVPADDING_OT_step_power_of_two.bl_idname,
        text="",
        icon="TRIA_RIGHT",
    )
    following.property_name = property_name
    following.direction = 1


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
        layout.prop(
            global_settings,
            "enabled",
            text="Show Padding",
            toggle=True,
        )

        body = layout.column()
        body.active = global_settings.enabled
        _draw_power_of_two_field(
            body,
            scene_settings,
            "margin_px",
            "Margin (px)",
        )
        _draw_power_of_two_field(
            body,
            scene_settings,
            "texture_resolution",
            "Resolution",
        )
        body.label(
            text=(
                "Outline Width: "
                f"{scene_settings.outline_width_px:.2f} px"
            )
        )
        body.separator()
        settings_header, settings_body = body.panel(
            "uv_padding_overlay_settings",
            default_closed=False,
        )
        settings_header.label(text="Settings")
        if settings_body is not None:
            settings_body.prop(global_settings, "color")
            settings_body.prop(
                global_settings,
                "corner_segments",
                text="Roundness",
            )
            settings_body.prop(global_settings, "render_mode", text="Mode")
            settings_body.prop(global_settings, "selected_only")

        status, icon = overlay.context_status(context)
        if status is not None:
            body.label(text=status, icon=icon)


_CLASSES = (
    UVPADDING_OT_step_power_of_two,
    UVPADDING_PT_overlay,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
