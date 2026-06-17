from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DEFAULT_MAX_TIME_GAP_SEC

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

stations: pd.DataFrame | None = None
bathy: pd.DataFrame | None = None

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
