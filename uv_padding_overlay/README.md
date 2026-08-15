# UV Padding Overlay

UV Padding Overlay displays the texture-space gap around UV shells directly in
Blender's UV Editor. It is a visualization-only extension: it never modifies
the mesh or UV coordinates.

## Install

1. Open **Edit > Preferences > Get Extensions**.
2. Open the menu in the top-right corner and choose **Install from Disk**.
3. Select `uv_padding_overlay-1.5.2.zip`.
4. Enable **UV Padding Overlay** if Blender does not enable it automatically.

Blender 4.2 through 5.3 is supported.

Version 1.5.2 clips the thin border to the padding band so increasing **Thin
Width** grows only inward. Thin Width is capped at the current scene's Margin.

Choose **Highlighted** mode to reveal insufficient gaps in a separate color,
**Thin Highlighted** to show only the thin outer limit plus filled overlap,
**Stacked** mode to let overlapping outlines darken naturally, or **Solid**
mode to composite all outlines through one cached mask with no visible overlap.
Each color picker's alpha channel controls that color's opacity.
Concave joins are partitioned between adjacent strips, so a shell never
darkens itself at an inner corner in **Stacked** mode.

## Use

1. Enter mesh Edit Mode and open the UV Editor.
2. Open the sidebar with `N` and select the **Padding** tab.
   Right-click **Show Padding** to assign a hotkey or add the action to Quick
   Favorites. It is also available as **Show Padding** in operator search.
3. Set **Margin (px)** and **Resolution**. **Outline Width** displays the
   derived per-shell width (`Margin / 2`) in pixels.
   The arrow buttons step to adjacent powers of two; typing still accepts any
   value allowed by the field.
4. Set **Roundness** to the number of segments generated per 90-degree round
   corner. Semicircular fallback caps use twice that number.

The margin is interpreted as the total desired gap between two shells. Each
shell therefore receives half of it on its exterior. For example, an 8 px
margin at 2048 px produces a 0.00390625 UV gap and a 0.001953125 UV band around
each shell.

**Selected Only** displays a complete shell when any of its visible UV elements
is selected. **Highlighted** mode uses **Highlight Color** where bands overlap.
**Thin Highlighted** keeps those overlaps filled but reduces other padding to a
configurable screen-pixel outer border. **Stacked** lets insufficient gaps
appear darker, while
**Solid** renders one combined mask with constant opacity.
The thin border grows inward from the padding limit and **Thin Width** cannot
exceed **Margin**.

## Notes

- Resolution is a single square dimension and does not depend on the active image.
- UDIM coordinates and UVs outside the 0–1 tile are supported.
- **Margin** and **Resolution** are stored per scene in the `.blend` file.
- Show Padding, Selected Only, Mode, Thin Width, Roundness, Color, and Highlight
  Color are global extension preferences shared by every scene and `.blend`
  file.
- The overlay is available in mesh Edit Mode only.
- Blender 5.3 alpha does not expose independent, non-synchronized UV selection
  through BMesh. In that provisional API configuration, **Selected Only** falls
  back to the selected mesh faces; synchronized selection remains exact.
