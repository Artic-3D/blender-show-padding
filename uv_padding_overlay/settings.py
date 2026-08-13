"""Persistent scene settings for UV Padding Overlay."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
)
from bpy.types import PropertyGroup


def _geometry_update(_settings, _context):
    from . import overlay

    overlay.invalidate_geometry()


def _style_update(_settings, _context):
    from . import overlay

    overlay.tag_uv_editors_for_redraw()


def _outline_width_px(settings):
    return max(0.0, float(settings.margin_px)) * 0.5


class UVPADDING_PG_settings(PropertyGroup):
    enabled: BoolProperty(
        name="Show Padding",
        description="Display padding around visible UV shells",
        default=True,
        update=_geometry_update,
    )
    margin_px: FloatProperty(
        name="Margin",
        description="Desired total gap between adjacent UV shells, in texture pixels",
        default=8.0,
        min=0.0,
        soft_max=128.0,
        precision=2,
        update=_geometry_update,
    )
    texture_resolution: IntProperty(
        name="Resolution",
        description="Square texture resolution used to convert pixels to UV units",
        default=2048,
        min=1,
        soft_min=64,
        soft_max=16384,
        update=_geometry_update,
    )
    outline_width_px: FloatProperty(
        name="Outline Width",
        description="Per-shell outline width in pixels (half the total margin)",
        min=0.0,
        precision=2,
        get=_outline_width_px,
        options={"SKIP_SAVE"},
    )
    corner_segments: IntProperty(
        name="Roundness",
        description="Segments generated per 90-degree rounded corner",
        default=2,
        min=1,
        max=64,
        soft_max=16,
        update=_geometry_update,
    )
    selected_only: BoolProperty(
        name="Selected Only",
        description="Show complete UV shells touched by the current UV selection",
        default=False,
        update=_geometry_update,
    )
    render_mode: EnumProperty(
        name="Mode",
        description="Choose how overlapping outline geometry is composited",
        items=(
            (
                "LAYERED",
                "Layered",
                "Draw every outline separately so overlaps become darker",
            ),
            (
                "UNIFIED",
                "Solid",
                "Composite all outlines as one mask with no visible overlap",
            ),
        ),
        default="LAYERED",
        update=_style_update,
    )
    color: FloatVectorProperty(
        name="Color",
        description="Overlay color and opacity",
        subtype="COLOR",
        size=4,
        min=0.0,
        max=1.0,
        default=(1.0, 0.05, 0.35, 0.5),
        update=_style_update,
    )


_CLASSES = (UVPADDING_PG_settings,)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.uv_padding_overlay = bpy.props.PointerProperty(
        type=UVPADDING_PG_settings
    )


def unregister():
    if hasattr(bpy.types.Scene, "uv_padding_overlay"):
        del bpy.types.Scene.uv_padding_overlay
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
