from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px

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
        return default_min, default_max
    return data_series.min(), data_series.max()

def get_palette(name):
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
