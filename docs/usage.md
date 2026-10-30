# Using the dashboard

This page describes how to use the dashboard once it is running. For install,
data layout, and deployment, see the [README](../README.md).

## Choosing data

Pick a dataset folder, then a CSV file, in the global options. **Refresh**
reloads the file list. **More** holds subsampling or averaging, opacity, marker
size, font size, and the point marker. Subsampling and averaging apply to every
plot.

## Plots

Each plot has a title row with its main variables, a **⬇ Export** button, and a
**⚙ More** menu for ranges, colormaps, labels, and sizes.

| Plot | What it shows |
| --- | --- |
| Track | Cruise track on a map or on longitude and latitude axes. Selecting points here filters every other plot. |
| Transect | Depth against the chosen variable, colored by a third variable. |
| T-S | Temperature against salinity, colored by a variable. |
| Profile | Vertical profiles of the casts in the current selection. |
| Scatter | Any two numeric variables, with an optional linear, 2nd- or 3rd-order fit. |

### Sizes and labels

- **Plot W × H (px)** sets the size of the plot including its axis text. A
  color bar or legend is added outside that size, so changing the color bar
  position does not change the plot.
- **X, Y, and Color label** fields replace the automatic axis and color bar
  titles. Leave them empty to use the automatic label.
- **Color bar position** is bottom or right on the colored plots.
- **Color min and max** fix the color range. Leave them empty for an automatic
  range.
- Latitude and longitude limits accept negative values for south and west, for
  example `-10` for 10°S or 10°W.

### Linked selection

A box or lasso on the transect, T-S, or scatter plot highlights the same points
on the others. The latest selection wins. The track plot filters all plots. The
profile plot shows the casts of the selection, and its legend can be switched
off, for example before exporting.

### Details

Click a point to see its values and camera frames when `media` and `frame`
columns exist. Prev and Next step through points in time. The point is ringed on
the linked plots and its cast is drawn bold on the profile plot. Drag the
divider to resize the panel.

### Sharing a view

The URL stores the dashboard settings, so a copied link or the browser's Back
button reproduces a view.

## Exporting figures

Click **⬇ Export** on a plot. The dialog makes a copy of the plot as it looks on
screen, scaled to the width you choose, so text, markers, lines, and the color
bar all scale together.

| Setting | Meaning |
| --- | --- |
| Format | PNG, SVG, or TIFF. PNG and TIFF store the resolution; SVG is vector. |
| Width (in) | Width of the file in inches. The height follows the plot's shape. |
| Resolution (dpi) | For PNG and TIFF. |
| Background | White or transparent. |

**Download settings** saves a text file recording the dataset, file, plot
settings, and export options, so a figure can be reproduced.

Large plots with very many points keep their markers as raster (WebGL) in the
export. A larger bin size, or averaging, gives sharp vector markers.

### Several panels

For the transect, T-S, profile, and scatter plots, the dialog lists panels. Each
row is one panel and can set its own X, Y, color variable, colormap, color
minimum and maximum, and (under **Labels**) its own axis and color bar text.
Everything else follows the plot as shown, and each panel has its own color bar.

- The first panel starts with the plot's current choices. **+ Add panel** adds a
  copy of the previous one, without its label text.
- A blank color range or label means automatic for that panel's variable.
- With **Combine panels into one figure** off, each panel is saved as its own
  file, named `<plot>_panel1`, `<plot>_panel2`, and so on. Your browser may ask
  to allow several downloads.
- With it on, the panels are laid out in one figure. **Rows × columns** sets the
  grid (empty means automatic) and **Panel letters** adds (a), (b), … or
  (A), (B), … in a corner of each panel.

### Fitting a page

With a combined figure, choose a page size under **Fit to page** or type your
own width and height in inches, plus a margin. The file is then the whole page,
with the grid scaled to fit inside the margin and centered. Width is ignored in
this mode. Clear the page size to use Width again.

For example, six panels on a letter page: set Rows × columns to 3 × 2, choose
Letter portrait, and keep the 0.5 in margin. The status line reports the page
size and the resulting text size in points. If the text is too small to read,
use fewer or smaller panels, or a larger plot font size, which makes the text
bigger relative to each plot.
