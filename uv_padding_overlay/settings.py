"""Scene-local and global settings for UV Padding Overlay."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    FloatVectorProperty,
    IntProperty,
)
from bpy.types import AddonPreferences, PropertyGroup


ADDON_ID = __package__
_GLOBAL_SETTING_NAMES = (
    "enabled",
    "corner_segments",
    "selected_only",
    "render_mode",
    "color",
)


def _geometry_update(_settings, _context):
    from . import overlay

    overlay.invalidate_geometry()


def _style_update(_settings, _context):
    from . import overlay

    overlay.request_style_redraw()


def _outline_width_px(settings):
    return max(0.0, float(settings.margin_px)) * 0.5


class UVPADDING_PG_scene_settings(PropertyGroup):
    """Values whose meaning belongs to the current texture/scene."""

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


class UVPADDING_AP_preferences(AddonPreferences):
    """Display preferences shared by every scene and blend file."""

    bl_idname = ADDON_ID

    storage_version: IntProperty(
        default=0,
        options={"HIDDEN"},
    )

    enabled: BoolProperty(
        name="Show Padding",
        description="Display padding around visible UV shells",
        default=True,
        update=_geometry_update,
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
                "Stacked",
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
        default=(1.0, 0.05, 0.35, 0.25),
        update=_style_update,
    )

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "enabled")
        column = layout.column()
        column.active = self.enabled
        column.prop(self, "corner_segments")
        column.prop(self, "selected_only")
        column.prop(self, "render_mode")
        column.prop(self, "color")


_CLASSES = (
    UVPADDING_PG_scene_settings,
    UVPADDING_AP_preferences,
)


def get_preferences(context=None):
    """Return this extension's global preferences, if they are available."""

    if context is None:
        context = bpy.context
    preferences = getattr(context, "preferences", None)
    if preferences is None:
        preferences = getattr(bpy.context, "preferences", None)
    if preferences is None:
        return None
    addon = preferences.addons.get(ADDON_ID)
    return addon.preferences if addon is not None else None


def migrate_legacy_scene_settings(context=None):
    """Move v1.2 scene-bound display values to global preferences once."""

    global_settings = get_preferences(context)
    if global_settings is None or global_settings.storage_version >= 1:
        return False
    context = context or bpy.context
    active_scene = getattr(context, "scene", None)
    try:
        available_scenes = tuple(bpy.data.scenes)
    except AttributeError:
        # Blender exposes _RestrictData during extension registration. Retry
        # on the first timer tick, once normal context/data access is restored.
        return False
    scenes = []
    if active_scene is not None:
        scenes.append(active_scene)
    scenes.extend(scene for scene in available_scenes if scene != active_scene)
    migrated = False
    for scene in scenes:
        legacy = getattr(scene, "uv_padding_overlay", None)
        if legacy is None:
            continue
        names = [name for name in _GLOBAL_SETTING_NAMES if name in legacy]
        if not names:
            continue
        for name in names:
            value = legacy[name]
            if name == "color":
                value = tuple(value)
            setattr(global_settings, name, value)
        migrated = True
        break
    # Remove obsolete values from every currently loaded scene. Files opened
    # later may still contain dormant v1.2 ID properties, but they are never
    # read after this global migration marker has been stored.
    for scene in scenes:
        legacy = getattr(scene, "uv_padding_overlay", None)
        if legacy is None:
            continue
        for name in _GLOBAL_SETTING_NAMES:
            if name in legacy:
                del legacy[name]
    global_settings.storage_version = 1
    return migrated


def _deferred_migration():
    global_settings = get_preferences()
    if global_settings is None:
        return 0.1
    if global_settings.storage_version >= 1:
        return None
    migrate_legacy_scene_settings()
    return None if global_settings.storage_version >= 1 else 0.1


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.uv_padding_overlay = bpy.props.PointerProperty(
        type=UVPADDING_PG_scene_settings
    )
    if not bpy.app.timers.is_registered(_deferred_migration):
        bpy.app.timers.register(_deferred_migration, first_interval=0.0)


def unregister():
    if bpy.app.timers.is_registered(_deferred_migration):
        bpy.app.timers.unregister(_deferred_migration)
    if hasattr(bpy.types.Scene, "uv_padding_overlay"):
        del bpy.types.Scene.uv_padding_overlay
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
