from __future__ import annotations

import os

DEFAULT_SUBSAMPLE = None
DEFAULT_MAX_TIME_GAP_SEC = 300  # 5 minutes; tune per platform
MAX_WORKERS = max(1, min(os.cpu_count() - 1, 8))

meta_vars = [
    "timestamp", "times", "matdate",
    "latitude", "longitude", "depth",
    "media", "media_path", "frame", "id", "link",
    "media_2", "media_path_2", "frame_2", "id_2", "link_2"
]

DEFAULT_DATASET = os.getenv("STINGRAY_DEFAULT_DATASET", "")


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
    {"key": "trackw", "id": "track_width", "default": 900, "type": "int"},
    {"key": "trackh", "id": "track_height", "default": 320, "type": "int"},
    {"key": "mainw", "id": "main_width", "default": 900, "type": "int"},
    {"key": "mainh", "id": "main_height", "default": 620, "type": "int"},
    {"key": "tsw", "id": "ts_width", "default": 560, "type": "int"},
    {"key": "tsh", "id": "ts_height", "default": 560, "type": "int"},
    {"key": "profilew", "id": "profile_width", "default": 520, "type": "int"},
    {"key": "profileh", "id": "profile_height", "default": 780, "type": "int"},
    # global text
    {"key": "fontsize", "id": "plot_font_size", "default": 14, "type": "int"},
]
