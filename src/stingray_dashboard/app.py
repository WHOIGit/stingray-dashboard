# ============================================
# Imports
# ============================================

from __future__ import annotations

import argparse
import os
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs

import dash
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, Patch, State, ctx, dcc, html, no_update
from plotly.colors import sample_colorscale

# ============================================
# Constants and module state
# ============================================
DEFAULT_SUBSAMPLE = None
DEFAULT_MAX_TIME_GAP_SEC = 300  # 5 minutes; tune per platform
MAX_WORKERS = max(1, min(os.cpu_count() - 1, 8))

WORK_DIR = Path("dash_data")
DATA_DIR = WORK_DIR / "data"
MISC_DIR = WORK_DIR / "misc"

# Cache structures
DATA_CACHE = {}
CSV_HEADER_CACHE = {}
SENSOR_VAR_CACHE = {}
MAX_DATA_CACHE = 8
AVERAGE_CACHE = {}
MAX_AVG_CACHE = 8

meta_vars = [
    "timestamp", "times", "matdate",
    "latitude", "longitude", "depth",
    "media", "media_path", "frame", "id", "link",
    "media_2", "media_path_2", "frame_2", "id_2", "link_2"
]

stations: pd.DataFrame | None = None
bathy: pd.DataFrame | None = None

DEFAULT_DATASET = "stingray_NESLTER"
def choose_default_dataset(datasets):
    if not datasets:
        return None
    return DEFAULT_DATASET if DEFAULT_DATASET in datasets else datasets[-1]
    
# ============================================
# Units
# ============================================
unit_patterns = {
    "°C": ["temperature","temp","t090","t190","t2","tv","_t"],
    "S m⁻¹": ["conductivity","cond","c0","c1","mS/cm"],
    "dbar": ["pressure","press","p_","pr","prd"],
    "m": ["depth","dep","z","altitude","alt"],
    "psu": ["salinity","sal","sal00","sal11","practical_salinity"],
    "kg m⁻³": ["density","dens","sigma"],
    "°": ["latitude","lat","longitude","lon","pitch","roll","heading"],
    "µM": ["nitrate","no3","suna","concentration"],
    "µg l⁻¹": ["chlorophyll","chl","fluor","fchl","fl"],
    "m⁻¹ sr⁻¹": ["backscattering","bb","bbp"],
    "µmol photons m⁻² s⁻¹": ["par","irradiance","ed"],
    "m s⁻¹": ["sound_velocity","sv","svcm"],
    "%": ["saturation","oxsat","o2sat"],
    "ind m⁻³": [
                "amphipod", "appendicularian", "chaetognath", "copepod", "ctenophore", "doliolid", "euphausids", "fish", "medusa",
                "polychaete", "pteropod", "radiolarian", "salp", "siphonophore", "trichodesmium","veliger"
               ],
}

def get_unit(varname):
    vn = varname.lower()
    # split variable name into tokens
    tokens = vn.replace("-", "_").split("_")
    for unit, pats in unit_patterns.items():
        for p in pats:
            if p in tokens:
                return unit
    return ""

# ============================================
# Scientific utilities
# ============================================
def ies80(s, t, p=0):
    """Compute seawater density using the IES80 equation."""
    s, t, p = map(np.asarray, (s, t, p))

    r0_coef = [
        999.842594, 6.793952e-2, -9.09529e-3, 1.001685e-4, -1.120083e-6,
        6.536332e-9, 8.24493e-1, -4.0899e-3, 7.6438e-5, -8.2467e-7,
        5.3875e-9, -5.72466e-3, 1.0227e-4, -1.6546e-6, 4.8314e-4
    ]

    r0 = (
        np.polyval(r0_coef[:6][::-1], t)
        + np.polyval(r0_coef[6:11][::-1], t) * s
        + np.polyval(r0_coef[11:14][::-1], t) * s**1.5
        + r0_coef[14] * s**2
    )

    if np.any(p):
        K_coef = [
            19652.21, 148.4206, -2.327105, 1.360447e-2, -5.155288e-5, 3.239908,
            1.43713e-3, 1.16092e-4, -5.77905e-7, 8.50935e-5, -6.12293e-6,
            5.2787e-8, 54.6746, -0.603459, 1.09987e-2, -6.1670e-5, 7.944e-2,
            1.6483e-2, -5.3009e-4, 2.2838e-3, -1.0981e-5, -1.6078e-6,
            1.91075e-4, -9.9348e-7, 2.0816e-8, 9.1697e-10
        ]
        K = (
            np.polyval(K_coef[:5][::-1], t)
            + np.polyval(K_coef[5:9][::-1], t) * p
            + np.polyval(K_coef[9:12][::-1], t) * p**2
            + np.polyval(K_coef[12:16][::-1], t) * s
            + np.polyval(K_coef[16:19][::-1], t) * s**1.5
            + np.polyval(K_coef[19:22][::-1], t) * p * s
            + K_coef[22] * p * s**1.5
            + np.polyval(K_coef[23:26][::-1], t) * p**2 * s
        )
        rho = r0 / (1 - p / K)
    else:
        rho = r0
    return rho


@lru_cache(maxsize=32)
def density_grid(tmin, tmax, smin, smax):
    """
    Cache potential density anomaly contours for the T-S diagram.

    Scientific relation:
      sigma_theta(S, T) = rho(S, T, p=0) - 1000 kg m^-3

    The grid depends only on the displayed salinity and temperature bounds,
    so color-variable changes can reuse the same contour field.
    """
    t_values = np.arange(tmin, tmax, 0.5)
    s_values = np.arange(smin, smax, 0.5)
    T, S = np.meshgrid(t_values, s_values, indexing="ij")
    D = ies80(S, T, 0) - 1000
    return t_values, s_values, D


# ============================================
# File Utilities
# ============================================
def get_link(media, frame):
    if media is not None and frame is not None:
        return f"https://stingraydash.whoi.edu/fv/frames/{media}/{frame}?format=png"
    return None

def scan_datasets() -> list[str]:
    """
    Returns a list of available dataset folders under DATA_DIR.
    Example:
      /dash_data/data/NESLTER_2022/...
      returns ['NESLTER_2022']
    """
    if not DATA_DIR.exists():
        return []
    return sorted([f.name for f in DATA_DIR.iterdir() if f.is_dir()])

def get_csv_files(dataset: str) -> list[str]:
    """
    Returns CSV stem names inside the selected dataset folder.
    Example:
        dataset = 'NESLTER_2022'
        scans /dash_data/data/NESLTER_2022/*.csv
    """
    dataset_path = DATA_DIR / dataset
    if not dataset_path.exists():
        return []
    return sorted(f.stem for f in dataset_path.glob("*.csv") if f.is_file())

@lru_cache(maxsize=4)
def load_csv(path: Path) -> pd.DataFrame | None:
    """Read a small auxiliary CSV file (stations, bathymetry)."""
    return pd.read_csv(path, dtype=str, encoding="utf-8") if path.exists() else None

def canonicalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.copy()
    def norm_colname(c: str) -> str:
        c = str(c).strip().lower()
        c = re.sub(r"_[0-9]+$", "", c)  # T090_1 -> t090, Sal00_2 -> sal00
        return c
    cols = {norm_colname(c): c for c in df.columns}
    aliases = {
        "latitude": ["latitude", "lat"],
        "longitude": ["longitude", "lon"],
        "temperature": ["temperature", "t090", "t090c", "t190", "t190c"],
        "salinity": ["salinity", "sal00", "sal11"],
        "pressure": ["pressure", "press", "prd", "prdm"],
        "depth": ["depth", "depsm", "z"],
        "times": ["times", "time"],
        "date": ["date"],
    }
    for canon, names in aliases.items():
        if canon not in cols:
            for n in names:
                if n in cols:
                    df[canon] = df[cols[n]]
                    break
    cols = {norm_colname(c): c for c in df.columns}
    if "depth" not in cols and "pressure" in cols:
        df["depth"] = df[cols["pressure"]]
    has_date = "date" in cols
    has_times = "times" in cols
    if has_times:
        times_col = cols["times"]
        is_numeric_times = pd.api.types.is_numeric_dtype(df[times_col])
        if is_numeric_times:
            df["time_s"] = df[times_col]
            if times_col != "times":
                df.drop(columns=[times_col], inplace=True)
            else:
                df.drop(columns=["times"], inplace=True)
            if has_date:
                df["times"] = pd.to_datetime(df[cols["date"]], errors="coerce")
            else:
                df["times"] = pd.NaT
        else:
            df["times"] = pd.to_datetime(df[times_col], errors="coerce")
    else:
        if has_date:
            df["times"] = pd.to_datetime(df[cols["date"]], errors="coerce")
        else:
            df["times"] = pd.NaT
    return df

def load_data(dataset: str, file_name: str, sub_sample: int | None = None, mode="subsample"):
    dataset_path = DATA_DIR / dataset
    csv_path = dataset_path / f"{file_name}.csv"
    if not csv_path.exists():
        return pd.DataFrame()
    base_key = f"{dataset}/{file_name}"
    # =========================================================
    # RAW DATA CACHE
    # =========================================================
    if base_key not in DATA_CACHE:
        # Read the dataset once; downstream steps normalize columns and types.
        df = pd.read_csv(csv_path, low_memory=False)
        df = canonicalize_columns(df)
        # downcast for memory efficiency
        int_cols = df.select_dtypes(include=["int64"]).columns
        for col in int_cols:
            s = df[col]
            if s.min() >= np.iinfo(np.int32).min and s.max() <= np.iinfo(np.int32).max:
                df[col] = s.astype(np.int32)
        if "point_id" not in df.columns:
            df = df.assign(point_id=np.arange(len(df), dtype=np.int32))
        df = df.set_index("point_id", drop=False)
        if len(DATA_CACHE) >= MAX_DATA_CACHE:
            DATA_CACHE.pop(next(iter(DATA_CACHE)))
        DATA_CACHE[base_key] = df
    df = DATA_CACHE[base_key]
    # =========================================================
    # AUTO SUBSAMPLING
    # =========================================================
    if sub_sample is None:
        n_rows = len(df)
        TARGET_POINTS = 30_000  
        if n_rows <= TARGET_POINTS:
            sub_sample = 1
        else:
            sub_sample = int(np.ceil(n_rows / TARGET_POINTS))
    # =========================================================
    # NO SAMPLING
    # =========================================================
    if sub_sample <= 1:
        return df
    # =========================================================
    # SUBSAMPLE MODE
    # =========================================================
    if mode == "subsample":
        return df.iloc[::sub_sample]
    # =========================================================
    # AVERAGE MODE WITH CACHE
    # =========================================================
    if mode == "average":
        max_gap = getattr(load_data, "_max_gap_seconds", DEFAULT_MAX_TIME_GAP_SEC)
        avg_key = f"{base_key}/{sub_sample}/{max_gap}"
        if avg_key in AVERAGE_CACHE:
            return AVERAGE_CACHE[avg_key]
        n = len(df)
        if n == 0:
            return df
        # -----------------------------------------------------
        # Build segment IDs (deployment-aware if available)
        # -----------------------------------------------------
        if "deployment" in df.columns:
            # Use precomputed deployment segmentation
            seg = df["deployment"].to_numpy(np.int32)
            new_segment = np.zeros(n, dtype=bool)
            new_segment[0] = True
            new_segment[1:] = seg[1:] != seg[:-1]
        elif "times" in df.columns and not df["times"].isna().all():
            # Fallback to time-gap segmentation
            dt = df["times"].diff().dt.total_seconds().to_numpy()
            new_segment = np.zeros(n, dtype=bool)
            new_segment[0] = True
            new_segment[1:] = (dt[1:] > max_gap) | np.isnan(dt[1:])
            seg = np.cumsum(new_segment)
        else:
            # No time and no deployment → treat entire dataset as one segment
            seg = np.zeros(n, dtype=np.int32)
            new_segment = np.zeros(n, dtype=bool)
            new_segment[0] = True
        # -----------------------------------------------------
        # Compute index within each segment
        # -----------------------------------------------------
        seg_start_idx = np.where(new_segment, np.arange(n), 0)
        seg_start_idx = np.maximum.accumulate(seg_start_idx)
        idx_in_seg = np.arange(n) - seg_start_idx
        # -----------------------------------------------------
        # Compute full-bin mask (discard short bins)
        # -----------------------------------------------------
        seg_sizes = np.bincount(seg)
        row_seg_size = seg_sizes[seg]
        full_bins = row_seg_size // sub_sample
        bin_index = idx_in_seg // sub_sample
        valid_mask = bin_index < full_bins
        if not np.any(valid_mask):
            return pd.DataFrame()
        # -----------------------------------------------------
        # Build groups only for valid rows
        # -----------------------------------------------------
        groups = seg * (n + 1) + bin_index
        dfo = df.loc[valid_mask].copy()
        dfo["_group"] = groups[valid_mask]
        # -----------------------------------------------------
        # Aggregate
        # -----------------------------------------------------
        numeric_cols = [
            c for c in dfo.select_dtypes(include=[np.number]).columns
            if c not in ("point_id", "_group", "frame", "frame_2")
        ]
        meta_cols = [
            c for c in dfo.columns
            if c not in numeric_cols and c not in ("point_id", "_group")
        ]
        grouped = dfo.groupby("_group", sort=False)
        avg_num = grouped[numeric_cols].mean()
        avg_meta = grouped[meta_cols].first()
        out = pd.concat([avg_meta, avg_num], axis=1).reset_index(drop=True)
        out["point_id"] = np.arange(len(out), dtype=np.int32)
        if "cast" in out.columns:
            out["cast"] = out["cast"].round().to_numpy(np.int32)
        out = out.set_index("point_id", drop=False)
        out = canonicalize_columns(out)
        # -----------------------------------------------------
        # Cache
        # -----------------------------------------------------
        if len(AVERAGE_CACHE) >= MAX_AVG_CACHE:
            AVERAGE_CACHE.pop(next(iter(AVERAGE_CACHE)))
        AVERAGE_CACHE[avg_key] = out
        return out
    
def init_data_dirs(work_dir: str | Path | None = None) -> None:
    """
    Initialize dashboard data directories.

    Directory contract:
      WORK_DIR/
        data/   dataset folders shown in the Dataset dropdown
        misc/   station and bathymetry CSV files

    If work_dir is None, prefer /dash_data when available, otherwise use ./dash_data.
    """
    global WORK_DIR, DATA_DIR, MISC_DIR

    WORK_DIR = (
        Path("/dash_data")
        if work_dir is None and Path("/dash_data").is_dir()
        else Path(work_dir or "dash_data")
    )

    DATA_DIR = WORK_DIR / "data"
    MISC_DIR = WORK_DIR / "misc"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MISC_DIR.mkdir(parents=True, exist_ok=True)


def load_auxiliary_data() -> None:
    global stations, bathy

    stations = load_csv(MISC_DIR / "NESLTER_station_list.csv")
    bathy = load_csv(MISC_DIR / "NESLTER_transect_bathymetry.csv")

    if stations is not None:
        stations["latitude"] = pd.to_numeric(stations["latitude"], errors="coerce")

    if bathy is not None:
        bathy["latitude"] = pd.to_numeric(bathy["latitude"], errors="coerce")
        bathy["bottom_depth_meters"] = pd.to_numeric(
            bathy["bottom_depth_meters"],
            errors="coerce",
        )

# ========================
# Plotting utilities
# ========================
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


def get_customdata_from_figure_point(point, figure):
    """
    Recover customdata from the rendered figure when Dash/Plotly omits it
    from clickData or selectedData.

    Event geometry:
      curveNumber = trace index k
      pointNumber/pointIndex = point index j within trace k
      customdata[k][j] -> point_id i
    """
    if not figure:
        return None

    curve_number = point.get("curveNumber")
    point_number = point.get("pointNumber", point.get("pointIndex"))

    if curve_number is None or point_number is None:
        return None

    traces = figure.get("data", [])

    if curve_number >= len(traces):
        return None

    customdata = traces[curve_number].get("customdata")

    if customdata is None:
        return None

    try:
        return customdata[point_number]
    except (IndexError, TypeError):
        return None


def get_point_id_from_event_point(point, figure=None):
    """
    Extract point_id from a Dash/Plotly event point.

    Prefer the event payload. Fall back to the full figure because newer
    Plotly/Dash versions may omit customdata from event data.
    """
    point_id = get_point_id_from_customdata(point.get("customdata"))

    if point_id is not None:
        return point_id

    return get_point_id_from_customdata(
        get_customdata_from_figure_point(point, figure)
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

# ========================
# App Layout 
# ========================
def make_layout() -> html.Div:
    datasets = scan_datasets()
    selected_dataset = choose_default_dataset(datasets)
    csv_files = get_csv_files(selected_dataset) if selected_dataset else []
    if csv_files:
        # Sample only enough rows to canonicalize names while preserving all
        # CSV columns for dropdown choices.
        csv_path = DATA_DIR / selected_dataset / f"{csv_files[-1]}.csv"
        df = pd.read_csv(csv_path, nrows=1000, low_memory=True)
        df = canonicalize_columns(df)
    else:
        df = pd.DataFrame()
    sensor_vars = [
        col for col in df.columns
        if "_std" not in col and col not in meta_vars
    ] if not df.empty else []
    default_color_var = (
        "temperature"
        if "temperature" in sensor_vars
        else (sensor_vars[0] if sensor_vars else None)
    )
    ts_candidates = [v for v in sensor_vars if v not in ["temperature", "salinity"]]
    default_ts_var = (
        "chlorophyll"
        if "chlorophyll" in ts_candidates
        else (ts_candidates[0] if ts_candidates else None)
    )
    default_profile_var = (
        "temperature"
        if "temperature" in sensor_vars
        else (sensor_vars[0] if sensor_vars else None)
    )
    sequential_maps = [
        name for name in px.colors.sequential.__dict__
        if not name.startswith("_") and isinstance(getattr(px.colors.sequential, name), list)
    ]
    qualitative_maps = [
        name for name in px.colors.qualitative.__dict__
        if not name.startswith("_") and isinstance(getattr(px.colors.qualitative, name), list)
    ]
    colormaps = sequential_maps + qualitative_maps

    return html.Div([
        # --- URL Sync ---
        dcc.Location(id='url', refresh=False),
        dcc.Store(id="url_restore_done", data=False),
        # --- Auto file scanner ---
        dcc.Interval(id="file-scan-interval", interval=600 * 1000, n_intervals=0),
        dcc.Store(id="file-snapshot", data={}),
        # ===== TOP HEADER =====
        html.Div([
            html.A(
                html.Img(src='/assets/WHOI_OneLineLogo_WhiteType_RGB.png',
                        style={'height': '30px'}, title='WHOI Homepage'),
                href="https://www.whoi.edu/", target="_blank"
            ),
            html.A(
                html.Img(src='/assets/lter-network.png',
                        style={'height': '25px'}, title='LTER Network Homepage'),
                href="https://lternet.edu/", target="_blank"
            )
        ], className='header-top flex-row'),
        # ===== SUB HEADER =====
        html.Div([
            html.Span('STINGRAY DASHBOARD', className='header-title'),
            html.A(
                html.Img(src='/assets/NES-LTER-horizontal.png',
                        style={'height': '40px'}, title='NES-LTER Homepage'),
                href="https://nes-lter.whoi.edu/", target="_blank"
            )
        ], className='header-sub flex-row'),
        # ===== MAIN BODY =====
        html.Div([
            # --- LEFT CONTROLS ---
            html.Div([
                # Dataset selection
                html.Div([
                    html.Label('Dataset:', style={'font-size': '20px'}),
                    dcc.Dropdown(
                        id='dataset_selector',
                        options=[{'label': f, 'value': f} for f in datasets],
                        value=selected_dataset,
                        clearable=False
                    ),
                    html.Label('Data file:', style={'font-size': '20px'}),
                    dcc.Dropdown(
                        id='csv_selector',
                        options=[{'label': f, 'value': f} for f in csv_files],
                        value=csv_files[-1] if csv_files else None,
                        clearable=False
                    ),
                    html.Button('Refresh list', id='refresh-button'),
                    html.Div([
                        html.Label('Cruise track X-axis:'),
                        dcc.Dropdown(
                            id='cruise_track_xaxis',
                            options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                            value='times'
                        )
                    ], style={'marginTop': '6px'}),
                    html.Div([
                        html.Label('Cruise track Y-axis:'),
                        dcc.Dropdown(
                            id='cruise_track_yaxis',
                            options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                            value='latitude'
                        )
                    ])
                ], className='panel'),
                # Row 2: Transect controls
                html.Div([
                    html.Label('TRANSECT PLOT:', className='section-label'),
                    dcc.Checklist(
                        id='bathymetry',
                        options=[{'label': 'Bathymetry', 'value': 'True'}],
                        value=['True']
                    ),
                    dcc.Checklist(
                        id='station',
                        options=[{'label': 'Stations', 'value': 'True'}],
                        value=['True']
                    ),
                    html.Div([
                        html.Label('X-Axis:'),
                        dcc.Dropdown(
                            id='x_axis_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in ['latitude', 'longitude', 'times']],
                            value='latitude'
                        )
                    ]),
                    html.Div([
                        html.Label('Y-Axis:'),
                        dcc.Dropdown(
                            id='y_axis_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in ['depth','latitude']],
                            value='depth'
                        )
                    ]),            
                    html.Div([
                        html.Label('Color Variable:'),
                        dcc.Dropdown(
                            id='color_variable',
                            options=[{'label': var.capitalize(), 'value': var} for var in sensor_vars],
                            value=default_color_var
                        )
                    ]),
                    html.Div([
                        html.Label('Colormap:'),
                        dcc.Dropdown(
                            id='color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                            value='Jet'
                        )
                    ]),
                    html.Div([
                        html.Label('Min Depth:'),
                        dcc.Input(id='z_min', type='number', value=0, debounce=True)
                    ]),
                    html.Div([
                        html.Label('Max Depth:'),
                        dcc.Input(id='z_max', type='number', value=200, debounce=True)
                    ]),
                    html.Div([
                        html.Label('Color Min:'),
                        dcc.Input(id='v_min', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Color Max:'),
                        dcc.Input(id='v_max', type='number', debounce=True)
                    ])
                ], className='panel'),
                # Row 3: TS controls
                html.Div([
                    html.Label('T-S PLOT:', className='section-label'),
                    html.Div([
                        html.Label('Color Variable:'),
                        dcc.Dropdown(
                            id='ts_color_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in sensor_vars if var not in ['temperature', 'salinity']],
                            value=default_ts_var
                        )
                    ]),
                    html.Div([
                        html.Label('Colormap:'),
                        dcc.Dropdown(
                            id='ts_color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                            value='Viridis'
                        )
                    ]),
                    html.Div([
                        html.Label('Color Min:'),
                        dcc.Input(id='ts_v_min', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Color Max:'),
                        dcc.Input(id='ts_v_max', type='number', debounce=True)
                    ])
                ], className='panel'),
                # Row 3.5: Profile controls
                html.Div([
                    html.Label('PROFILE PLOT:', className='section-label'),
                    html.Div([
                        html.Label('Variable:'),
                        dcc.Dropdown(
                            id='profile_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in sensor_vars],
                            value=default_profile_var
                        )
                    ]),
                    html.Div([
                        html.Label('Colormap:'),
                        dcc.Dropdown(
                            id='profile_color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in qualitative_maps],
                            value='Plotly'
                        )
                    ]),
                ], className='panel'),
                # Row 4: Other options (moved to bottom)
                html.Div([
                    html.Label('OTHER OPTIONS:', className='section-label'),
                    html.Div([
                        html.Label('Sampling mode:'),
                        dcc.Dropdown(
                            id='sampling_mode',
                            options=[
                                {'label':'Subsample','value':'subsample'},
                                {'label':'Average bins','value':'average'}
                            ],
                            value='subsample',
                            clearable=False
                        )
                    ]),
                    html.Div([
                        html.Label('Bin size (N points):'),
                        dcc.Input(id='sub_sample', type='number', value=DEFAULT_SUBSAMPLE, placeholder="Auto", debounce=True)
                    ]),
                    html.Div([
                        html.Label('Max time gap for averaging (sec):'),
                        dcc.Input(id='max_gap_seconds', type='number', value=DEFAULT_MAX_TIME_GAP_SEC, debounce=True)
                    ]),
                    html.Div([
                        html.Label('Opacity:'),
                        dcc.Input(id='hidden_opacity', type='number', value=0.1, debounce=True)
                    ]),
                    html.Div([
                        html.Label('Size:'),
                        dcc.Input(id='size', type='number', value=5, debounce=True)
                    ]),
                    html.Hr(),
                    html.Label('PLOT LAYOUT:', className='section-label'),
                    html.Div([
                        html.Label('Cruise Track Width (px):'),
                        dcc.Input(id='track_width', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Cruise Track Height (px):'),
                        dcc.Input(id='track_height', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Main Plot Width (px):'),
                        dcc.Input(id='main_width', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Main Plot Height (px):'),
                        dcc.Input(id='main_height', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('TS Plot Width (px):'),
                        dcc.Input(id='ts_width', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('TS Plot Height (px):'),
                        dcc.Input(id='ts_height', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Profile Plot Width (px):'),
                        dcc.Input(id='profile_width', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Profile Plot Height (px):'),
                        dcc.Input(id='profile_height', type='number', debounce=True)
                    ]),
                    html.Div([
                        html.Label('Font Size:'),
                        dcc.Input(id='plot_font_size', type='number', value=14, debounce=True)
                    ]),
                ], className='panel')
            ], className='left-panel'),
            # --- MIDDLE PLOTS ---
            html.Div([
                html.Div([
                    dcc.Graph(
                        id='cruise_track',
                        responsive=True,
                        style={"width": "100%", "height": "100%"}
                    )
                ], id='track_container', className='cruise-track-graph resizable'),
                html.Div([
                    dcc.Graph(
                        id='main_plot',
                        responsive=True,
                        style={"width": "100%", "height": "100%"}
                    )
                ], id='main_container', className='main-graph resizable'),
                html.Div([
                    dcc.Graph(
                        id='ts_plot',
                        responsive=True,
                        style={"width": "100%", "height": "100%"}
                    )
                ], id='ts_container', className='ts-graph resizable'),
                html.Div([
                    dcc.Graph(
                        id='profile_plot',
                        responsive=True,
                        style={"width": "100%", "height": "100%"}
                    )
                ], id='profile_container', className='profile-graph resizable'),
                dcc.Store(id='cruise_track_selected_data'),
                dcc.Store(id='cruise_track_selection_store', data={"selected_ids": None}),
                dcc.Store(id='main_plot_selected_data'),
                dcc.Store(id="plot_size_store", storage_type="local")
            ], className='middle-panel'),
            # --- RIGHT PANEL (Selected Data Info only) ---
            html.Div([
                html.Label('Details:', className='section-label', style={'font-size': '16px'}),
                html.Div(id='click-output', className='card', style={
                    'font-size': '13px',
                    'line-height': '1.4em'
                }),
            ], className='right-panel', style={
                'flex': '3.5',        # wider
                'minWidth': '320px',
                'maxWidth': '480px'
            })
        ], className='flex-row'),
        # ===== FOOTER =====
        html.Div([
            html.Span('Developed by: Anh Pham, Sidney Batchelder, Heidi Sosik'),
            html.Span('anh.pham@whoi.edu')
        ], className='footer')
    ])

# ============================================================
# === URL Synchronization & Dataset Management Callbacks ===
# ============================================================

# Define which UI parameters should be mirrored in the URL
URL_SYNCED_PARAMS = [
    # dataset / file
    {"key": "dataset", "id": "dataset_selector", "default": DEFAULT_DATASET, "type": "string"},
    {"key": "file", "id": "csv_selector", "default": None, "type": "string"},
    # cruise track
    {"key": "trackx", "id": "cruise_track_xaxis", "default": "times", "type": "string"},
    {"key": "tracky", "id": "cruise_track_yaxis", "default": "latitude", "type": "string"},
    # main transect plot
    {"key": "x", "id": "x_axis_variable", "default": "latitude", "type": "string"},
    {"key": "y", "id": "y_axis_variable", "default": "depth", "type": "string"},
    {"key": "variable", "id": "color_variable", "default": "temperature", "type": "string"},
    {"key": "colormap", "id": "color_map", "default": "Jet", "type": "string"},
    {"key": "size", "id": "size", "default": 5, "type": "int"},
    {"key": "zmin", "id": "z_min", "default": 0, "type": "float"},
    {"key": "zmax", "id": "z_max", "default": 200, "type": "float"},
    {"key": "vmin", "id": "v_min", "default": None, "type": "float", "write": False},
    {"key": "vmax", "id": "v_max", "default": None, "type": "float", "write": False},
    {"key": "opacity", "id": "hidden_opacity", "default": 0.1, "type": "float"},
    {"key": "bathymetry", "id": "bathymetry", "default": ["True"], "type": "list"},
    {"key": "station", "id": "station", "default": ["True"], "type": "list"},
    # TS plot
    {"key": "tsvar", "id": "ts_color_variable", "default": "chlorophyll", "type": "string"},
    {"key": "tsmap", "id": "ts_color_map", "default": "Viridis", "type": "string"},
    {"key": "tsvmin", "id": "ts_v_min", "default": None, "type": "float", "write": False},
    {"key": "tsvmax", "id": "ts_v_max", "default": None, "type": "float", "write": False},
    # profile plot
    {"key": "profilevar", "id": "profile_variable", "default": "chlorophyll", "type": "string"},
    {"key": "profilemap", "id": "profile_color_map", "default": "Plotly", "type": "string"},
    # sampling / averaging
    {"key": "sampling", "id": "sampling_mode", "default": "subsample", "type": "string"},
    {"key": "subsample", "id": "sub_sample", "default": DEFAULT_SUBSAMPLE, "type": "int"},
    {"key": "maxgap", "id": "max_gap_seconds", "default": DEFAULT_MAX_TIME_GAP_SEC, "type": "int"},
    # layout
    {"key": "trackw", "id": "track_width", "default": None, "type": "int"},
    {"key": "trackh", "id": "track_height", "default": None, "type": "int"},
    {"key": "mainw", "id": "main_width", "default": None, "type": "int"},
    {"key": "mainh", "id": "main_height", "default": None, "type": "int"},
    {"key": "tsw", "id": "ts_width", "default": None, "type": "int"},
    {"key": "tsh", "id": "ts_height", "default": None, "type": "int"},
    {"key": "profilew", "id": "profile_width", "default": None, "type": "int"},
    {"key": "profileh", "id": "profile_height", "default": None, "type": "int"},
    # global text
    {"key": "fontsize", "id": "plot_font_size", "default": 14, "type": "int"},
]

def _serialize_url_value(value, typ):
    if value is None:
        return None
    if typ == "list":
        if not value:
            return None
        return ",".join(map(str, value))
    return str(value)

def _deserialize_url_value(raw, typ, default=None):
    if raw is None:
        return default
    if typ == "string":
        return raw
    if typ == "int":
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default
    if typ == "float":
        try:
            return float(raw)
        except (ValueError, TypeError):
            return default
    if typ == "list":
        if raw == "":
            return []
        return [x for x in raw.split(",") if x != ""]
    return default

def register_callbacks(app: dash.Dash) -> None:
    # --- Callback: Update URL query string based on current UI state ---
    @app.callback(
        Output("url", "search"),
        [Input(p["id"], "value") for p in URL_SYNCED_PARAMS],
        State("url", "search"),
        State("url_restore_done", "data"),
        prevent_initial_call=True
    )
    def update_url(*args):
        *values, current_search, restore_done = args
        if not restore_done:
            return no_update
        current_values = {p["key"]: val for p, val in zip(URL_SYNCED_PARAMS, values)}
        dataset = current_values.get("dataset")
        file_name = current_values.get("file")
        if not dataset or not file_name:
            return no_update
        existing_params = parse_qs(urlparse(current_search or "").query)
        params = {
            k: v[0]
            for k, v in existing_params.items()
            if k not in {p["key"] for p in URL_SYNCED_PARAMS}
        }
        params["dataset"] = str(dataset)
        params["file"] = str(file_name)
        for p in URL_SYNCED_PARAMS:
            key = p["key"]
            if key in ("dataset", "file"):
                continue
            if p.get("write", True) is False:
                params.pop(key, None)
                continue
            val = current_values.get(key)
            default = p.get("default")
            if val in (None, "", []):
                params.pop(key, None)
                continue
            if val == default:
                params.pop(key, None)
                continue
            sval = _serialize_url_value(val, p["type"])
            if sval is None:
                params.pop(key, None)
            else:
                params[key] = sval
        new_search = f"?{urlencode(params)}"
        if new_search == (current_search or ""):
            return no_update
        return new_search

    # --- Callback: Restore UI state from URL query string ---
    @app.callback(
        [Output(p["id"], "value", allow_duplicate=True) for p in URL_SYNCED_PARAMS] +
        [Output("url_restore_done", "data", allow_duplicate=True)],
        Input("url", "search"),
        prevent_initial_call="initial_duplicate",
    )
    def restore_from_url(search):
        datasets = scan_datasets()
        params = parse_qs(urlparse(search or "").query)
        dataset_default = choose_default_dataset(datasets)
        dataset_val = params.get("dataset", [dataset_default])[0]
        if dataset_val not in datasets:
            dataset_val = dataset_default
        csv_files = get_csv_files(dataset_val) if dataset_val else []
        file_default = csv_files[-1] if csv_files else None
        file_val = params.get("file", [file_default])[0]
        if file_val not in csv_files:
            file_val = file_default
        defaults = {}
        for p in URL_SYNCED_PARAMS:
            if p["key"] == "dataset":
                defaults["dataset"] = dataset_val
            elif p["key"] == "file":
                defaults["file"] = file_val
            else:
                defaults[p["key"]] = p["default"]
        results = []
        for p in URL_SYNCED_PARAMS:
            key = p["key"]
            typ = p["type"]
            if key == "dataset":
                val = dataset_val
            elif key == "file":
                val = file_val
            else:
                raw = params.get(key, [None])[0]
                val = _deserialize_url_value(raw, typ, defaults.get(key))
            results.append(val)
        return results + [True]

    # --- Callback: Refresh available dataset list ---
    @app.callback(
        Output("dataset_selector", "options"),
        Output("dataset_selector", "value"),
        Input("file-scan-interval", "n_intervals"),
        State("dataset_selector", "value"),
    )
    def refresh_dataset_list(_, current_value):
        ds = scan_datasets()
        options = [{'label': f, 'value': f} for f in ds]
        if not ds:
            return [], None
        if current_value in ds:
            return options, current_value
        if current_value is None:
            default_ds = (
                DEFAULT_DATASET
                if DEFAULT_DATASET in ds
                else ds[-1]
            )
            return options, default_ds
        return options, no_update

    # --- Callback: Refresh available CSV file list ---
    @app.callback(
        Output("csv_selector", "options"),
        Output("csv_selector", "value"),
        Input("dataset_selector", "value"),
        Input("refresh-button", "n_clicks"),
        Input("file-scan-interval", "n_intervals"),
        State("csv_selector", "value"),
        State("url", "search"),
    )
    def update_csv_files(dataset, n_clicks, _scan_tick, current_value, search):
        if not dataset:
            return [], None
        triggered = ctx.triggered_id
        if triggered == "refresh-button":
            DATA_CACHE.clear()
            AVERAGE_CACHE.clear()
            CSV_HEADER_CACHE.clear()
            SENSOR_VAR_CACHE.clear()
            load_csv.cache_clear()
        elif triggered == "dataset_selector":
            DATA_CACHE.clear()
            AVERAGE_CACHE.clear()
        csv_files = get_csv_files(dataset)
        options = [{"label": f, "value": f} for f in csv_files]
        params = parse_qs(urlparse(search or "").query)
        url_file = params.get("file", [None])[0]
        if url_file in csv_files:
            return options, url_file
        if current_value in csv_files:
            return options, current_value
        return options, (csv_files[-1] if csv_files else None)
    # ============================================================
    # === Color Variable and Range Management ===
    # ============================================================
    @app.callback(
        [
            Output('color_variable', 'options'),
            Output('color_variable', 'value'),
            Output('ts_color_variable', 'options'),
            Output('ts_color_variable', 'value'),
            Output('profile_variable', 'options'),
            Output('profile_variable', 'value')
        ],
        Input('dataset_selector', 'value'),
        Input('csv_selector', 'value'),
        State('color_variable', 'value'),
        State('ts_color_variable', 'value'),
        State('profile_variable', 'value'),
        State('url', 'search'),
        prevent_initial_call=True
    )
    def update_color_variable_options(dataset, csv_file,
                                  current_color,
                                  current_ts_color,
                                  current_profile_var,
                                    search):
        trigger = ctx.triggered_id
        # Only rebuild variable dropdowns when the selected dataset/file changes.
        # This prevents TS/profile variable changes from cascading into other plots.
        if trigger not in ("dataset_selector", "csv_selector"):
            raise dash.exceptions.PreventUpdate
        if not dataset or not csv_file:
            return [], None, [], None, [], None
        csv_path = DATA_DIR / dataset / f"{csv_file}.csv"
        if csv_path not in SENSOR_VAR_CACHE:
            dfi = pd.read_csv(csv_path, nrows=1000, low_memory=True)
            dfi = canonicalize_columns(dfi)
            SENSOR_VAR_CACHE[csv_path] = [
                c for c in dfi.columns
                if "_std" not in c and c not in meta_vars
            ]
        sensor_vars = SENSOR_VAR_CACHE[csv_path]
        options = [{'label': v.capitalize(), 'value': v} for v in sensor_vars]
        default_color = "temperature" if "temperature" in sensor_vars else (sensor_vars[0] if sensor_vars else None)
        ts_candidates = [v for v in sensor_vars if v not in ['temperature', 'salinity']]
        ts_options = [{'label': v.capitalize(), 'value': v} for v in ts_candidates]
        default_ts = "chlorophyll" if "chlorophyll" in ts_candidates else (ts_candidates[0] if ts_candidates else None)
        default_profile = "temperature" if "temperature" in sensor_vars else (sensor_vars[0] if sensor_vars else None)
        params = parse_qs(urlparse(search or "").query)
        url_color = params.get("variable", [None])[0]
        url_ts = params.get("tsvar", [None])[0]
        url_profile = params.get("profilevar", [None])[0]
        if url_color in sensor_vars:
            color_val = url_color
        elif current_color in sensor_vars:
            color_val = current_color
        else:
            color_val = default_color
        if url_ts in ts_candidates:
            ts_val = url_ts
        elif current_ts_color in ts_candidates:
            ts_val = current_ts_color
        else:
            ts_val = default_ts
        if url_profile in sensor_vars:
            profile_val = url_profile
        elif current_profile_var in sensor_vars:
            profile_val = current_profile_var
        else:
            profile_val = default_profile
        return options, color_val, ts_options, ts_val, options, profile_val

    # --- Callback: Reset main plot color limits when color variable changes ---
    @app.callback(
        Output('v_min', 'value'),
        Output('v_max', 'value'),
        Input('color_variable', 'value'),
        prevent_initial_call=True
    )
    def reset_vmin_vmax(color_var):
        """Reset main plot color scale limits."""
        return None, None


    # --- Callback: Reset time-series color limits when color variable changes ---
    @app.callback(
        Output('ts_v_min', 'value'),
        Output('ts_v_max', 'value'),
        Input('ts_color_variable', 'value'),
        prevent_initial_call=True
    )
    def reset_ts_vmin_vmax(color_var):
        """Reset time-series color scale limits."""
        return None, None

    app.clientside_callback(
    """
    function(_, existing) {
        if (!window.plotResizeObserver) {
            window.plotResizeObserver = new ResizeObserver(() => {
                function getSize(id){
                    const el = document.getElementById(id);
                    if(!el) return null;
                    const rect = el.getBoundingClientRect();
                    return {
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    };
                }
                const sizes = {
                    track: getSize("track_container"),
                    main: getSize("main_container"),
                    ts: getSize("ts_container"),
                    profile: getSize("profile_container")
                };
                window.dash_clientside.set_props("plot_size_store", {data: sizes});
            });
            ["track_container", "main_container", "ts_container", "profile_container"].forEach(id => {
                const el = document.getElementById(id);
                if (el) window.plotResizeObserver.observe(el);
            });
        }
        return existing;
    }
    """,
    Output("plot_size_store", "data"),
    Input("main_container", "id"),
    State("plot_size_store", "data")
    )

    @app.callback(
        Output("track_width","value"),
        Output("track_height","value"),
        Output("main_width","value"),
        Output("main_height","value"),
        Output("ts_width","value"),
        Output("ts_height","value"),
        Output("profile_width","value"),
        Output("profile_height","value"),
        Input("plot_size_store","data"),
    )
    def update_size_inputs(data):
        if not data:
            raise dash.exceptions.PreventUpdate
        return (
            data["track"]["width"],
            data["track"]["height"],
            data["main"]["width"],
            data["main"]["height"],
            data["ts"]["width"],
            data["ts"]["height"],
            data["profile"]["width"],
            data["profile"]["height"],
        )

    @app.callback(
        Output("track_container","style"),
        Output("main_container","style"),
        Output("ts_container","style"),
        Output("profile_container","style"),
        Input("track_width", "value"),
        Input("track_height", "value"),
        Input("main_width","value"),
        Input("main_height","value"),
        Input("ts_width","value"),
        Input("ts_height","value"),
        Input("profile_width","value"),
        Input("profile_height","value"),
        prevent_initial_call=True
    )
    def apply_manual_layout(trw, trh, mw, mh, tsw, tsh, pw, ph):
        def style(w, h):
            out = {}
            if w is not None:
                out["width"] = f"{int(w)}px"
            if h is not None:
                out["height"] = f"{int(h)}px"
            return out if out else no_update
        return (
            style(trw, trh),
            style(mw, mh),
            style(tsw, tsh),
            style(pw, ph)
        )
        
    # ============================================================
    # === Cruise Track Plot (Latitude vs. Time or Longitude) ===
    # ============================================================
    @app.callback(
        Output("cruise_track", "figure"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("cruise_track_xaxis", "value"),
        Input("cruise_track_yaxis", "value"),
        Input("plot_font_size", "value"),
        Input("sub_sample", "value"),
        Input("sampling_mode", "value"),
        prevent_initial_call=True,
    )
    def draw_cruise_track(dataset, csv_file, xaxis, yaxis, fontsize, sub_sample, sampling_mode):
        trigger = ctx.triggered_id
        if trigger not in (
            "dataset_selector",   
            "csv_selector",
            "cruise_track_xaxis",
            "cruise_track_yaxis",
            "plot_font_size",
            "sub_sample",
            "sampling_mode"
        ):
            raise dash.exceptions.PreventUpdate
        if not csv_file:
            fig = go.Figure()
            fig.add_annotation(
                text="⚠️ No CSV found",
                x=0.5, y=0.5,
                xref="paper", yref="paper",
                showarrow=False
            )
            return fig
        
        df = load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=df[xaxis],
            y=df[yaxis],
            mode="markers",
            marker=dict(size=5, color="blue"),
            meta=df["point_id"].astype(int).tolist(),
            customdata=df["point_id"].astype(int).to_numpy().reshape(-1, 1)
        ))
        fig.update_traces(
            mode="markers",
            selected=dict(marker=dict(color="red")),
            unselected=dict(marker=dict(color="blue"))
        )
        fig.update_layout(
            dragmode="select",
            selectdirection="any",
            clickmode="select",
            uirevision="cruise-track",
            font=dict(size=fontsize if fontsize else 10),
            xaxis=dict(
                title=xaxis.capitalize(),
                rangeslider=dict(visible=False),
                showgrid=True, gridcolor="rgba(0,0,0,0.1)",
                showline=True, linecolor="black", mirror=True
            ),
            yaxis=dict(
                title=yaxis.capitalize(),
                autorange=True,
                fixedrange=False,
                showgrid=True, gridcolor="rgba(0,0,0,0.1)",
                showline=True, linecolor="black", mirror=True
            ),
            paper_bgcolor="white",
            plot_bgcolor="white"
        )
        if xaxis in ["longitude", "latitude"] and yaxis in ["longitude", "latitude"]:
            fig.update_layout(yaxis=dict(scaleanchor="x", scaleratio=1))
        return fig

    # ============================================================
    # === Selections & Range Change Tracking ===
    # ============================================================
    @app.callback(
        Output("cruise_track_selection_store", "data"),
        Input("cruise_track", "selectedData"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("sub_sample", "value"),
        Input("sampling_mode", "value"),
        State("cruise_track_selection_store", "data"),
        prevent_initial_call=True,
    )
    def persist_cruise_track_selection(
        selectedData, dataset, csv_file, sub_sample, sampling_mode, prev
    ):
        if not csv_file:
            raise dash.exceptions.PreventUpdate
        prev = prev or {}
        trigger = ctx.triggered_id
        this_key = f"{dataset}/{csv_file}/{sub_sample}/{sampling_mode}"
        last_key = prev.get("_key")
        if trigger in ("dataset_selector", "csv_selector", "sub_sample", "sampling_mode"):
            if last_key == this_key:
                return prev
            # All observations are selected by default:
            # S = {i : i is a point_id in the active dataframe}.
            return {"mode": "all", "selected_ids": None, "_key": this_key}

        if trigger == "cruise_track":
            if selectedData and selectedData.get("points"):
                ids = [
                    int(p["meta"])
                    for p in selectedData["points"]
                    if p.get("meta") is not None
                ]
                return {"mode": "ids", "selected_ids": ids, "_key": this_key}

            # Clearing the cruise-track selection restores all observations:
            # S = {i : i is a point_id in the active dataframe}.
            return {"mode": "all", "selected_ids": None, "_key": this_key}
        raise dash.exceptions.PreventUpdate

    # --- Callback: Mirror selections between main scatter and TS plots ---
    @app.callback(
        Output("main_plot", "figure", allow_duplicate=True),
        Output("ts_plot", "figure", allow_duplicate=True),
        Input("main_plot", "selectedData"),
        Input("ts_plot", "selectedData"),
        State("main_plot", "figure"),
        State("ts_plot", "figure"),
        prevent_initial_call=True
    )
    def mirror_selection(scatter_sel, ts_sel, fig_scatter, fig_ts):
        trigger = ctx.triggered_id
        patched_scatter = Patch()
        patched_ts = Patch()
        # --- Detect clearing ---
        if trigger == "main_plot" and not scatter_sel:
            for i, _ in enumerate(fig_ts.get("data", [])):
                patched_ts["data"][i]["selectedpoints"] = None
            for i, _ in enumerate(fig_scatter.get("data", [])):
                patched_scatter["data"][i]["selectedpoints"] = None
            return patched_scatter, patched_ts
        if trigger == "ts_plot" and not ts_sel:
            for i, _ in enumerate(fig_scatter.get("data", [])):
                patched_scatter["data"][i]["selectedpoints"] = None
            for i, _ in enumerate(fig_ts.get("data", [])):
                patched_ts["data"][i]["selectedpoints"] = None
            return patched_scatter, patched_ts
        # --- Collect selected IDs ---
        selected_ids = set()
        if trigger == "main_plot" and scatter_sel and scatter_sel.get("points"):
            selected_ids = {
                point_id
                for p in scatter_sel["points"]
                for point_id in [get_point_id_from_event_point(p, fig_scatter)]
                if point_id is not None
            }
        elif trigger == "ts_plot" and ts_sel and ts_sel.get("points"):
            selected_ids = {
                point_id
                for p in ts_sel["points"]
                for point_id in [get_point_id_from_event_point(p, fig_ts)]
                if point_id is not None
            }
        # --- Apply selection to both figures ---
        ids = np.asarray(list(selected_ids), dtype=np.int32)
        for fig, patch in [(fig_scatter, patched_scatter), (fig_ts, patched_ts)]:
            for i, trace in enumerate(fig.get("data", [])):
                if "customdata" not in trace:
                    continue
                custom_ids = [
                    point_id
                    for customdata in trace["customdata"]
                    for point_id in [get_point_id_from_customdata(customdata)]
                    if point_id is not None
                ]
                mask = np.isin(custom_ids, ids)
                selected_idx = np.nonzero(mask)[0]
                patch["data"][i]["selectedpoints"] = (
                    selected_idx.tolist() if len(selected_idx) else None
                )
        return patched_scatter, patched_ts

    # --- Callback: Store selected IDs from main plot (for cross-plot filtering) ---
    @app.callback(
        Output('main_plot_selected_data', 'data'),
        Input('main_plot', 'selectedData'),
        State('main_plot', 'figure'),
        prevent_initial_call=True
    )
    def store_scatter_selection_indices(selectedData, main_figure):
        """Store IDs of selected points in main scatter plot."""
        if not selectedData or "points" not in selectedData:
            return None
        selected_ids = [
            point_id
            for p in selectedData["points"]
            for point_id in [get_point_id_from_event_point(p, main_figure)]
            if point_id is not None
        ]
        return {"selected_ids": selected_ids}

    # # --- Callback: Download CSV file ---
    # @app.callback(
    #     Output('download_dataframe_csv', 'data'),
    #     Input('download-button', 'n_clicks'),
    #     State('dataset_selector', 'value'),
    #     State('csv_selector', 'value'),
    #     prevent_initial_call=True
    # )
    # def download_csv(n_clicks, dataset, csv_file):
    #     '''Download the selected CSV file.'''
    #     if ctx.triggered_id == 'download-button' and csv_file:
    #         df = load_data(dataset,csv_file)
    #         return dcc.send_data_frame(df.to_csv, filename=f'{csv_file}.csv', index=False)
    #     return no_update

    # ============================================================
    # === Main Plot Update: Depth vs. Variable / Coordinate ===
    # ============================================================
    @app.callback(
        Output('main_plot', 'figure'),
        [
            Input('dataset_selector', 'value'),
            Input('csv_selector', 'value'),
            Input('sub_sample', 'value'),
            Input('sampling_mode', 'value'),
            Input('x_axis_variable', 'value'),
            Input('y_axis_variable', 'value'),
            Input('color_variable', 'value'),
            Input('color_map', 'value'),
            Input('size', 'value'),
            Input('v_min', 'value'),
            Input('v_max', 'value'),
            Input('z_min', 'value'),
            Input('z_max', 'value'),
            Input('hidden_opacity', 'value'),
            Input('plot_font_size', 'value'),
            Input('bathymetry', 'value'),
            Input('station', 'value'),
            Input('cruise_track_selection_store', 'data'),
            Input('main_plot', 'relayoutData'),
        ]
    )
    def update_main_plot(
        dataset, csv_file, sub_sample, sampling_mode,
        x_axis, y_axis, color_var, color_map, size,
        vmin, vmax, zmin, zmax,
        hidden_opacity, fontsize, bathymetry, station,
        cruise_track_selection, relayoutData
    ):
        # ------------------------------------------------------------------
        # 1. Ignore irrelevant relayout events
        # ------------------------------------------------------------------
        trigger = ctx.triggered_id
        if trigger == "main_plot" and relayoutData:
            valid_relayout = any(
                k.startswith("xaxis.range")
                or k.startswith("yaxis.range")
                or "autorange" in k
                for k in relayoutData
            )
            if not valid_relayout:
                raise dash.exceptions.PreventUpdate
        # ------------------------------------------------------------------
        # 2. Validate CSV input
        # ------------------------------------------------------------------
        if not csv_file:
            return go.Figure().add_annotation(
                text="No CSV found",
                x=0.5,
                y=0.5,
                showarrow=False
            )
        # ------------------------------------------------------------------
        # 3. Load and optionally filter data
        # ------------------------------------------------------------------
        df = load_data(
            dataset,
            csv_file,
            sub_sample=sub_sample,
            mode=sampling_mode
        )
        # Apply cruise-track point selection, if present.
        if (
            cruise_track_selection
            and cruise_track_selection.get("mode") != "all"
            and cruise_track_selection.get("selected_ids") is not None
        ):
            ids = np.asarray(
                cruise_track_selection["selected_ids"],
                dtype=np.int32
            )
            mask = np.isin(df["point_id"].to_numpy(), ids)
            df = df.loc[mask]
        # Validate that there is plottable data for the requested color variable.
        if df.empty or color_var not in df.columns:
            return go.Figure().add_annotation(
                text=f"No {color_var} data available",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=16, color="red")
            )
        # ------------------------------------------------------------------
        # 4. Resolve color palette and color scaling
        # ------------------------------------------------------------------
        palette, palette_type = get_palette(color_map)
        color_mode = (
            "discrete"
            if is_discrete_variable(df[color_var])
            else "continuous"
        )
        # For numeric color variables, default to robust 5th–95th percentile limits.
        if pd.api.types.is_numeric_dtype(df[color_var]):
            if vmin is None or vmax is None:
                q = df[color_var].quantile([0.05, 0.95])
                vmin = q[0.05] if vmin is None else vmin
                vmax = q[0.95] if vmax is None else vmax
        else:
            palette_type = "discrete"
        fig = go.Figure()
        # ------------------------------------------------------------------
        # 5. Add scatter traces
        # ------------------------------------------------------------------
        if color_mode == "continuous":
            fig.add_trace(
                go.Scattergl(
                    x=df[x_axis],
                    y=df[y_axis],
                    mode="markers",
                    marker=dict(
                        size=size,
                        color=df[color_var],
                        colorscale=palette,
                        cmin=vmin,
                        cmax=vmax,
                        coloraxis="coloraxis"
                    ),
                    customdata=df["point_id"].astype(np.int32).to_numpy(),
                    hovertemplate=(
                        f"{x_axis.capitalize()}: %{{x:.2f}}<br>"
                        f"{y_axis.capitalize()}: %{{y:.2f}}<br>"
                        f"{color_var.replace('_', ' ').capitalize()}: "
                        "%{marker.color:.2f}"
                    ),
                    showlegend=False,
                )
            )
        else:
            df = df.copy()
            # Discrete variables get one trace per class.
            if is_discrete_variable(df[color_var]):
                if pd.api.types.is_numeric_dtype(df[color_var]):
                    df["_color_class"] = (
                        pd.to_numeric(df[color_var], errors="coerce")
                        .round()
                        .astype("Int32")
                    )
                else:
                    df["_color_class"] = df[color_var].astype(str)
                unique_classes = sorted(
                    df["_color_class"].dropna().unique(),
                    key=lambda x: str(x)
                )
                legend_title = color_var.replace("_", " ").capitalize()
                for i, class_value in enumerate(unique_classes):
                    g = df[df["_color_class"] == class_value]
                    color = palette[i % len(palette)]
                    fig.add_trace(
                        go.Scattergl(
                            x=g[x_axis],
                            y=g[y_axis],
                            mode="markers",
                            marker=dict(
                                size=size,
                                color=color
                            ),
                            customdata=np.c_[
                                g["point_id"].astype(np.int32).to_numpy(),
                                g[color_var].to_numpy()
                            ],
                            name=str(class_value),
                            hovertemplate=(
                                f"{x_axis.capitalize()}: %{{x}}<br>"
                                f"{y_axis.capitalize()}: %{{y}}<br>"
                                f"{legend_title}: %{{customdata[1]}}"
                                "<extra></extra>"
                            ),
                            showlegend=True,
                        )
                    )
                fig.update_layout(
                    legend=dict(
                        title=legend_title,
                        orientation="v"
                    )
                )
            else:
                # Float variables remain continuous, even when a qualitative
                # palette was selected. Fall back safely to Viridis.
                palette = px.colors.sequential.Viridis
                palette_type = "continuous"
                fig.add_trace(
                    go.Scattergl(
                        x=df[x_axis],
                        y=df[y_axis],
                        mode="markers",
                        marker=dict(
                            size=size,
                            color=df[color_var],
                            colorscale=palette,
                            cmin=vmin,
                            cmax=vmax,
                            coloraxis="coloraxis"
                        ),
                        customdata=np.c_[
                            df["point_id"].astype(np.int32).to_numpy(),
                            df[color_var].to_numpy()
                        ],
                        hovertemplate=(
                            f"{x_axis.capitalize()}: %{{x}}<br>"
                            f"{y_axis.capitalize()}: %{{y}}<br>"
                            f"{color_var.replace('_', ' ').capitalize()}: "
                            "%{customdata[1]:.2f}"
                            "<extra></extra>"
                        ),
                        showlegend=False,
                    )
                )
        # Shared marker selection styling.
        fig.update_traces(
            marker=dict(size=size),
            selected=dict(marker=dict(opacity=1)),
            unselected=dict(marker=dict(opacity=hidden_opacity))
        )
        # ------------------------------------------------------------------
        # 6. Resolve visible axis ranges and ticks
        # ------------------------------------------------------------------
        visible_xrange = get_visible_range("xaxis", relayoutData)
        visible_yrange = get_visible_range("yaxis", relayoutData)
        # -------------------------
        # X axis
        # -------------------------
        xticks = None
        xticktext = None
        if x_axis == "times":
            x_range = [df["times"].min(), df["times"].max()]
        elif x_axis in df.columns:
            xmin, xmax = resolve_range(visible_xrange, df[x_axis])
            xticks, digits = dynamic_ticks(xmin, xmax, nticks=6)
            x_range = [xmin, xmax]
            if x_axis == "latitude":
                xticktext = [
                    f"{abs(v):.{digits}f}°{'N' if v >= 0 else 'S'}"
                    for v in xticks
                ]
            elif x_axis == "longitude":
                xticktext = [
                    f"{abs(v):.{digits}f}°{'E' if v >= 0 else 'W'}"
                    for v in xticks
                ]
        else:
            x_range = None
            xticks = None
            xticktext = None
        # -------------------------
        # Y axis
        # -------------------------
        yticks = None
        yticktext = None
        ylabel = y_axis.capitalize()
        if y_axis in df.columns:
            if y_axis == "depth":
                ymin, ymax = resolve_range(
                    visible_yrange,
                    df[y_axis],
                    zmin,
                    zmax
                )
                ylabel = "Depth (m)"
                y_range = [ymax, ymin]
            else:
                ymin, ymax = resolve_range(visible_yrange, df[y_axis])
                y_range = [ymin, ymax]
            if y_axis in ["latitude", "longitude"]:
                yticks, digits = dynamic_ticks(ymin, ymax, nticks=6)
                if y_axis == "latitude":
                    yticktext = [
                        f"{abs(v):.{digits}f}°{'N' if v >= 0 else 'S'}"
                        for v in yticks
                    ]
                else:
                    yticktext = [
                        f"{abs(v):.{digits}f}°{'E' if v >= 0 else 'W'}"
                        for v in yticks
                    ]
        else:
            y_range = None
        # ------------------------------------------------------------------
        # 7. Resolve dynamic font size
        # ------------------------------------------------------------------
        if fontsize:
            base_font = fontsize
        elif x_axis == "times" and x_range is not None:
            try:
                span_days = (
                    pd.to_datetime(x_range[1])
                    - pd.to_datetime(x_range[0])
                ).days
                span_days = max(span_days, 1)
                base_font = max(
                    7,
                    min(14, 10 - 0.5 * np.log10(span_days))
                )
            except Exception:
                base_font = 10
        else:
            if x_range is not None and np.all(np.isfinite(x_range)):
                span = abs(x_range[1] - x_range[0])
            else:
                span = 1
            base_font = max(
                7,
                min(14, 10 - np.log10(span + 1e-6))
            )
        # ------------------------------------------------------------------
        # 8. Build base layout
        # ------------------------------------------------------------------
        layout_kwargs = dict(
            dragmode="zoom",
            uirevision=f"{dataset}-{csv_file}",
            font=dict(size=base_font, color="black"),
            paper_bgcolor="white",
            plot_bgcolor="white",
            xaxis=dict(
                title=x_axis.capitalize(),
                range=x_range,
                autorange="reversed" if x_axis == "latitude" else None,
                tickvals=xticks,
                ticktext=xticktext,
                tickmode="array",
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor="rgba(0,0,0,0.1)",
                showline=True,
                linecolor="black",
                mirror=True,
                ticks="outside",
                tickwidth=1,
                tickcolor="black",
                rangeslider=dict(visible=False),
            ),
            yaxis=dict(
                title=ylabel,
                range=y_range,
                tickvals=yticks,
                ticktext=yticktext,
                tickmode="array",
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor="rgba(0,0,0,0.1)",
                showline=True,
                linecolor="black",
                mirror=True,
                ticks="outside",
                tickwidth=1,
                tickcolor="black",
            ),
        )
        # Add shared continuous color axis configuration.
        if color_mode == "continuous":
            layout_kwargs["coloraxis"] = dict(
                colorbar=dict(
                    title=dict(
                        text=(
                            f"{color_var.replace('_', ' ').capitalize()} "
                            f"({get_unit(color_var)})"
                        ),
                        side="bottom",
                        font=dict(size=base_font + 1),
                    ),
                    tickfont=dict(size=base_font * 0.9),
                    orientation="h",
                    x=0.5,
                    xanchor="center",
                    y=0,
                    yanchor="top",
                    ypad=80,
                    lenmode="fraction",
                    len=0.75,
                    thickness=15,
                    ticks="outside",
                    ticklabelposition="outside bottom",
                    tickmode="auto",
                    nticks=5,
                ),
                colorscale=palette,
                cmin=vmin,
                cmax=vmax,
            )
        fig.update_layout(**layout_kwargs)
        # ------------------------------------------------------------------
        # 9. Optional bathymetry overlay
        # ------------------------------------------------------------------
        if (
            "True" in bathymetry
            and bathy is not None
            and x_axis == "latitude"
            and y_axis == "depth"
        ):
            bathy_mask = (
                (bathy["latitude"] <= df["latitude"].max() + 0.01)
                & (bathy["latitude"] >= df["latitude"].min() - 0.01)
            )
            fig.add_trace(
                go.Scatter(
                    x=bathy["latitude"][bathy_mask],
                    y=bathy["bottom_depth_meters"][bathy_mask],
                    mode="lines",
                    line=dict(color="black", width=1),
                    name="Bathymetry",
                    showlegend=False,
                )
            )
        # ------------------------------------------------------------------
        # 10. Optional station overlay
        # ------------------------------------------------------------------
        if (
            "True" in station
            and stations is not None
            and x_axis == "latitude"
            and y_axis == "depth"
        ):
            station_mask = (
                (stations["latitude"] <= df["latitude"].max() + 0.1)
                & (stations["latitude"] >= df["latitude"].min() - 0.1)
            )
            visible_stations = stations[station_mask]
            station_labels = [
                dict(
                    x=lat,
                    y=1,
                    xref="x",
                    yref="paper",
                    text=label,
                    showarrow=False,
                    font=dict(size=base_font),
                    align="center",
                    yshift=35
                )
                for lat, label in zip(
                    visible_stations["latitude"],
                    visible_stations["station"]
                )
            ]
            station_lines = [
                dict(
                    type="line",
                    x0=lat,
                    x1=lat,
                    y0=1,
                    y1=1.02,
                    xref="x",
                    yref="paper",
                    line=dict(color="black", width=1),
                )
                for lat in visible_stations["latitude"]
            ]
            fig.update_layout(
                annotations=station_labels,
                shapes=station_lines
            )
        return fig
    # ============================================================
    # === Time–Salinity (T–S) Diagram Update ===
    # ============================================================
    @app.callback(
        Output('ts_plot', 'figure'),
        [
            Input('dataset_selector', 'value'),
            Input('csv_selector', 'value'),
            Input('sub_sample', 'value'),
            Input('sampling_mode', 'value'),
            Input('ts_color_variable', 'value'),
            Input('ts_color_map', 'value'),
            Input('size', 'value'),
            Input('ts_v_min', 'value'),
            Input('ts_v_max', 'value'),
            Input('hidden_opacity', 'value'),
            Input('plot_font_size', 'value'),
            Input('cruise_track_selection_store', 'data'),
        ]
    )
    def update_ts_plot(dataset, csv_file, sub_sample, sampling_mode,
                    color_var, color_map, size, vmin, vmax,
                    hidden_opacity, fontsize, cruise_track_selection):
        # --------------------------------------------------------
        # 1️⃣ Load Data
        # --------------------------------------------------------
        if not csv_file:
            return go.Figure().add_annotation(
                text="⚠️ No CSV found", x=0.5, y=0.5, showarrow=False
            )
        df = load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
        if 'temperature' not in df.columns or 'salinity' not in df.columns:
            return go.Figure().add_annotation(
                text="⚠️ Temperature or Salinity data missing",
                x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(size=16, color="red")
            )
        if color_var not in df.columns:
            return go.Figure().add_annotation(
                text=f"No {color_var} data available",
                x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(size=16, color="red")
            )
        # --------------------------------------------------------
        # 2️⃣ Apply Cruise Track Selection
        # --------------------------------------------------------
        if (
            cruise_track_selection
            and cruise_track_selection.get("mode") != "all"
            and cruise_track_selection.get("selected_ids") is not None
        ):
            ids = np.asarray(cruise_track_selection["selected_ids"], dtype=np.int32)
            mask = np.isin(df["point_id"].to_numpy(), ids)
            df = df.loc[mask]
        # --------------------------------------------------------
        # 3️⃣ Handle Empty Data
        # --------------------------------------------------------
        if df.empty:
            return go.Figure().add_annotation(
                text=f"⚠️ No {color_var} data available",
                x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(size=16, color="red")
            )
        # --------------------------------------------------------
        # 4️⃣ Compute Density Contours (σθ)
        # --------------------------------------------------------
        tmin, tmax = df['temperature'].quantile([0.01, 0.99]).round().astype(int)
        smin, smax = df['salinity'].quantile([0.01, 0.99]).round().astype(int)
        tmin -= 2
        tmax += 2
        smin -= 2
        smax += 2
        t_values, s_values, D = density_grid(tmin, tmax, smin, smax)
        # --------------------------------------------------------
        # 5️⃣ Configure Color Range
        # --------------------------------------------------------
        if vmin is None or vmax is None:
            q = df[color_var].quantile([0.05, 0.95])
            vmin = q[0.05] if vmin is None else vmin
            vmax = q[0.95] if vmax is None else vmax
        # --------------------------------------------------------
        # 6️⃣ Create T-S Scatter Plot
        # --------------------------------------------------------
        fig = go.Figure()
        fig.add_trace(
            go.Scattergl(
                x=df['salinity'],
                y=df['temperature'],
                mode="markers",
                marker=dict(
                    size=size,
                    color=df[color_var],
                    colorscale=color_map,
                    cmin=vmin,
                    cmax=vmax,
                    coloraxis="coloraxis"
                ),
                # Only send point_id
                customdata=df["point_id"].astype(np.int32).to_numpy(),
                selected=dict(marker=dict(opacity=1)),
                unselected=dict(marker=dict(opacity=hidden_opacity)),
                hovertemplate=(
                    "Salinity: %{x:.2f}<br>"
                    "Temperature: %{y:.2f} °C<br>"
                    f"{color_var}: %{{marker.color:.2f}}<extra></extra>"
                ),
            )
        )
        # --------------------------------------------------------
        # 7️⃣ Dynamic Font Size
        # --------------------------------------------------------
        if fontsize:
            base_font = fontsize
        else:
            span = max(abs(tmax - tmin), abs(smax - smin))
            base_font = max(7, min(14, 9 * (span / 1.0)))
        # --------------------------------------------------------
        # 8️⃣ Add Density Contours
        # --------------------------------------------------------
        fig.add_trace(
            go.Contour(
                z=D,
                x=s_values,
                y=t_values,
                colorscale=[[0, 'black'], [1, 'black']],
                contours=dict(
                    coloring='lines',
                    showlabels=True,
                    labelfont=dict(size=base_font - 1, color='black')
                ),
                line=dict(color='black', width=1),
                hoverinfo='skip',
                showscale=False,
                name="σθ"
            )
        )
        # --------------------------------------------------------
        # 9️⃣ Layout
        # --------------------------------------------------------
        fig.update_layout(
            dragmode="zoom",
            uirevision='keep',
            font=dict(size=base_font, color='black'),
            paper_bgcolor='white',
            plot_bgcolor='white',
            xaxis=dict(
                title='Salinity (psu)',
                range=[smin, smax],
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor='rgba(0, 0, 0, 0.1)',
                showline=True,
                linecolor='black',
                mirror=True,
                ticks='outside'
            ),
            yaxis=dict(
                title='Temperature (°C)',
                range=[tmin, tmax],
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor='rgba(0, 0, 0, 0.1)',
                showline=True,
                linecolor='black',
                mirror=True,
                ticks='outside'
            ),
            coloraxis=dict(
                colorscale=color_map,
                cmin=vmin,
                cmax=vmax,
                colorbar=dict(
                    title=dict(
                        text=f'{color_var.replace("_"," ").capitalize()} ({get_unit(color_var)})',
                        side='bottom',
                        font=dict(size=base_font + 1),
                    ),
                    tickfont=dict(size=base_font * 0.9),
                    orientation='h',
                    x=0.5,
                    xanchor='center',
                    y=0,
                    yanchor='top',
                    ypad=70,
                    lenmode='fraction',
                    len=0.75,
                    thickness=15,
                    ticks='outside',
                    ticklabelposition="outside bottom",
                    tickmode='auto',
                    nticks=5,
                )
            )
        )
        return fig

    # ============================================================
    # === Vertical Profile Plot Update (Depth vs. Variable) ===
    # ============================================================
    @app.callback(
        Output('profile_plot', 'figure'),
        [
            Input('dataset_selector', 'value'),
            Input('csv_selector', 'value'),
            Input('sub_sample', 'value'),
            Input('sampling_mode', 'value'),
            Input('profile_variable', 'value'),   
            Input('profile_color_map', 'value'),
            Input('plot_font_size', 'value'),   
            Input('main_plot_selected_data', 'data'),
            Input('cruise_track_selection_store', 'data'),
        ]
    )
    def update_profile_plot(dataset, csv_file, sub_sample, sampling_mode,
                            color_var, color_map, fontsize,
                            selected_data, cruise_track_selection):
        """
        Update the vertical profile plot.
        Behavior:
            - Cruise track selection is applied first.
            - Main-plot selection expands to full casts when cast exists.
            - If cast exists, plot each cast as a separate profile.
            - Profile colors always use qualitative palettes.
            - If cast does not exist, plot a median profile with percentile envelope.
        """
        fig = go.Figure()
        if not csv_file:
            fig.add_annotation(
                text="No CSV found",
                x=0.5,
                y=0.5,
                showarrow=False,
            )
            return fig
        df = load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
        if not color_var or color_var not in df.columns:
            fig.add_annotation(
                text="No variable selected",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=16, color="red"),
            )
            return fig
        if (
            cruise_track_selection
            and cruise_track_selection.get("mode") != "all"
            and cruise_track_selection.get("selected_ids") is not None
        ):
            ids = np.asarray(cruise_track_selection["selected_ids"], dtype=np.int32)
            mask = np.isin(df["point_id"].to_numpy(), ids)
            df = df.loc[mask]
        selected_ids = None
        if isinstance(selected_data, dict):
            selected_ids = selected_data.get("selected_ids")
        if selected_ids:
            ids = np.asarray(selected_ids, dtype=np.int32)
            if "cast" in df.columns:
                mask = np.isin(df["point_id"].to_numpy(), ids)
                selected_casts = df.loc[mask, "cast"].dropna().unique()
                if len(selected_casts) > 0:
                    cast_mask = np.isin(df["cast"].to_numpy(), selected_casts)
                    df = df.loc[cast_mask]
            else:
                mask = np.isin(df["point_id"].to_numpy(), ids)
                df = df.loc[mask]
        required_cols = ["depth", color_var, "latitude", "longitude"]
        for col in required_cols:
            if col not in df.columns:
                fig.add_annotation(
                    text=f"No {col} data available",
                    x=0.5,
                    y=0.5,
                    xref="paper",
                    yref="paper",
                    showarrow=False,
                    font=dict(size=16, color="red"),
                )
                return fig
        df = df.loc[
            np.isfinite(df["depth"]) &
            np.isfinite(df[color_var]) &
            np.isfinite(df["latitude"]) &
            np.isfinite(df["longitude"])
        ]
        if df.empty:
            fig.add_annotation(
                text=f"No {color_var} data available",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=16, color="red"),
            )
            return fig
        
        if "cast" in df.columns and df["cast"].notna().any():
            df = df.copy()
            cast_numeric = pd.to_numeric(df["cast"], errors="coerce")
            df = df.loc[cast_numeric.notna()].copy()
            if df.empty:
                fig.add_annotation(
                    text="No valid cast data available",
                    x=0.5,
                    y=0.5,
                    xref="paper",
                    yref="paper",
                    showarrow=False,
                    font=dict(size=16, color="red"),
                )
                return fig
            df["cast"] = cast_numeric.loc[df.index].round().astype("Int32")
            unique_casts = sorted(
                df["cast"].dropna().unique(),
                key=lambda x: int(x),
            )
            palette,_ = get_palette(color_map)
            cast_colors = {
                cast_id: palette[i % len(palette)]
                for i, cast_id in enumerate(unique_casts)
            }
            for cast_id, g in df.groupby("cast", dropna=True):
                g = g.sort_values("depth")
                color = cast_colors.get(cast_id, "rgba(0,0,0,0.6)")
                fig.add_trace(
                    go.Scatter(
                        x=g[color_var],
                        y=g["depth"],
                        mode="lines+markers",
                        line=dict(color=color, width=2),
                        marker=dict(size=4, color=color),
                        name=f"Cast {int(cast_id)}",
                        customdata=np.c_[g["latitude"], g["longitude"]],
                        hovertemplate=(
                            f"<b>Cast:</b> {int(cast_id)}<br>"
                            "<b>Depth:</b> %{y:.1f} m<br>"
                            f"<b>{color_var.replace('_', ' ').capitalize()}:</b> "
                            f"%{{x:.2f}} {get_unit(color_var)}<br>"
                            "<b>Latitude:</b> %{customdata[0]:.4f}<br>"
                            "<b>Longitude:</b> %{customdata[1]:.4f}<br>"
                            "<extra></extra>"
                        ),
                    )
                )
        else:
            step = 2
            depth_bin = np.floor((df["depth"] + step / 2) / step) * step
            summary = (
                df.assign(_depth_bin=depth_bin)
                .groupby("_depth_bin")
                .agg(
                    depth=("depth", "median"),
                    median=(color_var, "median"),
                    q05=(color_var, lambda x: np.nanpercentile(x, 5)),
                    q95=(color_var, lambda x: np.nanpercentile(x, 95)),
                    latitude=("latitude", "median"),
                    longitude=("longitude", "median"),
                )
                .reset_index(drop=True)
                .sort_values("depth")
            )
            fig.add_trace(
                go.Scatter(
                    x=np.concatenate([summary["q05"], summary["q95"][::-1]]),
                    y=np.concatenate([summary["depth"], summary["depth"][::-1]]),
                    fill="toself",
                    fillcolor="rgba(0,100,200,0.2)",
                    line=dict(color="rgba(0,0,0,0)"),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=summary["median"],
                    y=summary["depth"],
                    mode="lines+markers",
                    line=dict(color="blue", width=2),
                    marker=dict(size=5, color="blue"),
                    showlegend=False,
                    customdata=summary[["latitude", "longitude"]].to_numpy(),
                    hovertemplate=(
                        "<b>Depth:</b> %{y:.1f} m<br>"
                        f"<b>{color_var.replace('_', ' ').capitalize()}:</b> "
                        f"%{{x:.2f}} {get_unit(color_var)}<br>"
                        "<b>Latitude:</b> %{customdata[0]:.4f}<br>"
                        "<b>Longitude:</b> %{customdata[1]:.4f}<br>"
                        "<extra></extra>"
                    ),
                )
            )
        fig.update_layout(
            dragmode="zoom",
            paper_bgcolor="white",
            plot_bgcolor="white",
            font=dict(
                color="black",
                size=fontsize if fontsize else 10,
            ),
            legend=dict(title="Cast"),
        )
        fig.update_yaxes(
            autorange="reversed",
            title="Depth (m)",
            showgrid=True,
            gridcolor="rgba(0, 0, 0, 0.1)",
            showline=True,
            linecolor="black",
            linewidth=1,
            mirror=True,
            ticks="outside",
            tickwidth=1,
            tickcolor="black",
        )
        fig.update_xaxes(
            title=f"{color_var.replace('_', ' ').capitalize()} ({get_unit(color_var)})",
            showgrid=True,
            gridcolor="rgba(0, 0, 0, 0.1)",
            showline=True,
            linecolor="black",
            linewidth=1,
            mirror=True,
            ticks="outside",
            tickwidth=1,
            tickcolor="black",
        )
        return fig

    # ============================================================s
    # === Display Clicked Point Details (from Main Plot) ===
    # ============================================================
    @app.callback(
        Output('click-output', 'children'),
        Input('main_plot', 'clickData'),
        State('main_plot', 'figure'),
        State('dataset_selector', 'value'),
        State('csv_selector', 'value'),
        State('sub_sample', 'value'),
        State('sampling_mode', 'value'),
    )
    def display_click_data(clickData, main_figure, dataset, csv_file, sub_sample, sampling_mode):
        """
        Display detailed information for a clicked point in the main scatter plot.
        Fetches the full row server-side using point_id.
        """
        if not clickData or not clickData.get("points"):
            return "Click on a point to see full details."
        try:
            clicked_point = clickData["points"][0]
            point_id = get_point_id_from_event_point(clicked_point, main_figure)

            if point_id is None:
                return "Click on a data point to see full details."

            df = load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
            row = get_row_by_point_id(df, point_id)

            if row is None:
                return "Point no longer in filtered dataset."

            def clean(value, default=None):
                if value is None or value == "":
                    return default
                if pd.isna(value):
                    return default
                return value
            def format_number(value, suffix="", precision=2):
                value = clean(value)
                if value is None:
                    return "N/A"
                try:
                    return f"{float(value):.{precision}f}{suffix}"
                except (TypeError, ValueError):
                    return "N/A"
            def format_time(value):
                value = clean(value)
                if value is None:
                    return "N/A"
                timestamp = pd.to_datetime(value, errors="coerce")
                if pd.isna(timestamp):
                    return "N/A"
                return timestamp.strftime("%Y-%m-%d %H:%M:%S")
            def make_media_link(media_value, frame_value):
                media_value = clean(media_value)
                frame_value = clean(frame_value)
                if media_value is None or frame_value is None:
                    return "N/A", None
                try:
                    frame_int = int(frame_value)
                except (TypeError, ValueError):
                    return str(media_value), None
                label = f"{media_value}_{frame_int}"
                href = get_link(media_value, frame_int)
                return label, href
            def info_row(label, value):
                return html.Div(
                    [
                        html.Span(label),
                        html.Span(value),
                    ],
                    style={"display": "flex", "justify-content": "space-between"},
                )
            def link_row(label, link_text, href):
                value = (
                    html.A(
                        link_text,
                        href=href,
                        target="_blank",
                        style={"flex": "7", "text-align": "right"},
                    )
                    if href
                    else html.Span(
                        link_text,
                        style={"flex": "7", "text-align": "right"},
                    )
                )
                return html.Div(
                    [
                        html.Span(label, style={"flex": "3"}),
                        value,
                    ],
                    style={"display": "flex", "justify-content": "space-between"},
                )
            def format_sensor_value(value):
                value = clean(value, "N/A")
                if value == "N/A":
                    return value
                if isinstance(value, (int, float, np.integer, np.floating)):
                    if abs(value) >= 1e6:
                        return f"{value:.2e}"
                    if abs(value) < 1 and value != 0:
                        return f"{value:.4f}"
                    return f"{value:,.2f}"
                return str(value)
            media_1_label, media_1_link = make_media_link(row.get("media"), row.get("frame"))
            media_2_label, media_2_link = make_media_link(row.get("media_2"), row.get("frame_2"))
            meta_cols = {
                "point_id",
                "media", "frame", "media_path", "id", "link",
                "media_2", "frame_2", "media_path_2", "id_2", "link_2",
                "times", "latitude", "longitude", "depth", "timestamp", "matdate",
            }
            variable_details = []
            for var, value in row.items():
                if var in meta_cols or var.endswith("_std"):
                    continue
                variable_details.append(
                    html.Div(
                        [
                            html.Span(
                                f"📈 {var.replace('_', ' ').capitalize()} ({get_unit(var)}):",
                                style={"flex": "7", "text-align": "left"},
                            ),
                            html.Span(
                                format_sensor_value(value),
                                style={"flex": "3", "text-align": "right"},
                            ),
                        ],
                        style={
                            "display": "flex",
                            "justify-content": "space-between",
                            "width": "100%",
                        },
                    )
                )
                
            def image_block(title, label, href, key_suffix="img"):
                if not href:
                    return info_row(title, "N/A")

                sep = "&" if "?" in href else "?"
                img_src = f"{href}{sep}_cb={point_id}_{key_suffix}"

                return html.Div(
                    [
                        html.Div(
                            [
                                html.Div(title, style={"fontWeight": "bold"}),
                                html.A(label, href=href, target="_blank"),
                            ],
                            style={"marginBottom": "6px"},
                        ),

                        html.Img(
                            src=img_src,
                            key=f"img-{point_id}-{key_suffix}",
                            style={
                                "width": "100%",
                                "maxWidth": "320px",
                                "minHeight": "180px",
                                "objectFit": "contain",
                                "borderRadius": "6px",
                                "border": "1px solid #ccc",
                                "marginBottom": "12px",
                                "backgroundColor": "#f5f5f5",
                            },
                        ),
                    ],
                    key=f"image-block-{point_id}-{key_suffix}",
                )

            return html.Div(
                [
                    image_block("📽️ ISIIS 1:", media_1_label, media_1_link, "isiis1"),
                    image_block("📽️ ISIIS 2:", media_2_label, media_2_link, "isiis2"),

                    info_row("⏳ Time:", format_time(row.get("times"))),
                    info_row("🌍 Latitude:", format_number(row.get("latitude"), "°")),
                    info_row("🌍 Longitude:", format_number(row.get("longitude"), "°")),
                    info_row("🌊 Depth:", format_number(row.get("depth"), " m")),

                    html.Hr(),

                    *variable_details,
                ]
            )
        except Exception as e:
            return f"⚠️ Error processing click data: {str(e)}"

# ============================================
# App factory, CLI, and WSGI entrypoints
# ============================================

def cli(argv: list[str] | None = None):
    import argparse
    parser = argparse.ArgumentParser(description="Stingray Dashboard Server")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--work-dir", type=str, default=None)

    return parser.parse_args(argv)


def resolve_assets_dir() -> Path:
    """
    Locate the packaged Dash assets directory.

    Install invariant:
      assets_dir = package_dir / "assets"

    This keeps the dashboard static files coupled to the Python package, so a
    Git or wheel install carries the code and visual assets together.  The
    current working directory is only checked as a development fallback.
    """
    here = Path(__file__).resolve()
    search_roots = [here.parent, Path.cwd()]

    for root in search_roots:
        candidate = root / "assets"
        if candidate.is_dir():
            return candidate

    return Path.cwd() / "assets"


def create_app(work_dir: str | Path | None = None) -> dash.Dash:
    """
    Build and configure the Dash application.
    """
    init_data_dirs(work_dir)
    load_auxiliary_data()

    dash_app = dash.Dash(
        __name__,
        assets_folder=str(resolve_assets_dir()),
        assets_url_path="/assets",
    )

    dash_app.layout = make_layout()
    register_callbacks(dash_app)

    return dash_app

def main(argv: list[str] | None = None) -> None:
    args = cli(argv)
    app = create_app(work_dir=args.work_dir)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)

application = create_app().server

if __name__ == "__main__":
    main()
