# Using the dashboard

This page describes how to use the dashboard once it is running. For install,
data layout, and deployment, see the [README](../README.md).

## Layout

- **Top bar:** dataset and file selection, CSV download, global options, and the
  reset button.
- **Plots (left):** track, transect, T-S, profile, and scatter. Each has its own
  toolbar with the main choices, an **⬇ Export** button, and a **⚙ More** menu
  for ranges, colormaps, labels, and sizes.
- **Details (right):** the values and camera frames of one point. Drag the
  divider to resize it; double-click the divider to reset the width.

## Choosing data

Pick a dataset folder, then a data file. The lists rescan the data folder every
30 minutes, so new files appear without a reload. The **⟳** button rescans
immediately, clears the cached data, and resets every plot (zoom and
selections).

**Download CSV** asks for a name, email, and institution before it sends the
file. Operators can turn downloads off with
`STINGRAY_DASHBOARD_DOWNLOADS_ENABLED=false`, which disables the button.

### Global options

The **⚙ More** menu next to Download CSV applies to every plot:

| Option | Meaning |
| --- | --- |
| Sampling mode | **Subsample** keeps every Nth point. **Average bins** averages N points per bin. |
| Bin size (N points) | N for either mode. Empty chooses automatically. |
| Max time gap (s) | In average mode, a bin does not run across a gap longer than this, so averages do not bridge separate deployments or casts. |
| Opacity | How visible unselected points are while a selection is active. |
| Size | Marker size. |
| Font | Font size of all plots. Plot text and margins scale with it. |
| Point marker, Marker color | Ring around the Details point on the transect, T-S, scatter, and profile plots. |

## Plots

| Plot | What it shows |
| --- | --- |
| Track | Time, latitude, longitude, or depth against another of them (default time against latitude). Selecting points here filters every other plot. |
| Transect | Depth against latitude, longitude, or time, colored by a variable. Bathymetry and station overlays can be switched on or off. |
| T-S | Temperature against salinity, colored by a variable. |
| Profile | Vertical profiles of one variable for the casts in the current selection. |
| Scatter | Any two numeric variables, optionally colored by a third, with an optional fit. |

Variable lists show numeric columns only. Latitude, longitude, and depth can
also be used as scatter axes and as color variables where they make sense.

### Per-plot options (⚙ More)

- **Colormap:** the Plotly sequential and qualitative maps, plus the cmocean
  maps (named `cmo.<name>`) on the colored plots.
- **Ranges:** depth, latitude, and longitude limits on the transect, X and Y
  limits on the scatter plot, and a color range on every colored plot. Leave
  a box empty for the automatic range. Either order is accepted. Latitude and
  longitude accept negative values for south and west, for example `-10` for
  10°S or 10°W.
- **Color bar:** bottom or right of the plot.
- **X, Y, and Color label:** replace the automatic titles. Empty keeps the
  default. `<sub>` and `<sup>` work, for example `µg l<sup>-1</sup>`.
- **Plot W × H (px):** the size of the plot including its axis text. A color bar
  or legend is added outside that size, so moving the color bar does not change
  the plot.

### Scatter fit

Choose **Linear**, **2nd order**, or **3rd order** in the scatter toolbar. The
fit curve and a summary panel with the equation, R², RMSE, and n appear.
**On selected** fits only the points currently selected in any linked plot;
otherwise the fit uses all points. The fit line color is under More.

## Selecting points

A box or lasso on the **transect, T-S, or scatter** plot is mirrored onto the
other two: the same observations are highlighted everywhere and the rest fade
(see Opacity). The latest selection wins, and the box you drew on a previous
plot is removed. Double-click an empty area to clear the selection.

A selection also drives:

- the **profile** plot, which shows the casts of the selected points;
- the **scatter fit**, when **On selected** is ticked.

A box or lasso on the **track** plot works differently: it filters the data for
every plot, as if the other points did not exist. Clearing it restores all
observations. Changing dataset, file, or bin size also restores all
observations.

## Profile

When the data have a `cast` column, each selected cast is drawn as its own
profile. On towed and AUV tracks these are the vertical segments (pseudo-casts)
identified along the trajectory, so each one is a slanted profile rather than a
fixed station. A selection on the other plots expands to whole casts. Without a
`cast` column the plot shows a median profile with a percentile envelope.
Colors always come from a qualitative palette. Casts are labeled with their
number and position (`Cast · 43.80N 70.10W`); the legend can be switched off in
More, for example before exporting.

## Details

Click a point on the transect, T-S, or scatter plot. Details shows:

- the camera frame or frames, when `media` and `frame` columns exist, with a
  link to open each frame (`media_<stream>` and `frame_<stream>` columns give
  extra camera streams). A frame that cannot load shows a short note;
- time, latitude, longitude, and depth;
- cruise and cast, when those columns exist;
- every sensor variable with its unit.

**◀ Prev** and **Next ▶** step to the previous or next point in time. They move
through the points the plots currently show, after sampling and any track
filter. A ring marks the point on the transect, T-S, and scatter plots, and on
the profile plot its cast is drawn bold, a dot marking its depth, while the
other selected casts fade. A click never changes your box or lasso selection.

## Sharing a view

The URL stores the dashboard settings: dataset, file, variables, ranges,
colormaps, sizes, and labels. A copied link or the browser's Back button brings
back the same view.

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

Plots with very many points keep their markers as raster (WebGL) in the export.
A larger bin size, or averaging, gives sharp vector markers.

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
