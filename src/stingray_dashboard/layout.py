from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.express as px
from dash import dcc, html

from . import data
from .config import (
    DEFAULT_MAX_TIME_GAP_SEC,
    DEFAULT_SUBSAMPLE,
    choose_default_dataset,
    meta_vars,
)

# ========================
# App Layout 
# ========================
def make_layout() -> html.Div:
    datasets = data.scan_datasets()
    selected_dataset = choose_default_dataset(datasets)
    csv_files = data.get_csv_files(selected_dataset) if selected_dataset else []
    if csv_files:
        # Sample only enough rows to canonicalize names while preserving all
        # CSV columns for dropdown choices.
        csv_path = data.DATA_DIR / selected_dataset / f"{csv_files[-1]}.csv"
        df = pd.read_csv(csv_path, nrows=1000, low_memory=True)
        df = data.canonicalize_columns(df)
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
        dcc.Interval(id="file-scan-interval", interval=1800 * 1000, n_intervals=0),
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
        # ===== GLOBAL CONTROLS =====
        html.Div([
            html.Div([
                html.Div([
                    html.Div([
                        html.Label('Dataset:'),
                        dcc.Dropdown(
                            id='dataset_selector',
                            options=[{'label': f, 'value': f} for f in datasets],
                            value=selected_dataset,
                            clearable=False
                        ),
                    ], className='control-field'),
                    html.Div([
                        html.Label('Data file:'),
                        dcc.Dropdown(
                            id='csv_selector',
                            options=[{'label': f, 'value': f} for f in csv_files],
                            value=csv_files[-1] if csv_files else None,
                            clearable=False
                        ),
                    ], className='control-field'),
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
                    ], className='control-field'),
                    html.Div([
                        html.Label('Bin size (N points):'),
                        dcc.Input(id='sub_sample', type='number', value=DEFAULT_SUBSAMPLE, placeholder="Auto", debounce=True)
                    ], className='control-field'),
                    html.Div([
                        html.Label('Max time gap (s):'),
                        dcc.Input(id='max_gap_seconds', type='number', value=DEFAULT_MAX_TIME_GAP_SEC, debounce=True)
                    ], className='control-field'),
                    html.Div([
                        html.Label('Opacity:'),
                        dcc.Input(id='hidden_opacity', type='number', value=0.1, debounce=True)
                    ], className='control-field'),
                    html.Div([
                        html.Label('Size:'),
                        dcc.Input(id='size', type='number', value=5, debounce=True)
                    ], className='control-field'),
                    html.Div([
                        html.Label('Font:'),
                        dcc.Input(id='plot_font_size', type='number', value=14, debounce=True)
                    ], className='control-field'),
                    html.Button('Refresh list', id='refresh-button'),
                ], className='global-fields'),
            ], className='panel global-panel options-panel'),
        ], className='global-controls'),
        # ===== MAIN BODY =====
        html.Div([
            html.Div([
                html.Div([
                    html.Div([
                        html.Label('CRUISE TRACK', className='section-label'),
                        html.Div([
                            html.Label('X-axis:'),
                            dcc.Dropdown(
                                id='cruise_track_xaxis',
                                options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                                value='times'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Y-axis:'),
                            dcc.Dropdown(
                                id='cruise_track_yaxis',
                                options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                                value='latitude'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Track W:'),
                            dcc.Input(id='track_width', type='number', value=900, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Track H:'),
                            dcc.Input(id='track_height', type='number', value=320, debounce=True)
                        ], className='control-field'),
                    ], className='panel plot-controls'),
                    html.Div([
                        dcc.Graph(
                            id='cruise_track',
                            responsive=True,
                            style={"width": "100%", "height": "100%"}
                        )
                    ], id='track_container', className='cruise-track-graph plot-area'),
                    html.Div(className='right-slot'),
                ], className='plot-row'),
                html.Div([
                    html.Div([
                        html.Label('TRANSECT PLOT', className='section-label'),
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
                            html.Label('X-axis:'),
                            dcc.Dropdown(
                                id='x_axis_variable',
                                options=[{'label': var.capitalize(), 'value': var}
                                        for var in ['latitude', 'longitude', 'times']],
                                value='latitude'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Y-axis:'),
                            dcc.Dropdown(
                                id='y_axis_variable',
                                options=[{'label': var.capitalize(), 'value': var}
                                        for var in ['depth','latitude']],
                                value='depth'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Color Variable:'),
                            dcc.Dropdown(
                                id='color_variable',
                                options=[{'label': var.capitalize(), 'value': var} for var in sensor_vars],
                                value=default_color_var
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Colormap:'),
                            dcc.Dropdown(
                                id='color_map',
                                options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                                value='Jet'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Min Depth:'),
                            dcc.Input(id='z_min', type='number', value=0, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Max Depth:'),
                            dcc.Input(id='z_max', type='number', value=200, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Color Min:'),
                            dcc.Input(id='v_min', type='number', debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Color Max:'),
                            dcc.Input(id='v_max', type='number', debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Main W:'),
                            dcc.Input(id='main_width', type='number', value=900, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Main H:'),
                            dcc.Input(id='main_height', type='number', value=620, debounce=True)
                        ], className='control-field'),
                    ], className='panel plot-controls'),
                    html.Div([
                        dcc.Graph(
                            id='main_plot',
                            responsive=True,
                            style={"width": "100%", "height": "100%"}
                        )
                    ], id='main_container', className='main-graph plot-area'),
                    html.Div([
                        html.Label('Details:', className='section-label', style={'font-size': '16px'}),
                        html.Div(id='click-output', className='card', style={
                            'font-size': '13px',
                            'line-height': '1.4em'
                        }),
                    ], className='right-panel right-slot'),
                ], className='plot-row'),
                html.Div([
                    html.Div([
                        html.Label('T-S PLOT', className='section-label'),
                        html.Div([
                            html.Label('Color Variable:'),
                            dcc.Dropdown(
                                id='ts_color_variable',
                                options=[{'label': var.capitalize(), 'value': var}
                                        for var in sensor_vars if var not in ['temperature', 'salinity']],
                                value=default_ts_var
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Colormap:'),
                            dcc.Dropdown(
                                id='ts_color_map',
                                options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                                value='Viridis'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Color Min:'),
                            dcc.Input(id='ts_v_min', type='number', debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Color Max:'),
                            dcc.Input(id='ts_v_max', type='number', debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('TS W:'),
                            dcc.Input(id='ts_width', type='number', value=560, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('TS H:'),
                            dcc.Input(id='ts_height', type='number', value=560, debounce=True)
                        ], className='control-field'),
                    ], className='panel plot-controls'),
                    html.Div([
                        dcc.Graph(
                            id='ts_plot',
                            responsive=True,
                            style={"width": "100%", "height": "100%"}
                        )
                    ], id='ts_container', className='ts-graph plot-area'),
                    html.Div(className='right-slot'),
                ], className='plot-row'),
                html.Div([
                    html.Div([
                        html.Label('PROFILE PLOT', className='section-label'),
                        html.Div([
                            html.Label('Variable:'),
                            dcc.Dropdown(
                                id='profile_variable',
                                options=[{'label': var.capitalize(), 'value': var}
                                        for var in sensor_vars],
                                value=default_profile_var
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Colormap:'),
                            dcc.Dropdown(
                                id='profile_color_map',
                                options=[{'label': cmap, 'value': cmap} for cmap in qualitative_maps],
                                value='Plotly'
                            )
                        ], className='control-field'),
                        html.Div([
                            html.Label('Profile W:'),
                            dcc.Input(id='profile_width', type='number', value=520, debounce=True)
                        ], className='control-field'),
                        html.Div([
                            html.Label('Profile H:'),
                            dcc.Input(id='profile_height', type='number', value=780, debounce=True)
                        ], className='control-field'),
                    ], className='panel plot-controls'),
                    html.Div([
                        dcc.Graph(
                            id='profile_plot',
                            responsive=True,
                            style={"width": "100%", "height": "100%"}
                        )
                    ], id='profile_container', className='profile-graph plot-area'),
                    html.Div(className='right-slot'),
                ], className='plot-row'),
                dcc.Store(id='cruise_track_selected_data'),
                dcc.Store(id='cruise_track_selection_store', data={"selected_ids": None}),
                dcc.Store(id='main_plot_selected_data'),
                # Compact trace-wise point-ID arrays support cross-plot
                # selection without sending complete Plotly figures to Python.
                dcc.Store(id='main_plot_point_ids', data=[]),
                dcc.Store(id='ts_plot_point_ids', data=[]),
            ], className='plot-workspace'),
        ], className='dashboard-body'),
        # ===== FOOTER =====
        html.Div([
            html.Span('Developed by: Anh Pham, Sidney Batchelder, Joe Futrelle, Heidi Sosik'),
            html.Span('anh.pham@whoi.edu')
        ], className='footer')
    ])

# ============================================================
# === URL Synchronization & Dataset Management Callbacks ===
# ============================================================
