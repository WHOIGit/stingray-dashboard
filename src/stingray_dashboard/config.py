from __future__ import annotations

import os
import re

DEFAULT_SUBSAMPLE = None
DEFAULT_MAX_TIME_GAP_SEC = 300  # 5 minutes; tune per platform
MAX_WORKERS = max(1, min(os.cpu_count() - 1, 8))

meta_vars = [
    "timestamp", "times", "matdate",
    "latitude", "longitude", "depth",
    "media", "frame", "media_1", "frame_1",
]

# media/frame (first camera) or media_<stream>/frame_<stream>; a stream is a
# camera name or an older number.
MEDIA_VAR_RE = re.compile(r"^(?:media|frame)(?:_.+)?$")


def is_meta_var(name: str) -> bool:
    return name in meta_vars or bool(MEDIA_VAR_RE.fullmatch(name))

DEFAULT_DATASET = os.getenv("STINGRAY_DEFAULT_DATASET", "")
DOWNLOADS_ENABLED = (
    os.getenv("STINGRAY_DASHBOARD_DOWNLOADS_ENABLED", "true").lower()
    not in {"0", "false", "no", "off"}
)


def set_default_dataset(dataset: str | None) -> None:
    global DEFAULT_DATASET
    DEFAULT_DATASET = dataset or ""
    for param in URL_SYNCED_PARAMS:
        if param["key"] == "dataset":
            param["default"] = DEFAULT_DATASET
            break


def choose_default_dataset(datasets):
    if not datasets:
        return None
    return DEFAULT_DATASET if DEFAULT_DATASET in datasets else datasets[0]
    
# ============================================
# Units
# ============================================
# Keep the units below identical to stingraytools' units.yaml (src/stingray/
# config/defaults/units.yaml), which sets the unit of each merged sensor
# column. "ind m⁻³" is dashboard-only: abundance columns are named by class.
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

# ============================================
# Sensor aliases
# ============================================
# Instrument exports keep their own column names (e.g. SeaBird t090c, sal00).
# Columns are not renamed or copied; code that needs a quantity looks it up
# here. Order matters: the first name present in a file wins, so primary
# sensors come before secondary ones. The first name of each entry is the
# output column name stingraytools writes (its columns.yaml); the rest are
# other instruments' names for the same quantity.
SENSOR_ALIASES = {
    "temperature": ["temperature", "t090c", "t090", "t190c", "t190"],
    "salinity": ["salinity", "sal00", "sal11"],
    "chlorophyll": ["chlorophyll", "fleco_afl", "chl", "fluor"],
    "pressure": ["pressure", "prdm", "prd", "press"],
    "backscattering": ["backscattering", "bb", "bbp"],
}


# Variables listed first in every plot dropdown, in this order. Each is matched
# by quantity (see SENSOR_ALIASES) or by name prefix (oxygen -> oxygen_*).
VARIABLE_PRIORITY = [
    "chlorophyll", "backscattering", "par", "oxygen", "nitrate",
    "temperature", "salinity", "density",
]
# Technical columns listed last.
VARIABLE_LAST = {"cast", "deployment", "altitude", "pitch", "roll", "heading"}
VARIABLE_LAST_PREFIXES = ("velocity_",)


def variable_rank(name: str) -> tuple[int, int]:
    """Sort key for dropdown order: priority variables, the rest, technical."""
    lname = name.lower()
    for index, quantity in enumerate(VARIABLE_PRIORITY):
        if lname in SENSOR_ALIASES.get(quantity, [quantity]) or lname == quantity \
                or lname.startswith(quantity + "_"):
            return (0, index)
    if lname in VARIABLE_LAST or lname.startswith(VARIABLE_LAST_PREFIXES):
        return (2, 0)
    return (1, 0)


def find_alias(columns, quantity):
    """Column in ``columns`` that holds ``quantity``, or None."""
    present = set(columns)
    for name in SENSOR_ALIASES.get(quantity, [quantity]):
        if name in present:
            return name
    return None


def get_unit(varname):
    vn = varname.lower()
    # A recognised alias takes the unit of its quantity (t090c -> °C).
    for quantity, names in SENSOR_ALIASES.items():
        if vn in names and vn != quantity:
            return get_unit(quantity)
    # split variable name into tokens
    tokens = vn.replace("-", "_").split("_")
    for unit, pats in unit_patterns.items():
        for p in pats:
            if p in tokens:
                return unit
    return ""

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
    {"key": "zmin", "id": "z_min", "default": None, "type": "float"},
    {"key": "zmax", "id": "z_max", "default": None, "type": "float"},
    {"key": "latmin", "id": "lat_min", "default": None, "type": "float"},
    {"key": "latmax", "id": "lat_max", "default": None, "type": "float"},
    {"key": "lonmin", "id": "lon_min", "default": None, "type": "float"},
    {"key": "lonmax", "id": "lon_max", "default": None, "type": "float"},
    {"key": "vmin", "id": "v_min", "default": None, "type": "float", "write": False},
    {"key": "vmax", "id": "v_max", "default": None, "type": "float", "write": False},
    {"key": "opacity", "id": "hidden_opacity", "default": 0.05, "type": "float"},
    {"key": "bathymetry", "id": "bathymetry", "default": ["True"], "type": "list"},
    {"key": "station", "id": "station", "default": ["True"], "type": "list"},
    {"key": "dmark", "id": "detail_marker", "default": ["True"], "type": "list"},
    {"key": "dmarkcolor", "id": "detail_marker_color", "default": "#e00000", "type": "string"},
    # TS plot
    {"key": "tsvar", "id": "ts_color_variable", "default": "chlorophyll", "type": "string"},
    {"key": "tsmap", "id": "ts_color_map", "default": "Viridis", "type": "string"},
    {"key": "tsvmin", "id": "ts_v_min", "default": None, "type": "float", "write": False},
    {"key": "tsvmax", "id": "ts_v_max", "default": None, "type": "float", "write": False},
    # profile plot
    {"key": "profilevar", "id": "profile_variable", "default": "chlorophyll", "type": "string"},
    {"key": "profilemap", "id": "profile_color_map", "default": "Plotly", "type": "string"},
    # scatter plot
    {"key": "sx", "id": "scatter_x", "default": None, "type": "string", "restore_missing": False},
    {"key": "sy", "id": "scatter_y", "default": None, "type": "string", "restore_missing": False},
    {"key": "scolor", "id": "scatter_color", "default": "depth", "type": "string"},
    {"key": "smap", "id": "scatter_color_map", "default": "Turbo", "type": "string"},
    {"key": "sfit", "id": "scatter_fit_degree", "default": 0, "type": "int"},
    {"key": "sfitsel", "id": "scatter_fit_selected", "default": [], "type": "list"},
    {"key": "sfitcolor", "id": "scatter_fit_color", "default": "#d62728", "type": "string"},
    {"key": "svmin", "id": "scatter_v_min", "default": None, "type": "float", "write": False},
    {"key": "svmax", "id": "scatter_v_max", "default": None, "type": "float", "write": False},
    {"key": "sxmin", "id": "scatter_x_min", "default": None, "type": "float"},
    {"key": "sxmax", "id": "scatter_x_max", "default": None, "type": "float"},
    {"key": "symin", "id": "scatter_y_min", "default": None, "type": "float"},
    {"key": "symax", "id": "scatter_y_max", "default": None, "type": "float"},
    # sampling / averaging
    {"key": "sampling", "id": "sampling_mode", "default": "subsample", "type": "string"},
    {"key": "subsample", "id": "sub_sample", "default": DEFAULT_SUBSAMPLE, "type": "int"},
    {"key": "maxgap", "id": "max_gap_seconds", "default": DEFAULT_MAX_TIME_GAP_SEC, "type": "int"},
    # layout
    {"key": "trackw", "id": "track_width", "default": 900, "type": "int"},
    {"key": "trackh", "id": "track_height", "default": 300, "type": "int"},
    {"key": "mainw", "id": "main_width", "default": 900, "type": "int"},
    {"key": "mainh", "id": "main_height", "default": 400, "type": "int"},
    {"key": "tsw", "id": "ts_width", "default": 400, "type": "int"},
    {"key": "tsh", "id": "ts_height", "default": 400, "type": "int"},
    {"key": "profilew", "id": "profile_width", "default": 300, "type": "int"},
    {"key": "profileh", "id": "profile_height", "default": 450, "type": "int"},
    {"key": "scatterw", "id": "scatter_width", "default": 400, "type": "int"},
    {"key": "scatterh", "id": "scatter_height", "default": 400, "type": "int"},
    # global text
    {"key": "fontsize", "id": "plot_font_size", "default": 14, "type": "int"},
]
