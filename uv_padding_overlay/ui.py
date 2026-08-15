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


class UVPADDING_OT_toggle_padding(Operator):
    bl_idname = "uv_padding_overlay.toggle_padding"
    bl_label = "Show Padding"
    bl_description = "Toggle the UV padding overlay"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        from . import settings as settings_module

        return settings_module.get_preferences(context) is not None

    def execute(self, context):
        from . import settings as settings_module

        global_settings = settings_module.get_preferences(context)
        if global_settings is None:
            return {"CANCELLED"}
        global_settings.enabled = not global_settings.enabled
        return {"FINISHED"}


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
        setattr(settings, self.property_name, int(target))
        return {"FINISHED"}


def _draw_power_of_two_field(layout, settings, property_name, text):
    row = layout.row(align=True)
    row.prop(settings, property_name, text=text)
    previous = row.operator(
        UVPADDING_OT_step_power_of_two.bl_idname,
        text="",
        icon="TRIA_LEFT",
    )
    previous.property_name = property_name
    previous.direction = -1
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
        from . import emptiness, overlay, settings as settings_module

        layout = self.layout
        scene_settings = context.scene.uv_padding_overlay
        global_settings = settings_module.get_preferences(context)
        if global_settings is None:
            layout.label(text="Global preferences unavailable", icon="INFO")
            return
        settings_module.clamp_thin_width_to_margin(
            context,
            global_settings,
        )
        layout.operator(
            UVPADDING_OT_toggle_padding.bl_idname,
            text="Show Padding",
            depress=global_settings.enabled,
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
        outline_row = body.row(align=True)
        outline_prefix = outline_row.row(align=True)
        outline_prefix.enabled = False
        outline_prefix.label(text="     Outline: ")
        outline_row.label(
            text=f"{scene_settings.outline_width_px:.2f} px"
        )
        body.separator()
        settings_header, settings_body = body.panel(
            "uv_padding_overlay_settings_v2",
            default_closed=True,
        )
        settings_header.label(text="Settings")
        if settings_body is not None:
            settings_body.prop(global_settings, "color")
            settings_body.prop(global_settings, "highlight_color")
            settings_body.prop(
                global_settings,
                "corner_segments",
                text="Roundness",
            )
            settings_body.prop(global_settings, "render_mode", text="Mode")
            if global_settings.render_mode == "THIN_HIGHLIGHTED":
                settings_body.prop(global_settings, "thin_width")
            settings_body.prop(global_settings, "selected_only")

        status, icon = overlay.context_status(context)
        if status is not None:
            body.label(text=status, icon=icon)

        layout.separator()
        experimental_header, experimental_body = layout.panel(
            "uv_padding_overlay_experimental_v2",
            default_closed=True,
        )
        experimental_header.label(text="Experimental")
        if experimental_body is not None:
            row = experimental_body.split(factor=0.88, align=True)
            row.operator(
                emptiness.UVPADDING_OT_calculate_emptiness.bl_idname,
                text="Calculate Emptiness",
            )
            row.operator(
                emptiness.UVPADDING_OT_clear_emptiness.bl_idname,
                text="",
                icon="X",
            )
            experimental_body.prop(
                global_settings,
                "emptiness_color",
                text="Color",
            )


_CLASSES = (
    UVPADDING_OT_toggle_padding,
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
