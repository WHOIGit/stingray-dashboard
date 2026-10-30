from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px

from .config import get_unit


def variable_label(name, with_unit=True):
    """Readable name for a variable, with its unit: 'Chlorophyll (µg l⁻¹)'."""
    label = str(name).replace("_", " ").capitalize()
    unit = get_unit(str(name)) if with_unit else ""
    return f"{label} ({unit})" if unit else label


# --- Figure frame ---------------------------------------------------------------
# Plots are laid out the way R lays out a figure: fixed margins in lines of text
# (R's mar), with the axis text inside them, and the figure sized from the plot
# size plus the margins. Nothing is measured. A bigger text size gives bigger
# margins in proportion, and a color bar or legend adds one extra block outside
# the plot, so the plot (with its axis text) is always the Plot W x H you set.
LINE_EM = 1.25              # one line of text, in text sizes
MARGIN_LEFT_LINES = 4.4     # y tick labels and y title
MARGIN_BOTTOM_LINES = 3.4   # x tick labels and x title
MARGIN_TOP_LINES = 0.8
MARGIN_RIGHT_LINES = 0.8
BAR_GAP_LINES = 0.8         # between the plot with its text and a color bar or legend
BAR_THICKNESS_EM = 1.1
BAR_LABEL_CHARS = 5         # widest tick label of a vertical bar, in characters
LEGEND_CHAR_EM = 0.62
LEGEND_PAD_EM = 2.5
STANDOFF_PX = 6             # axis title distance from the tick labels


def coloraxis_name(position):
    """
    Layout color axis for a color bar position.

    Each position uses its own axis, so changing the position removes the old
    bar instead of leaving pieces of it behind when Plotly redraws.
    """
    return "coloraxis2" if position == "right" else "coloraxis"


def frame_layout(plot_w, plot_h, font, bar=None, legend_chars=0, top_extra=0):
    """
    Figure size and margins (px) for a plot of plot_w x plot_h including its axis
    text. ``bar`` is "bottom" or "right" for a color bar; ``legend_chars`` is the
    longest legend entry (0 for no legend); ``top_extra`` adds room above the
    plot for labels placed there.
    """
    line = LINE_EM * font
    left = round(MARGIN_LEFT_LINES * line)
    bottom = round(MARGIN_BOTTOM_LINES * line)
    top = round(MARGIN_TOP_LINES * line) + round(top_extra)
    right = round(MARGIN_RIGHT_LINES * line)
    gap = round(BAR_GAP_LINES * line)
    thickness = round(BAR_THICKNESS_EM * font)
    tick_font = 0.9 * font
    title_height = 1.2 * (font + 1)

    extra_w = 0
    extra_h = 0
    block_w = 0
    block_h = 0
    if bar == "bottom":
        block_h = round(thickness + 8 + 1.2 * tick_font + 4 + title_height + 3)
        extra_h = gap + block_h
    elif bar == "right":
        block_w = round(thickness + 8 + 0.58 * tick_font * BAR_LABEL_CHARS + 4 + title_height + 3)
        extra_w = gap + block_w
    if legend_chars:
        extra_w += gap + round(LEGEND_CHAR_EM * font * legend_chars + LEGEND_PAD_EM * font)

    return dict(
        width=round(plot_w + extra_w),
        height=round(plot_h + extra_h),
        margin=dict(l=left, r=right + extra_w, t=top, b=bottom + extra_h, pad=0),
        block_w=block_w,
        block_h=block_h,
        tick_font=tick_font,
        legend_x=1 + gap / max(1, plot_w - left - right) if legend_chars else None,
    )


def _y_tick_chars(fig):
    """Rough width, in characters, of the widest y tick label (0 when not numeric)."""
    import math

    import numpy as np

    low, high = None, None
    y_range = fig.layout.yaxis.range
    if y_range is not None and len(y_range) == 2:
        try:
            low, high = float(min(y_range)), float(max(y_range))
        except (TypeError, ValueError):
            low = high = None
    if low is None:
        for trace in fig.data:
            y = getattr(trace, "y", None)
            if y is None:
                continue
            try:
                values = np.asarray(y, dtype=float)
                values = values[np.isfinite(values)]
            except (TypeError, ValueError):
                continue
            if values.size:
                lo, hi = float(values.min()), float(values.max())
                low = lo if low is None else min(low, lo)
                high = hi if high is None else max(high, hi)
    if low is None or high is None:
        return 0
    span = high - low
    biggest = max(abs(low), abs(high))
    if span <= 0 or biggest == 0:
        return 0
    step = span / 6
    decimals = max(0, -math.floor(math.log10(step)))
    label = f"{biggest:.{decimals}f}"
    return len(label) + (1 if low < 0 else 0)


def apply_frame(fig, frame):
    """Give a figure its own size and margins, and fixed (not auto-fitted) axis text room."""
    margin = dict(frame["margin"])
    # Long y tick labels (0.00098, 12500) get room instead of running into the title.
    extra_chars = max(0, _y_tick_chars(fig) - 3)
    margin["l"] = margin["l"] + round(extra_chars * 0.58 * frame["tick_font"])
    fig.update_layout(
        width=frame["width"],
        height=frame["height"],
        autosize=False,
        margin=margin,
    )
    fig.update_xaxes(automargin=False, title_standoff=STANDOFF_PX)
    fig.update_yaxes(automargin=False, title_standoff=STANDOFF_PX)
    if frame["legend_x"] is not None:
        fig.update_layout(legend=dict(x=frame["legend_x"], xanchor="left", y=1, yanchor="top"))
    return fig


def colorbar_layout(position, title, base_font, nticks=8, frame=None):
    """
    Color bar settings for the bottom (horizontal) or right (vertical) of a plot.

    The bar sits in the block frame_layout() reserved at the edge of the figure,
    so it is placed in figure coordinates and its offset from the plot is fixed.
    """
    common = dict(
        tickfont=dict(size=base_font * 0.9),
        lenmode="fraction",
        len=0.75,
        thickness=round(BAR_THICKNESS_EM * base_font),
        ticks="outside",
        tickmode="auto",
        nticks=nticks,
    )
    if position == "right":
        return dict(
            common,
            title=dict(text=title, side="right", font=dict(size=base_font + 1)),
            orientation="v",
            xref="container",
            x=(frame["width"] - frame["block_w"]) / frame["width"],
            xanchor="left",
            xpad=0,
            y=0.5,
            yanchor="middle",
            ticklabelposition="outside",
        )
    return dict(
        common,
        title=dict(text=title, side="bottom", font=dict(size=base_font + 1)),
        orientation="h",
        x=0.5,
        xanchor="center",
        yref="container",
        y=frame["block_h"] / frame["height"],
        yanchor="top",
        ypad=0,
        ticklabelposition="outside bottom",
    )


def dynamic_ticks(vmin, vmax, nticks=6):
    """Dynamic tick label with range"""
    span = abs(vmax - vmin)
    if span == 0:
        return np.array([vmin]), 2
    raw_step = span / (nticks - 1)
    magnitude = 10 ** np.floor(np.log10(raw_step))
    frac = raw_step / magnitude
    if frac < 1.5:
        step = 1 * magnitude
    elif frac < 3:
        step = 2 * magnitude
    elif frac < 7:
        step = 5 * magnitude
    else:
        step = 10 * magnitude
    # determine decimals
    if step >= 1:
        digits = 0
    else:
        digits = int(abs(np.floor(np.log10(step))))
    # compute nice bounds
    start = np.floor(vmin / step) * step
    end   = np.ceil(vmax / step) * step
    ticks = np.arange(start, end + step * 0.5, step)
    return ticks, digits

def get_visible_range(axis_name, relayoutData):
    if relayoutData:
        r0 = f"{axis_name}.range[0]"
        r1 = f"{axis_name}.range[1]"
        if r0 in relayoutData:
            return [relayoutData[r0], relayoutData[r1]]
    return None

def resolve_range(visible_range, data_series, default_min=None, default_max=None):
    if visible_range is not None:
        return min(visible_range), max(visible_range)
    if default_min is not None and default_max is not None:
        # Either order works, as for depth: 70 to 72 or 72 to 70.
        return min(default_min, default_max), max(default_min, default_max)
    return data_series.min(), data_series.max()

def get_palette(name):
    # cmocean palettes (shipped with Plotly, when present) are listed as "cmo.<name>".
    if str(name).startswith("cmo."):
        palette = getattr(getattr(px.colors, "cmocean", None), str(name)[4:], None)
        if isinstance(palette, list):
            return palette, "continuous"
    if hasattr(px.colors.qualitative, name):
        palette = getattr(px.colors.qualitative, name)
        if isinstance(palette, list):
            return palette, "discrete"
    if hasattr(px.colors.sequential, name):
        palette = getattr(px.colors.sequential, name)
        if isinstance(palette, list):
            return palette, "continuous"
    return px.colors.sequential.Viridis, "continuous"

def is_discrete_variable(series):
    s = pd.to_numeric(series.dropna(), errors="coerce")
    if s.empty:
        return True
    if pd.api.types.is_integer_dtype(series):
        return True
    if not pd.api.types.is_numeric_dtype(series):
        return True
    return np.all(np.isclose(s, np.round(s)))

def get_point_id_from_customdata(customdata):
    """
    Extract the stable row index i from Plotly customdata.

    Supported payloads:
      customdata = i
      customdata = [i]
      customdata = [i, extra_value]

    Scientific notation:
      i identifies observation x_i in the server-side dataframe.
    """
    if customdata is None:
        return None

    arr = np.asarray(customdata)

    if arr.ndim == 0:
        return int(arr)

    if arr.size == 0:
        return None

    return int(arr.flat[0])


def get_customdata_from_trace_ids(point, trace_point_ids):
    """
    Recover point_id from compact trace-wise arrays when Dash/Plotly omits
    customdata from clickData or selectedData.

    Event geometry:
      curveNumber = trace index k
      pointNumber/pointIndex = point index j within trace k
      trace_point_ids[k][j] -> point_id i
    """
    if not trace_point_ids:
        return None

    curve_number = point.get("curveNumber")
    point_number = point.get("pointNumber", point.get("pointIndex"))

    if curve_number is None or point_number is None:
        return None

    if curve_number >= len(trace_point_ids):
        return None

    try:
        return trace_point_ids[curve_number][point_number]
    except (IndexError, TypeError):
        return None


def get_point_id_from_event_point(point, trace_point_ids=None):
    """
    Extract point_id from a Dash/Plotly event point.

    Prefer the event payload. Fall back to compact trace-wise point-ID arrays
    because newer Plotly/Dash versions may omit customdata from event data.
    """
    point_id = get_point_id_from_customdata(point.get("customdata"))

    if point_id is not None:
        return point_id

    return get_point_id_from_customdata(
        get_customdata_from_trace_ids(point, trace_point_ids)
    )


def selection_shapes(selected_data):
    """Box/lasso geometry from a Plotly selection event as layout.selections."""
    if not selected_data:
        return []
    box = selected_data.get("range")
    if box and "x" in box and "y" in box:
        return [dict(type="rect", xref="x", yref="y",
                     x0=box["x"][0], x1=box["x"][1], y0=box["y"][0], y1=box["y"][1])]
    lasso = selected_data.get("lassoPoints")
    if lasso and lasso.get("x"):
        path = "M" + "L".join(f"{x},{y}" for x, y in zip(lasso["x"], lasso["y"])) + "Z"
        return [dict(type="path", xref="x", yref="y", path=path)]
    return []


def apply_selection(fig, selection, graph_id=None, axes=None):
    """
    Highlight the stored cross-plot selection on a freshly built figure.

    A rebuilt figure otherwise loses its highlighted points while the other
    linked plots still show them. Traces without point-ID customdata
    (contours, bathymetry, stations) are left alone.

    When this graph is where the selection was drawn and its axes are
    unchanged, the box/lasso is restored too. Without it Plotly fires
    plotly_deselect on redraw, which Dash reports as a cleared selection.
    """
    selection = selection or {}
    selected_ids = selection.get("selected_ids")
    if not selected_ids:
        return fig
    if (
        graph_id is not None
        and selection.get("source") == graph_id
        and selection.get("axes") == axes
        and selection.get("selections")
    ):
        fig.update_layout(selections=selection["selections"])
    ids = np.asarray(selected_ids, dtype=float)
    for trace in fig.data:
        customdata = getattr(trace, "customdata", None)
        if customdata is None or len(customdata) == 0:
            continue
        point_ids = np.asarray(
            [
                value[0] if isinstance(value, (list, tuple, np.ndarray)) else value
                for value in customdata
            ],
            dtype=float,
        )
        trace.selectedpoints = np.nonzero(np.isin(point_ids, ids))[0].tolist()
    return fig


def get_row_by_point_id(df: pd.DataFrame, point_id: int) -> pd.Series | None:
    """
    Recover observation x_i by stable point_id i.

    Prefer the explicit point_id column because filtering, averaging, and
    Plotly serialization can make the dataframe index differ from the plotted
    identifier.
    """
    if df.empty:
        return None

    if "point_id" in df.columns:
        matches = df.loc[df["point_id"] == point_id]

        if not matches.empty:
            return matches.iloc[0]

    if point_id in df.index:
        row = df.loc[point_id]

        if isinstance(row, pd.DataFrame):
            return row.iloc[0]

        return row

    return None
