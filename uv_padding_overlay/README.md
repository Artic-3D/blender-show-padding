# UV Padding Overlay

UV Padding Overlay displays the texture-space gap around UV shells directly in
Blender's UV Editor. It is a visualization-only extension: it never modifies
the mesh or UV coordinates.

## Install

1. Open **Edit > Preferences > Get Extensions**.
2. Open the menu in the top-right corner and choose **Install from Disk**.
3. Select `uv_padding_overlay-1.3.1.zip`.
4. Enable **UV Padding Overlay** if Blender does not enable it automatically.

Blender 4.2 through 5.3 is supported.

Version 1.3.1 keeps edit-BMesh inspection out of viewport draw callbacks,
pauses snapshot reads while a modal transform owns the mesh, and cleans up
callbacks captured by an earlier extension reload. It also explicitly reloads
all implementation modules during an in-place package update, preventing a new
manifest from being paired with stale Python code. The last cached band remains
visible while dragging and refreshes shortly after the transform is released.

Choose **Layered** mode to let overlapping outlines darken naturally, or
**Solid** mode to composite all outlines through one cached mask with no
visible overlap. The color picker's alpha channel controls overlay opacity.
Concave joins are partitioned between adjacent strips, so a shell never
darkens itself at an inner corner in **Layered** mode.

## Use

1. Enter mesh Edit Mode and open the UV Editor.
2. Open the sidebar with `N` and select the **Padding** tab.
3. Set **Margin (px)** and **Resolution**. **Outline Width** displays the
   derived per-shell width (`Margin / 2`) in pixels.
4. Set **Roundness** to the number of segments generated per 90-degree round
   corner. Semicircular fallback caps use twice that number.

The margin is interpreted as the total desired gap between two shells. Each
shell therefore receives half of it on its exterior. For example, an 8 px
margin at 2048 px produces a 0.00390625 UV gap and a 0.001953125 UV band around
each shell.

**Selected Only** displays a complete shell when any of its visible UV elements
is selected. **Layered** mode lets insufficient gaps appear darker where bands
overlap. **Solid** mode renders one combined mask with constant opacity.
Opacity is controlled by the alpha channel in **Color**.

## Notes

- Resolution is a single square dimension and does not depend on the active image.
- UDIM coordinates and UVs outside the 0–1 tile are supported.
- **Margin** and **Resolution** are stored per scene in the `.blend` file.
- Show Padding, Roundness, Selected Only, Mode, and Color are global extension
  preferences shared by every scene and `.blend` file.
- The overlay is available in mesh Edit Mode only.
- Blender 5.3 alpha does not expose independent, non-synchronized UV selection
  through BMesh. In that provisional API configuration, **Selected Only** falls
  back to the selected mesh faces; synchronized selection remains exact.
