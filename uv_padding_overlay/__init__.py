"""UV Padding Overlay Blender extension."""

import bpy


# Blender's extension installer may reload only this package module after an
# in-place update.  Explicitly retire and reload every implementation module;
# otherwise Python can keep executing the previous overlay.py even though the
# new manifest and files are already on disk.
if "overlay" in locals():
    import importlib

    if (
        getattr(overlay, "_REGISTERED", False)
        or hasattr(bpy.types.Scene, "uv_padding_overlay")
    ):
        unregister()
    importlib.reload(geometry)
    if "emptiness" in locals():
        importlib.reload(emptiness)
    else:
        from . import emptiness
    importlib.reload(overlay)
    importlib.reload(settings)
    importlib.reload(ui)
else:
    from . import emptiness, geometry, overlay, settings, ui


def register():
    settings.register()
    emptiness.register()
    ui.register()
    overlay.register()


def unregister():
    overlay.unregister()
    ui.unregister()
    emptiness.unregister()
    settings.unregister()
