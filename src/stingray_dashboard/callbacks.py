from __future__ import annotations

import logging
from datetime import datetime, timezone
import re
from urllib.parse import parse_qs, urlencode, urlparse

import dash
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, Patch, State, ctx, dcc, html, no_update
from flask import has_request_context, request

from . import data
from .config import (
    DOWNLOADS_ENABLED,
    URL_SYNCED_PARAMS,
    choose_default_dataset,
    get_unit,
    meta_vars,
)
from .plot_utils import (
    dynamic_ticks,
    get_palette,
    get_point_id_from_event_point,
    get_row_by_point_id,
    get_visible_range,
    is_discrete_variable,
    resolve_range,
)
from .science import density_grid

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$"
)

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

def _is_valid_email(email: str | None) -> bool:
    return bool(email and EMAIL_RE.fullmatch(email.strip()))

def _is_present(value: str | None) -> bool:
    return bool(value and value.strip())

def _clean_log_value(value: str) -> str:
    return " ".join(value.strip().split())

def _log_csv_download(
    dataset: str,
    csv_file: str,
    csv_path,
    name: str,
    email: str,
    institution: str,
) -> None:
    logs_dir = data.WORK_DIR / "logs"
    timestamp = datetime.now(timezone.utc).isoformat()
    remote_addr = "-"
    user_agent = "-"
    if has_request_context():
        remote_addr = request.headers.get("X-Forwarded-For", request.remote_addr or "-")
        user_agent = request.headers.get("User-Agent", "-")
    line = (
        f"{timestamp}\tname={name}\temail={email}\tinstitution={institution}"
        f"\tdataset={dataset}\tfile={csv_file}.csv"
        f"\tpath={csv_path}\tremote_addr={remote_addr}\tuser_agent={user_agent}\n"
    )
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        with (logs_dir / "dashboard_downloads.log").open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        logger.warning("Could not write CSV download log entry", exc_info=True)

def register_callbacks(app: dash.Dash) -> None:
    # Extract only trace-wise point IDs in the browser. Selection callbacks
    # use these compact arrays instead of transferring complete Plotly figures.
    app.clientside_callback(
        """
        function(figure) {
            if (!figure || !figure.data) {
                return [];
            }
            return figure.data.map(function(trace) {
                if (!trace.customdata) {
                    return [];
                }
                return trace.customdata.map(function(value) {
                    return Array.isArray(value) ? value[0] : value;
                });
            });
        }
        """,
        Output("main_plot_point_ids", "data"),
        Input("main_plot", "figure"),
    )

    app.clientside_callback(
        """
        function(figure) {
            if (!figure || !figure.data) {
                return [];
            }
            return figure.data.map(function(trace) {
                if (!trace.customdata) {
                    return [];
                }
                return trace.customdata.map(function(value) {
                    return Array.isArray(value) ? value[0] : value;
                });
            });
        }
        """,
        Output("ts_plot_point_ids", "data"),
        Input("ts_plot", "figure"),
    )

    @app.callback(
        Output('download-confirm-button', 'disabled'),
        Input('download-name', 'value'),
        Input('download-email', 'value'),
        Input('download-institution', 'value'),
    )
    def toggle_download_confirm_button(name, email, institution):
        return not (
            _is_present(name)
            and _is_valid_email(email)
            and _is_present(institution)
        )

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
        datasets = data.scan_datasets()
        params = parse_qs(urlparse(search or "").query)
        dataset_default = choose_default_dataset(datasets)
        dataset_val = params.get("dataset", [dataset_default])[0]
        if dataset_val not in datasets:
            dataset_val = dataset_default
        csv_files = data.get_csv_files(dataset_val) if dataset_val else []
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
        ds = data.scan_datasets()
        options = [{'label': f, 'value': f} for f in ds]
        if not ds:
            return [], None
        if current_value in ds:
            return options, current_value
        if current_value is None:
            return options, choose_default_dataset(ds)
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
            data.DATA_CACHE.clear()
            data.AVERAGE_CACHE.clear()
            data.CSV_HEADER_CACHE.clear()
            data.SENSOR_VAR_CACHE.clear()
            data.load_csv.cache_clear()
        elif triggered == "dataset_selector":
            data.DATA_CACHE.clear()
            data.AVERAGE_CACHE.clear()
        csv_files = data.get_csv_files(dataset)
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
        csv_path = data.DATA_DIR / dataset / f"{csv_file}.csv"
        if csv_path not in data.SENSOR_VAR_CACHE:
            dfi = pd.read_csv(csv_path, nrows=1000, low_memory=True)
            dfi = data.canonicalize_columns(dfi)
            data.SENSOR_VAR_CACHE[csv_path] = [
                c for c in dfi.columns
                if "_std" not in c and c not in meta_vars
            ]
        sensor_vars = data.SENSOR_VAR_CACHE[csv_path]
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
        
        df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
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
        State("main_plot_point_ids", "data"),
        State("ts_plot_point_ids", "data"),
        prevent_initial_call=True
    )
    def mirror_selection(scatter_sel, ts_sel, scatter_point_ids, ts_point_ids):
        trigger = ctx.triggered_id
        patched_scatter = Patch()
        patched_ts = Patch()
        # --- Detect clearing ---
        if trigger == "main_plot" and not scatter_sel:
            for i, _ in enumerate(ts_point_ids or []):
                patched_ts["data"][i]["selectedpoints"] = None
            for i, _ in enumerate(scatter_point_ids or []):
                patched_scatter["data"][i]["selectedpoints"] = None
            return patched_scatter, patched_ts
        if trigger == "ts_plot" and not ts_sel:
            for i, _ in enumerate(scatter_point_ids or []):
                patched_scatter["data"][i]["selectedpoints"] = None
            for i, _ in enumerate(ts_point_ids or []):
                patched_ts["data"][i]["selectedpoints"] = None
            return patched_scatter, patched_ts
        # --- Collect selected IDs ---
        selected_ids = set()
        if trigger == "main_plot" and scatter_sel and scatter_sel.get("points"):
            selected_ids = {
                point_id
                for p in scatter_sel["points"]
                for point_id in [get_point_id_from_event_point(p, scatter_point_ids)]
                if point_id is not None
            }
        elif trigger == "ts_plot" and ts_sel and ts_sel.get("points"):
            selected_ids = {
                point_id
                for p in ts_sel["points"]
                for point_id in [get_point_id_from_event_point(p, ts_point_ids)]
                if point_id is not None
            }
        # --- Apply selection to both figures ---
        ids = np.asarray(list(selected_ids), dtype=np.int32)
        for trace_point_ids, patch in [
            (scatter_point_ids, patched_scatter),
            (ts_point_ids, patched_ts),
        ]:
            for i, custom_ids in enumerate(trace_point_ids or []):
                if not custom_ids:
                    continue
                mask = np.isin(custom_ids, ids)
                selected_idx = np.nonzero(mask)[0]
                patch["data"][i]["selectedpoints"] = (
                    selected_idx.tolist() if len(selected_idx) else []
                )
        return patched_scatter, patched_ts

    # --- Callback: Store selected IDs from either scatter plot for profiles ---
    @app.callback(
        Output('main_plot_selected_data', 'data'),
        Input('main_plot', 'selectedData'),
        Input('ts_plot', 'selectedData'),
        State('main_plot_point_ids', 'data'),
        State('ts_plot_point_ids', 'data'),
        prevent_initial_call=True
    )
    def store_scatter_selection_indices(
        main_selected_data,
        ts_selected_data,
        main_point_ids,
        ts_point_ids,
    ):
        """Store IDs selected in the main or T-S scatter plot."""
        if ctx.triggered_id == "ts_plot":
            selected_data = ts_selected_data
            point_ids = ts_point_ids
        else:
            selected_data = main_selected_data
            point_ids = main_point_ids
        if not selected_data or "points" not in selected_data:
            return None
        selected_ids = [
            point_id
            for p in selected_data["points"]
            for point_id in [get_point_id_from_event_point(p, point_ids)]
            if point_id is not None
        ]
        return {"selected_ids": selected_ids}

    # --- Callback: Download CSV file ---
    @app.callback(
        Output('download_dataframe_csv', 'data'),
        Output('download-modal-backdrop', 'className'),
        Output('download-status', 'children'),
        Input('download-button', 'n_clicks'),
        Input('download-confirm-button', 'n_clicks'),
        Input('download-cancel-button', 'n_clicks'),
        Input('download-cancel-x', 'n_clicks'),
        State('dataset_selector', 'value'),
        State('csv_selector', 'value'),
        State('download-name', 'value'),
        State('download-email', 'value'),
        State('download-institution', 'value'),
        prevent_initial_call=True
    )
    def download_csv(
        n_clicks,
        confirm_clicks,
        cancel_clicks,
        cancel_x_clicks,
        dataset,
        csv_file,
        name,
        email,
        institution,
    ):
        """Download the selected raw CSV file and audit the request."""
        if (
            not DOWNLOADS_ENABLED
            or ctx.triggered_id is None
        ):
            return no_update, no_update, no_update

        if ctx.triggered_id == 'download-button':
            if not n_clicks:
                return no_update, no_update, no_update
            return no_update, 'download-modal-backdrop', ''

        if ctx.triggered_id in ('download-cancel-button', 'download-cancel-x'):
            if not (cancel_clicks or cancel_x_clicks):
                return no_update, no_update, no_update
            return no_update, 'download-modal-backdrop hidden', ''

        if ctx.triggered_id != 'download-confirm-button' or not confirm_clicks:
            return no_update, no_update, no_update

        if not _is_present(name):
            return no_update, 'download-modal-backdrop', "Enter your name to download."
        if not _is_valid_email(email):
            return no_update, 'download-modal-backdrop', "Enter a valid email to download."
        if not _is_present(institution):
            return no_update, 'download-modal-backdrop', "Enter your institution to download."
        if (
            not dataset
            or not csv_file
        ):
            return no_update, 'download-modal-backdrop', ''

        csv_path = data.DATA_DIR / dataset / f"{csv_file}.csv"
        if not csv_path.is_file():
            return no_update, 'download-modal-backdrop', ''

        name = _clean_log_value(name)
        email = email.strip()
        institution = _clean_log_value(institution)
        _log_csv_download(dataset, csv_file, csv_path, name, email, institution)
        return (
            dcc.send_file(csv_path, filename=f"{csv_file}.csv"),
            'download-modal-backdrop hidden',
            '',
        )

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
        df = data.load_data(
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
        # Cast numbers identify an ordered sequence of profiles. Render them
        # on one continuous trace to avoid creating one trace per cast.
        if color_var == "cast":
            color_mode = "continuous"

        # Continuous marker coloring requires a sequential Plotly colorscale.
        if color_mode == "continuous" and palette_type == "discrete":
            palette = px.colors.sequential.Viridis
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
            and data.bathy is not None
            and x_axis == "latitude"
            and y_axis == "depth"
        ):
            bathy_mask = (
                (data.bathy["latitude"] <= df["latitude"].max() + 0.01)
                & (data.bathy["latitude"] >= df["latitude"].min() - 0.01)
            )
            fig.add_trace(
                go.Scatter(
                    x=data.bathy["latitude"][bathy_mask],
                    y=data.bathy["bottom_depth_meters"][bathy_mask],
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
            and data.stations is not None
            and x_axis == "latitude"
            and y_axis == "depth"
        ):
            station_mask = (
                (data.stations["latitude"] <= df["latitude"].max() + 0.1)
                & (data.stations["latitude"] >= df["latitude"].min() - 0.1)
            )
            visible_stations = data.stations[station_mask]
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
        df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
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
        # 5️⃣ Configure Numeric or Categorical Colors
        # --------------------------------------------------------
        non_null_color = df[color_var].notna()
        numeric_color = pd.to_numeric(df[color_var], errors="coerce")
        numeric_color_mode = bool(
            non_null_color.any()
            and numeric_color[non_null_color].notna().all()
        )
        colorbar_title = color_var.replace("_", " ").capitalize()

        if numeric_color_mode:
            if vmin is None or vmax is None:
                q = numeric_color.dropna().quantile([0.05, 0.95])
                vmin = q[0.05] if vmin is None else vmin
                vmax = q[0.95] if vmax is None else vmax
            marker_color = numeric_color
            customdata = df["point_id"].astype(np.int32).to_numpy()
            hovertemplate = (
                "Salinity: %{x:.2f}<br>"
                "Temperature: %{y:.2f} °C<br>"
                f"{color_var}: %{{marker.color:.2f}}<extra></extra>"
            )
            coloraxis = dict(
                colorscale=color_map,
                cmin=vmin,
                cmax=vmax,
            )
            unit = get_unit(color_var)
            if unit:
                colorbar_title = f"{colorbar_title} ({unit})"
        else:
            labels = df[color_var].fillna("Missing").astype(str)
            categories = sorted(labels.unique())
            category_codes = pd.Categorical(
                labels,
                categories=categories,
            ).codes
            palette, palette_type = get_palette(color_map)
            if palette_type != "discrete":
                palette = px.colors.qualitative.Plotly
            category_colors = [
                palette[index % len(palette)]
                for index in range(len(categories))
            ]
            discrete_scale = []
            for index, color in enumerate(category_colors):
                lower = index / len(categories)
                upper = (index + 1) / len(categories)
                discrete_scale.extend([[lower, color], [upper, color]])
            marker_color = category_codes
            customdata = np.c_[
                df["point_id"].astype(np.int32).to_numpy(),
                labels.to_numpy(),
            ]
            hovertemplate = (
                "Salinity: %{x:.2f}<br>"
                "Temperature: %{y:.2f} °C<br>"
                f"{color_var}: %{{customdata[1]}}<extra></extra>"
            )
            coloraxis = dict(
                colorscale=discrete_scale,
                cmin=-0.5,
                cmax=len(categories) - 0.5,
            )
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
                    color=marker_color,
                    coloraxis="coloraxis"
                ),
                customdata=customdata,
                selected=dict(marker=dict(opacity=1)),
                unselected=dict(marker=dict(opacity=hidden_opacity)),
                hovertemplate=hovertemplate,
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
                **coloraxis,
                colorbar=dict(
                    title=dict(
                        text=colorbar_title,
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
        if not numeric_color_mode:
            fig.update_coloraxes(
                colorbar=dict(
                    tickmode="array",
                    tickvals=list(range(len(categories))),
                    ticktext=categories,
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
        df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
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
        has_cruise_selection = (
            cruise_track_selection
            and cruise_track_selection.get("mode") != "all"
            and cruise_track_selection.get("selected_ids") is not None
        )
        if has_cruise_selection:
            ids = np.asarray(cruise_track_selection["selected_ids"], dtype=np.int32)
            mask = np.isin(df["point_id"].to_numpy(), ids)
            df = df.loc[mask]
        selected_ids = None
        if isinstance(selected_data, dict):
            selected_ids = selected_data.get("selected_ids")
        has_point_selection = bool(selected_ids)
        if has_point_selection:
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
        elif not has_cruise_selection:
            fig.add_annotation(
                text="Select points in the main or T-S plot to show profiles",
                x=0.5,
                y=0.5,
                xref="paper",
                yref="paper",
                showarrow=False,
                font=dict(size=16, color="black"),
            )
            return fig
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
                    go.Scattergl(
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
        State('main_plot_point_ids', 'data'),
        State('dataset_selector', 'value'),
        State('csv_selector', 'value'),
        State('sub_sample', 'value'),
        State('sampling_mode', 'value'),
    )
    def display_click_data(clickData, main_point_ids, dataset, csv_file, sub_sample, sampling_mode):
        """
        Display detailed information for a clicked point in the main scatter plot.
        Fetches the full row server-side using point_id.
        """
        if not clickData or not clickData.get("points"):
            return "Click on a point to see full details."
        try:
            clicked_point = clickData["points"][0]
            point_id = get_point_id_from_event_point(clicked_point, main_point_ids)

            if point_id is None:
                return "Click on a data point to see full details."

            df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
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
                href = data.get_link(media_value, frame_int)
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
