from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
from dash import dcc, html

from . import data
from .config import (
    DEFAULT_MAX_TIME_GAP_SEC,
    DEFAULT_SUBSAMPLE,
    DOWNLOADS_ENABLED,
    choose_default_dataset,
    find_alias,
)


def _read_project_version() -> str:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue

        in_project = False
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped == "[project]":
                in_project = True
                continue
            if in_project and stripped.startswith("["):
                break
            if in_project and stripped.startswith("version"):
                return stripped.split("=", 1)[1].strip().strip('"')

    return "dev"


# Prefer pyproject.toml when running from a source checkout: an editable
# install's metadata keeps the version from install time and goes stale.
DASHBOARD_VERSION = _read_project_version()
if DASHBOARD_VERSION == "dev":
    try:
        DASHBOARD_VERSION = version("stingray-dashboard")
    except PackageNotFoundError:
        pass


# ========================
# Layout helpers
# ========================
def _inline_field(label: str, control, narrow: bool = False) -> html.Div:
    """Label and control on one line for a plot toolbar."""
    control_class = 'toolbar-control toolbar-control-narrow' if narrow else 'toolbar-control'
    return html.Div(
        [html.Label(label), html.Div(control, className=control_class)],
        className='toolbar-field',
    )


def _select_field(label: str, control) -> html.Div:
    """Label above a dropdown inside a More popover."""
    return html.Div([html.Label(label), control], className='control-field')


def _range_field(label, min_id, max_id, min_value=None, max_value=None) -> html.Div:
    """Min/max pair sharing one row."""
    return html.Div([
        html.Label(f'{label} range:'),
        html.Div([
            dcc.Input(id=min_id, type='number', value=min_value, placeholder='min', debounce=True),
            html.Span('–', className='range-sep'),
            dcc.Input(id=max_id, type='number', value=max_value, placeholder='max', debounce=True),
        ], className='range-inputs'),
    ], className='control-field')


def _size_fields(width_id, width, height_id, height) -> html.Div:
    return html.Div([
        html.Label('Size W × H (px):'),
        html.Div([
            dcc.Input(id=width_id, type='number', value=width, debounce=True),
            html.Span('×', className='range-sep'),
            dcc.Input(id=height_id, type='number', value=height, debounce=True),
        ], className='range-inputs'),
    ], className='control-field')


def _plot_card(title, primary, more, graph_id, container_id, card_class='',
               aside=None) -> html.Div:
    """Plot with a one-line toolbar; secondary options live in a popover.

    ``aside`` is an optional panel shown beside the plot.
    """
    plot = html.Div([
        dcc.Graph(
            id=graph_id,
            responsive=True,
            style={"width": "100%", "height": "100%"}
        )
    ], id=container_id, className='plot-area')
    return html.Div([
        html.Div([
            html.Span(title, className='section-label'),
            *primary,
            html.Details([
                html.Summary('⚙ More', title='More plot options'),
                html.Div(more, className='more-panel'),
            ], className='more-menu'),
        ], className='plot-toolbar'),
        html.Div([plot, aside], className='plot-body') if aside is not None else plot,
    ], className=f'panel plot-card {card_class}'.strip())


def default_scatter_axes(numeric_vars):
    """Pick default scatter X/Y variables from the numeric columns."""
    x = find_alias(numeric_vars, 'chlorophyll') or (numeric_vars[0] if numeric_vars else None)
    y_candidates = [v for v in numeric_vars if v != x]
    y = (
        find_alias(y_candidates, 'backscattering')
        or find_alias(y_candidates, 'salinity')  # e.g. CTD files without backscatter
        or (y_candidates[0] if y_candidates else None)
    )
    return x, y


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
        df = data.coerce_numeric_columns(data.canonicalize_columns(df))
    else:
        df = pd.DataFrame()
    # Numeric columns only, the same rule the callbacks use.
    sensor_vars = data.plot_variables(df) if not df.empty else []
    profile_vars = sensor_vars
    # Defaults found through sensor aliases (temperature may be t090c, ...).
    temp_col = find_alias(sensor_vars, "temperature")
    sal_col = find_alias(sensor_vars, "salinity")
    default_color_var = temp_col or (sensor_vars[0] if sensor_vars else None)
    ts_candidates = [v for v in sensor_vars if v not in (temp_col, sal_col)]
    default_ts_var = find_alias(ts_candidates, "chlorophyll") or (ts_candidates[0] if ts_candidates else None)
    default_profile_var = temp_col or (profile_vars[0] if profile_vars else None)
    scatter_vars = profile_vars + [
        col for col in ['depth', 'latitude', 'longitude'] if col in df.columns
    ]
    default_scatter_x, default_scatter_y = default_scatter_axes(scatter_vars)
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
                        style={'width': '140px'}, title='LTER Network Homepage'),
                href="https://lternet.edu/", target="_blank"
            )
        ], className='header-top flex-row'),
        # ===== SUB HEADER =====
        html.Div([
            html.Span('STINGRAY DASHBOARD', className='header-title'),
            html.A(
                html.Img(src='/assets/NES-LTER-horizontal.png',
                        # Same width as the LTER logo; cover-crops the image's
                        # transparent top/bottom padding so the header stays slim.
                        style={'width': '140px', 'height': '20px', 'objectFit': 'cover'},
                        title='NES-LTER Homepage'),
                href="https://nes-lter.whoi.edu/", target="_blank"
            )
        ], className='header-sub flex-row'),
        # ===== GLOBAL CONTROLS =====
        html.Div([
            html.Div([
                html.Div([
                html.Div([
                    html.Span('GLOBAL OPTIONS', className='section-label'),
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
                    html.Button('⟳', id='refresh-button', title='Refresh dataset and file lists'),
                    html.Button(
                        'Download CSV',
                        id='download-button',
                        disabled=not DOWNLOADS_ENABLED,
                    ),
                    html.Details([
                        html.Summary('⚙ More', title='Sampling, display and marker options'),
                        html.Div([
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
                                dcc.Input(id='hidden_opacity', type='number', value=0.05, step=0.01, debounce=True)
                            ], className='control-field'),
                            html.Div([
                                html.Label('Size:'),
                                dcc.Input(id='size', type='number', value=5, debounce=True)
                            ], className='control-field'),
                            html.Div([
                                html.Label('Font:'),
                                dcc.Input(id='plot_font_size', type='number', value=14, debounce=True)
                            ], className='control-field'),
                            html.Div([
                                html.Label('Point marker:'),
                                dcc.Checklist(
                                    id='detail_marker',
                                    options=[{'label': 'Show', 'value': 'True'}],
                                    value=['True'],
                                    className='toolbar-check',
                                ),
                            ], className='control-field', title='Ring the Details point on the transect, T-S, scatter and profile plots'),
                            html.Div([
                                html.Label('Marker color:'),
                                dcc.Dropdown(
                                    id='detail_marker_color',
                                    options=[
                                        {'label': name, 'value': value}
                                        for name, value in [
                                            ('Red', '#e00000'), ('Black', 'black'),
                                            ('Magenta', '#e377c2'), ('Orange', '#ff7f0e'),
                                            ('Green', '#2ca02c'), ('Blue', '#1f77b4'),
                                            ('White', 'white'),
                                        ]
                                    ],
                                    value='#e00000',
                                    clearable=False,
                                ),
                            ], className='control-field'),
                        ], className='more-panel more-grid'),
                    ], className='more-menu global-more'),
                    dcc.Download(id='download_dataframe_csv'),
                ], className='global-fields'),
            ], className='panel global-panel options-panel'),
        ], className='global-controls'),
        html.Div([
            html.Div([
                html.Div([
                    html.Div('Download CSV', className='download-modal-title'),
                    html.Button('×', id='download-cancel-x', className='modal-close-button'),
                ], className='download-modal-header'),
                html.Div([
                    html.Label('Name:'),
                    dcc.Input(id='download-name', type='text'),
                ], className='control-field'),
                html.Div([
                    html.Label('Email:'),
                    dcc.Input(
                        id='download-email',
                        type='email',
                        placeholder='name@example.org',
                    ),
                ], className='control-field'),
                html.Div([
                    html.Label('Institution:'),
                    dcc.Input(id='download-institution', type='text'),
                ], className='control-field'),
                html.Div(id='download-status', className='download-status'),
                html.Div([
                    html.Button('Cancel', id='download-cancel-button', className='modal-secondary-button'),
                    html.Button('Download', id='download-confirm-button', disabled=True),
                ], className='download-modal-actions'),
            ], className='download-modal'),
        ], id='download-modal-backdrop', className='download-modal-backdrop hidden'),
        # ===== MAIN BODY =====
        html.Div([
            html.Div([
                _plot_card(
                    'CRUISE TRACK',
                    primary=[
                        _inline_field('X-axis:', dcc.Dropdown(
                            id='cruise_track_xaxis',
                            options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                            value='times'
                        )),
                        _inline_field('Y-axis:', dcc.Dropdown(
                            id='cruise_track_yaxis',
                            options=[{'label': f.capitalize(), 'value': f} for f in ['times', 'latitude', 'longitude', 'depth']],
                            value='latitude'
                        )),
                    ],
                    more=[
                        _size_fields('track_width', 900, 'track_height', 320),
                    ],
                    graph_id='cruise_track',
                    container_id='track_container',
                    card_class='plot-card-wide',
                ),
                _plot_card(
                    'TRANSECT PLOT',
                    primary=[
                        _inline_field('X-axis:', dcc.Dropdown(
                            id='x_axis_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in ['latitude', 'longitude', 'times']],
                            value='latitude',
                            clearable=False,
                        ), narrow=True),
                        _inline_field('Y-axis:', dcc.Dropdown(
                            id='y_axis_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in ['depth','latitude']],
                            value='depth',
                            clearable=False,
                        ), narrow=True),
                        _inline_field('Color:', dcc.Dropdown(
                            id='color_variable',
                            options=[{'label': var.capitalize(), 'value': var} for var in sensor_vars],
                            value=default_color_var
                        )),
                        dcc.Checklist(
                            id='bathymetry',
                            options=[{'label': 'Bathymetry', 'value': 'True'}],
                            value=['True'],
                            className='toolbar-check',
                        ),
                        dcc.Checklist(
                            id='station',
                            options=[{'label': 'Stations', 'value': 'True'}],
                            value=['True'],
                            className='toolbar-check',
                        ),
                    ],
                    more=[
                        _select_field('Colormap:', dcc.Dropdown(
                            id='color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                            value='Jet'
                        )),
                        _range_field('Depth', 'z_min', 'z_max'),
                        _range_field('Latitude', 'lat_min', 'lat_max'),
                        _range_field('Longitude', 'lon_min', 'lon_max'),
                        _range_field('Color', 'v_min', 'v_max'),
                        _size_fields('main_width', 900, 'main_height', 620),
                    ],
                    graph_id='main_plot',
                    container_id='main_container',
                    card_class='plot-card-wide',
                ),
                _plot_card(
                    'T-S PLOT',
                    primary=[
                        _inline_field('Color:', dcc.Dropdown(
                            id='ts_color_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in ts_candidates],
                            value=default_ts_var
                        )),
                    ],
                    more=[
                        _select_field('Colormap:', dcc.Dropdown(
                            id='ts_color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                            value='Viridis'
                        )),
                        _range_field('Color', 'ts_v_min', 'ts_v_max'),
                        _size_fields('ts_width', 500, 'ts_height', 620),
                    ],
                    graph_id='ts_plot',
                    container_id='ts_container',
                ),
                _plot_card(
                    'PROFILE PLOT',
                    primary=[
                        _inline_field('Variable:', dcc.Dropdown(
                            id='profile_variable',
                            options=[{'label': var.capitalize(), 'value': var}
                                    for var in profile_vars],
                            value=default_profile_var
                        )),
                    ],
                    more=[
                        _select_field('Colormap:', dcc.Dropdown(
                            id='profile_color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in qualitative_maps],
                            value='Plotly'
                        )),
                        _size_fields('profile_width', 500, 'profile_height', 620),
                    ],
                    graph_id='profile_plot',
                    container_id='profile_container',
                ),
                _plot_card(
                    'SCATTER PLOT',
                    primary=[
                        _inline_field('X:', dcc.Dropdown(
                            id='scatter_x',
                            options=[{'label': var.capitalize(), 'value': var} for var in scatter_vars],
                            value=default_scatter_x,
                            clearable=False,
                        )),
                        _inline_field('Y:', dcc.Dropdown(
                            id='scatter_y',
                            options=[{'label': var.capitalize(), 'value': var} for var in scatter_vars],
                            value=default_scatter_y,
                            clearable=False,
                        )),
                        _inline_field('Color:', dcc.Dropdown(
                            id='scatter_color',
                            options=[{'label': 'None', 'value': 'none'}] + [
                                {'label': var.capitalize(), 'value': var} for var in scatter_vars
                            ],
                            value='depth' if 'depth' in scatter_vars else 'none',
                            clearable=False,
                        )),
                        _inline_field('Fit:', dcc.Dropdown(
                            id='scatter_fit_degree',
                            options=[
                                {'label': 'None', 'value': 0},
                                {'label': 'Linear', 'value': 1},
                                {'label': '2nd order', 'value': 2},
                                {'label': '3rd order', 'value': 3},
                            ],
                            value=0,
                            clearable=False,
                        ), narrow=True),
                        html.Div(dcc.Checklist(
                            id='scatter_fit_selected',
                            options=[{'label': 'On selected', 'value': 'True'}],
                            value=[],
                            className='toolbar-check',
                        ), title='Fit only the points currently selected in any linked plot'),
                    ],
                    more=[
                        _select_field('Colormap:', dcc.Dropdown(
                            id='scatter_color_map',
                            options=[{'label': cmap, 'value': cmap} for cmap in colormaps],
                            value='Turbo'
                        )),
                        _select_field('Fit line color:', dcc.Dropdown(
                            id='scatter_fit_color',
                            options=[
                                {'label': name, 'value': value}
                                for name, value in [
                                    ('Black', 'black'), ('Red', '#d62728'),
                                    ('Blue', '#1f77b4'), ('Orange', '#ff7f0e'),
                                    ('Green', '#2ca02c'), ('Magenta', '#e377c2'),
                                    ('Gray', '#7f7f7f'), ('White', 'white'),
                                ]
                            ],
                            value='#d62728',
                            clearable=False,
                        )),
                        _range_field('Color', 'scatter_v_min', 'scatter_v_max'),
                        _range_field('X', 'scatter_x_min', 'scatter_x_max'),
                        _range_field('Y', 'scatter_y_min', 'scatter_y_max'),
                        _size_fields('scatter_width', 640, 'scatter_height', 520),
                    ],
                    graph_id='scatter_plot',
                    container_id='scatter_container',
                    card_class='plot-card-wide',
                    aside=html.Div(id='scatter_fit_summary', className='fit-summary card'),
                ),
                dcc.Store(id='cruise_track_selected_data'),
                dcc.Store(id='cruise_track_selection_store', data={"selected_ids": None}),
                dcc.Store(id='main_plot_selected_data'),
                # Compact trace-wise point-ID arrays support cross-plot
                # selection without sending complete Plotly figures to Python.
                dcc.Store(id='main_plot_point_ids', data=[]),
                dcc.Store(id='ts_plot_point_ids', data=[]),
                dcc.Store(id='scatter_plot_point_ids', data=[]),
                # Casts currently drawn in the profile, for in-place restyling.
                dcc.Store(id='profile_state'),
            ], className='plot-workspace'),
            html.Div(
                className='details-resizer',
                title='Drag to resize Details; double-click to reset',
            ),
            html.Div([
                html.Div([
                    html.Label('DETAILS', className='section-label'),
                    html.Div([
                        html.Button('◀ Prev', id='detail-prev', title='Previous point in time'),
                        html.Button('Next ▶', id='detail-next', title='Next point in time'),
                    ], className='detail-nav'),
                ], className='details-header'),
                dcc.Store(id='detail_point_id'),
                dcc.Store(id='detail_marker_sink'),
                html.Div(id='click-output', className='card', style={
                    'font-size': '13px',
                    'line-height': '1.4em'
                }),
            ], className='analysis-details right-panel'),
        ], className='dashboard-body'),
        ], className='dashboard-main'),
        # ===== FOOTER =====
        html.Div([
            html.Span(
                'Developed by Sosik Lab @ WHOI | '
                'Credits: Anh Pham, Sidney Batchelder, Joe Futrelle, Heidi Sosik',
                className='footer-credit',
            ),
            html.Span(
                f'Version {DASHBOARD_VERSION} | October 2026',
                className='footer-version',
            )
        ], className='footer')
    ], className='dashboard-shell')

# ============================================================
# === URL Synchronization & Dataset Management Callbacks ===
# ============================================================
