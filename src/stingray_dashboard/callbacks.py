from __future__ import annotations

import logging
from datetime import datetime, timezone
import re
import time
from urllib.parse import parse_qs, urlencode, urlparse

import dash
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
from dash import ALL, ClientsideFunction, Input, Output, Patch, State, ctx, dcc, html, no_update
from flask import has_request_context, request

from . import data
from .config import (
    DOWNLOADS_ENABLED,
    URL_SYNCED_PARAMS,
    choose_default_dataset,
    find_alias,
    get_unit,
    is_meta_var,
)
from .plot_utils import (
    apply_selection,
    selection_shapes,
    dynamic_ticks,
    get_palette,
    get_point_id_from_event_point,
    get_row_by_point_id,
    get_visible_range,
    is_discrete_variable,
    resolve_range,
    variable_label,
    colorbar_layout,
    coloraxis_name,
    apply_frame,
    frame_layout,
)
from .layout import CITATION_URL, DASHBOARD_VERSION, default_scatter_axes
from .science import density_grid, polynomial_fit

logger = logging.getLogger(__name__)
# White background for figures that don't set one (e.g. empty-state messages).
# plotly_white without the zero line: Plotly draws a pale line at 0 on every axis,
# which nobody asked for and which shows up as a band on transparent exports.
_template = go.layout.Template(pio.templates["plotly_white"])
_template.layout.xaxis.zeroline = False
_template.layout.yaxis.zeroline = False
pio.templates["stingray"] = _template
pio.templates.default = "stingray"
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
    if raw == "":
        # Explicitly emptied control, e.g. an unticked checkbox.
        return [] if typ == "list" else None
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

def _url_param_text(param, value):
    """Text a synced control contributes to the URL, or None when omitted
    (empty, default, or not written)."""
    if param.get("write", True) is False:
        return None
    default = param.get("default")
    if value == default:
        return None
    if value in (None, "", []):
        # Record an emptied control whose default isn't empty, otherwise a
        # link or Back would silently restore the default.
        return None if default in (None, "", []) else ""
    return _serialize_url_value(value, param["type"])

def _is_valid_email(email: str | None) -> bool:
    return bool(email and EMAIL_RE.fullmatch(email.strip()))

def _is_present(value: str | None) -> bool:
    return bool(value and value.strip())

def _clean_log_value(value: str) -> str:
    return " ".join(value.strip().split())

def _get_cruise_track_selection_ids(selected_data):
    """Return stable cruise-track point IDs from a Plotly selection event."""
    if not selected_data or not selected_data.get("points"):
        return []

    selected_ids = []
    for point in selected_data["points"]:
        point_id = get_point_id_from_event_point(point)
        if point_id is None:
            # Keep compatibility with the cruise-track trace's trace-level
            # meta payload for Plotly event payloads that omit customdata.
            point_id = point.get("meta")
        if point_id is not None:
            selected_ids.append(int(point_id))
    return selected_ids

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
    # 1-D point-ID customdata arrives Plotly-encoded ({dtype, bdata}, base64);
    # 2-D customdata with labels stays a list of [point_id, label] rows.
    point_ids_js = (
        """
        function(figure) {
            var TYPED = {
                i1: Int8Array, u1: Uint8Array, i2: Int16Array, u2: Uint16Array,
                i4: Int32Array, u4: Uint32Array, f4: Float32Array, f8: Float64Array
            };
            function decode(encoded) {
                var Typed = TYPED[encoded.dtype];
                if (!Typed) {
                    return [];
                }
                var raw = atob(encoded.bdata);
                var bytes = new Uint8Array(raw.length);
                for (var i = 0; i < raw.length; i++) {
                    bytes[i] = raw.charCodeAt(i);
                }
                return Array.from(new Typed(bytes.buffer));
            }
            function pointIdValue(value) {
                if (value == null) {
                    return null;
                }
                if (Array.isArray(value) || ArrayBuffer.isView(value)) {
                    return value.length ? value[0] : null;
                }
                return value;
            }

            if (!figure || !figure.data) {
                return [];
            }
            return figure.data.map(function(trace) {
                var customdata = trace.customdata;
                if (!customdata) {
                    return [];
                }
                if (customdata.bdata && customdata.dtype) {
                    return decode(customdata);
                }
                return Array.from(customdata, function(value) {
                    return pointIdValue(value);
                });
            });
        }
        """
    )
    for graph_id in ("main_plot", "ts_plot", "scatter_plot"):
        app.clientside_callback(
            point_ids_js,
            Output(f"{graph_id}_point_ids", "data"),
            Input(graph_id, "figure"),
        )

    def _plot_export_config(dataset, csv_file, plot_type, *parts):
        """Build a safe Plotly image-export filename from the active plot settings."""
        components = [dataset, csv_file, plot_type, *parts]
        filename = "_".join(
            re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_")
            for value in components
            if value
        )
        return {"toImageButtonOptions": {"filename": filename or "stingray_plot"}}


    @app.callback(
        Output("cruise_track", "config"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("cruise_track_xaxis", "value"),
        Input("cruise_track_yaxis", "value"),
    )
    def update_cruise_track_export_config(dataset, csv_file, xaxis, yaxis):
        return _plot_export_config(dataset, csv_file, "cruise_track", xaxis, yaxis)


    @app.callback(
        Output("main_plot", "config"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("x_axis_variable", "value"),
        Input("y_axis_variable", "value"),
        Input("color_variable", "value"),
    )
    def update_main_plot_export_config(dataset, csv_file, xaxis, yaxis, color_var):
        return _plot_export_config(dataset, csv_file, "transect", xaxis, yaxis, color_var)


    @app.callback(
        Output("ts_plot", "config"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("ts_color_variable", "value"),
    )
    def update_ts_plot_export_config(dataset, csv_file, color_var):
        return _plot_export_config(
            dataset, csv_file, "ts_temperature-salinity", color_var
        )


    @app.callback(
        Output("profile_plot", "config"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("profile_variable", "value"),
    )
    def update_profile_plot_export_config(dataset, csv_file, variable):
        return _plot_export_config(dataset, csv_file, "profile", variable)


    @app.callback(
        Output("scatter_plot", "config"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("scatter_x", "value"),
        Input("scatter_y", "value"),
    )
    def update_scatter_plot_export_config(dataset, csv_file, x_var, y_var):
        return _plot_export_config(dataset, csv_file, "scatter", x_var, y_var)

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
        existing_params = parse_qs(urlparse(current_search or "").query, keep_blank_values=True)
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
            sval = _url_param_text(p, current_values.get(key))
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
        [State(p["id"], "value") for p in URL_SYNCED_PARAMS],
        State("url_restore_done", "data"),
        prevent_initial_call="initial_duplicate",
    )
    def restore_from_url(search, *state):
        *current_values, restore_done = state
        datasets = data.scan_datasets()
        params = parse_qs(urlparse(search or "").query, keep_blank_values=True)
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
        if not restore_done:
            # Initial page load: apply the full URL state.
            return results + [True]
        # Later URL changes are usually the dashboard's own writes. Only push
        # settings whose URL text differs from the current control, so a
        # change to one plot doesn't reset every control and rebuild all plots.
        # Browser back/forward and edited links still restore what changed.
        updates = []
        for p, url_val, current in zip(URL_SYNCED_PARAMS, results, current_values):
            if p["key"] in ("dataset", "file"):
                changed = url_val != current
            elif p["key"] not in params and p.get("restore_missing", True) is False:
                changed = False
            else:
                changed = (
                    p.get("write", True) is not False
                    and params.get(p["key"], [None])[0] != _url_param_text(p, current)
                )
            updates.append(url_val if changed else no_update)
        if all(u is no_update for u in updates):
            raise dash.exceptions.PreventUpdate
        return updates + [no_update]

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
            data.PROFILE_VAR_CACHE.clear()
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
        if (
            csv_path not in data.SENSOR_VAR_CACHE
            or csv_path not in data.PROFILE_VAR_CACHE
        ):
            # Same numeric-only rule for every plot list, from the loaded
            # (type-converted) data rather than a sample of the raw CSV.
            numeric_vars = data.plot_variables(data.load_data(dataset, csv_file))
            data.SENSOR_VAR_CACHE[csv_path] = numeric_vars
            data.PROFILE_VAR_CACHE[csv_path] = numeric_vars
        profile_vars = data.PROFILE_VAR_CACHE[csv_path]
        # Latitude and longitude can color the transect and T-S plots too.
        sensor_vars = data.SENSOR_VAR_CACHE[csv_path] + data.coordinate_variables(
            data.load_data(dataset, csv_file)
        )
        options = [{'label': variable_label(v), 'value': v} for v in sensor_vars]
        profile_options = [{'label': variable_label(v), 'value': v} for v in profile_vars]
        temp_col = find_alias(sensor_vars, "temperature")
        sal_col = find_alias(sensor_vars, "salinity")
        default_color = temp_col or (sensor_vars[0] if sensor_vars else None)
        ts_candidates = [v for v in sensor_vars if v not in (temp_col, sal_col)]
        ts_options = [{'label': variable_label(v), 'value': v} for v in ts_candidates]
        default_ts = find_alias(ts_candidates, "chlorophyll") or (ts_candidates[0] if ts_candidates else None)
        default_profile = find_alias(profile_vars, "temperature") or (profile_vars[0] if profile_vars else None)
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
        if url_profile in profile_vars:
            profile_val = url_profile
        elif current_profile_var in profile_vars:
            profile_val = current_profile_var
        else:
            profile_val = default_profile
        return options, color_val, ts_options, ts_val, profile_options, profile_val

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


    # --- Callback: Scatter plot variable choices ---
    @app.callback(
        Output('scatter_x', 'options'),
        Output('scatter_x', 'value'),
        Output('scatter_y', 'options'),
        Output('scatter_y', 'value'),
        Output('scatter_color', 'options'),
        Output('scatter_color', 'value'),
        Input('dataset_selector', 'value'),
        Input('csv_selector', 'value'),
        State('scatter_x', 'value'),
        State('scatter_y', 'value'),
        State('scatter_color', 'value'),
        State('url', 'search'),
        prevent_initial_call=True
    )
    def update_scatter_variable_options(dataset, csv_file, current_x, current_y,
                                        current_color, search):
        if not dataset or not csv_file:
            return [], None, [], None, [{'label': 'None', 'value': 'none'}], 'none'
        df = data.load_data(dataset, csv_file)
        numeric_vars = data.plot_variables(df) + [
            c for c in ("depth", "latitude", "longitude")
            if c in df.columns and pd.api.types.is_numeric_dtype(df[c])
        ]
        options = [{'label': variable_label(v), 'value': v} for v in numeric_vars]
        default_x, default_y = default_scatter_axes(numeric_vars)
        params = parse_qs(urlparse(search or "").query)

        def pick(url_key, current, default, allowed):
            url_val = params.get(url_key, [None])[0]
            if url_val in allowed:
                return url_val
            if current in allowed:
                return current
            return default

        x_val = pick("sx", current_x, default_x, numeric_vars)
        y_val = pick("sy", current_y, default_y, numeric_vars)
        color_allowed = ['none'] + numeric_vars
        color_default = 'depth' if 'depth' in numeric_vars else 'none'
        color_val = pick("scolor", current_color, color_default, color_allowed)
        color_options = [{'label': 'None', 'value': 'none'}] + options
        return options, x_val, options, y_val, color_options, color_val

    # --- Callback: Reset scatter color limits when color variable changes ---
    @app.callback(
        Output('scatter_v_min', 'value'),
        Output('scatter_v_max', 'value'),
        Input('scatter_color', 'value'),
        prevent_initial_call=True
    )
    def reset_scatter_vmin_vmax(color_var):
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

    # ============================================================
    # === Cruise Track Plot (Latitude vs. Time or Longitude) ===
    # ============================================================
    def _track_axes(df, xaxis, yaxis):
        """Swap a times axis for longitude/latitude when the data has no valid times."""
        if data.has_valid_times(df):
            return xaxis, yaxis
        if xaxis == "times":
            xaxis = "longitude"
        if yaxis == "times":
            yaxis = "latitude" if xaxis != "latitude" else "longitude"
        return xaxis, yaxis

    # Keep the Track axis dropdowns in step when a file has no usable times.
    @app.callback(
        Output("cruise_track_xaxis", "value"),
        Output("cruise_track_yaxis", "value"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        State("cruise_track_xaxis", "value"),
        State("cruise_track_yaxis", "value"),
        prevent_initial_call=True,
    )
    def update_track_axes(dataset, csv_file, xaxis, yaxis):
        if not dataset or not csv_file:
            raise dash.exceptions.PreventUpdate
        df = data.load_data(dataset, csv_file)
        new_x, new_y = _track_axes(df, xaxis, yaxis)
        if (new_x, new_y) == (xaxis, yaxis):
            raise dash.exceptions.PreventUpdate
        return new_x, new_y

    @app.callback(
        Output("cruise_track", "figure"),
        Input("dataset_selector", "value"),
        Input("csv_selector", "value"),
        Input("cruise_track_xaxis", "value"),
        Input("cruise_track_yaxis", "value"),
        Input("plot_font_size", "value"),
        Input("sub_sample", "value"),
        Input("sampling_mode", "value"),
        Input("track_xlabel", "value"),
        Input("track_ylabel", "value"),
        Input("track_width", "value"),
        Input("track_height", "value"),
        prevent_initial_call=True,
    )
    def draw_cruise_track(dataset, csv_file, xaxis, yaxis, fontsize, sub_sample, sampling_mode,
                          x_text, y_text, plot_w, plot_h):
        trigger = ctx.triggered_id
        if trigger not in (
            "dataset_selector",   
            "csv_selector",
            "cruise_track_xaxis",
            "cruise_track_yaxis",
            "plot_font_size",
            "sub_sample",
            "sampling_mode",
            "track_xlabel",
            "track_ylabel",
            "track_width",
            "track_height",
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
        xaxis, yaxis = _track_axes(df, xaxis, yaxis)
        fig = go.Figure()
        fig.add_trace(go.Scattergl(
            x=df[xaxis],
            y=df[yaxis],
            mode="markers",
            marker=dict(size=5, color="blue"),
            meta=df["point_id"].astype(int).tolist(),
            customdata=df["point_id"].astype(int).to_numpy().reshape(-1, 1).tolist()
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
                title=x_text or xaxis.capitalize(),
                rangeslider=dict(visible=False),
                showgrid=True, gridcolor="rgba(0,0,0,0.1)",
                showline=True, linecolor="black", mirror=True
            ),
            yaxis=dict(
                title=y_text or yaxis.capitalize(),
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
        return apply_frame(fig, frame_layout(plot_w, plot_h, fontsize or 10))

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
            ids = _get_cruise_track_selection_ids(selectedData)
            if ids:
                return {"mode": "ids", "selected_ids": ids, "_key": this_key}

            # Clearing the cruise-track selection restores all observations:
            # S = {i : i is a point_id in the active dataframe}.
            return {"mode": "all", "selected_ids": None, "_key": this_key}
        raise dash.exceptions.PreventUpdate

    # Plots whose box/lasso selections are mirrored onto each other and
    # drive the profile plot.
    linked_plots = ("main_plot", "ts_plot", "scatter_plot")

    def _selected_point_ids(selected_data, point_ids):
        return {
            point_id
            for p in selected_data["points"]
            for point_id in [get_point_id_from_event_point(p, point_ids)]
            if point_id is not None
        }

    # --- Callback: Mirror selections between the transect, T-S and scatter
    # plots, and store the selection for the profile, fit and redraws ---
    # One callback does both: as separate callbacks they raced. Patching the
    # other plots removes their old boxes, Plotly then reports "0 points" for
    # them, and Dash discarded the still-pending store update for the new box.
    @app.callback(
        [Output(g, "figure", allow_duplicate=True) for g in linked_plots]
        + [Output('main_plot_selected_data', 'data')],
        [Input(g, "selectedData") for g in linked_plots],
        [State(f"{g}_point_ids", "data") for g in linked_plots],
        State('x_axis_variable', 'value'),
        State('y_axis_variable', 'value'),
        State('scatter_x', 'value'),
        State('scatter_y', 'value'),
        prevent_initial_call=True
    )
    def mirror_selection(*args):
        n = len(linked_plots)
        selections = dict(zip(linked_plots, args[:n]))
        point_ids = dict(zip(linked_plots, args[n:2 * n]))
        main_x, main_y, scatter_x, scatter_y = args[2 * n:]
        axes = {
            "main_plot": f"{main_x}/{main_y}",
            "ts_plot": "salinity/temperature",
            "scatter_plot": f"{scatter_x}/{scatter_y}",
        }
        trigger = ctx.triggered_id if ctx.triggered_id in linked_plots else "main_plot"
        patches = {g: Patch() for g in linked_plots}
        trigger_sel = selections.get(trigger)
        # --- Detect clearing (double-click deselect) ---
        if not trigger_sel:
            for g in linked_plots:
                for i, _ in enumerate(point_ids[g] or []):
                    patches[g]["data"][i]["selectedpoints"] = None
            return [patches[g] for g in linked_plots] + [None]
        # An empty selection is either a box over no data or Plotly reporting
        # a box removed below; neither should replace the current selection.
        if not trigger_sel.get("points"):
            raise dash.exceptions.PreventUpdate
        # --- Latest selection wins: drop the drawn boxes on the other plots ---
        # Plotly keeps user-drawn boxes across redraws (uirevision) and would
        # re-apply them, so a new selectionrevision is needed to discard them.
        revision = f"{trigger}-{time.monotonic_ns()}"
        for g in linked_plots:
            if g != trigger:
                patches[g]["layout"]["selections"] = []
                patches[g]["layout"]["selectionrevision"] = revision
        # --- Apply selection to every linked figure ---
        selected_ids = _selected_point_ids(trigger_sel, point_ids[trigger])
        ids = np.asarray(list(selected_ids), dtype=np.int32)
        for g in linked_plots:
            for i, custom_ids in enumerate(point_ids[g] or []):
                if not custom_ids:
                    continue
                mask = np.isin(custom_ids, ids)
                selected_idx = np.nonzero(mask)[0]
                patches[g]["data"][i]["selectedpoints"] = (
                    selected_idx.tolist() if len(selected_idx) else []
                )
        # Stored for the profile and fit, plus the drawn box/lasso so the
        # source plot can keep it when it redraws.
        stored = {
            "selected_ids": list(selected_ids),
            "source": trigger,
            "axes": axes[trigger],
            "selections": selection_shapes(trigger_sel),
        }
        return [patches[g] for g in linked_plots] + [stored]

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
            Input('lat_min', 'value'),
            Input('lat_max', 'value'),
            Input('lon_min', 'value'),
            Input('lon_max', 'value'),
            Input('hidden_opacity', 'value'),
            Input('plot_font_size', 'value'),
            Input('bathymetry', 'value'),
            Input('station', 'value'),
            Input('cruise_track_selection_store', 'data'),
            Input('main_colorbar_pos', 'value'),
            Input('main_xlabel', 'value'),
            Input('main_ylabel', 'value'),
            Input('main_clabel', 'value'),
            Input('main_width', 'value'),
            Input('main_height', 'value'),
            Input('main_plot', 'relayoutData'),
        ],
        State('main_plot_selected_data', 'data'),
    )
    def update_main_plot(
        dataset, csv_file, sub_sample, sampling_mode,
        x_axis, y_axis, color_var, color_map, size,
        vmin, vmax, zmin, zmax, latmin, latmax, lonmin, lonmax,
        hidden_opacity, fontsize, bathymetry, station,
        cruise_track_selection, colorbar_pos, x_text, y_text, c_text,
        plot_w, plot_h, relayoutData, selection
    ):
        # ------------------------------------------------------------------
        # 1. Ignore irrelevant relayout events
        # ------------------------------------------------------------------
        axis_id = coloraxis_name(colorbar_pos)
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
        # Only the colormap or color range changed: patch the shared color
        # axis instead of rebuilding (keeps zoom/selection, much less data).
        # Discrete coloring uses one trace per class, so it still rebuilds.
        color_only = {"color_map", "v_min", "v_max"}
        triggered = {prop.split(".")[0] for prop in ctx.triggered_prop_ids}
        if color_mode == "continuous" and triggered and triggered <= color_only:
            patched = Patch()
            # A Patch bypasses plotly.py's validation, so send the [[pos, color]]
            # pairs Plotly.js expects rather than a bare list of colors.
            steps = max(len(palette) - 1, 1)
            patched["layout"][axis_id]["colorscale"] = [
                [i / steps, color] for i, color in enumerate(palette)
            ]
            patched["layout"][axis_id]["cmin"] = None if vmin is None else float(vmin)
            patched["layout"][axis_id]["cmax"] = None if vmax is None else float(vmax)
            return patched
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
                        coloraxis=axis_id
                    ),
                    customdata=df["point_id"].astype(np.int32).to_numpy(),
                    hovertemplate=(
                        f"{variable_label(x_axis)}: %{{x:.2f}}<br>"
                        f"{variable_label(y_axis)}: %{{y:.2f}}<br>"
                        f"{variable_label(color_var)}: "
                        "%{marker.color:.2f}"
                        "<extra></extra>"
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
                            ].tolist(),
                            name=str(class_value),
                            hovertemplate=(
                                f"{variable_label(x_axis)}: %{{x}}<br>"
                                f"{variable_label(y_axis)}: %{{y}}<br>"
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
                            coloraxis=axis_id
                        ),
                        customdata=np.c_[
                            df["point_id"].astype(np.int32).to_numpy(),
                            df[color_var].to_numpy()
                        ].tolist(),
                        hovertemplate=(
                            f"{variable_label(x_axis)}: %{{x}}<br>"
                            f"{variable_label(y_axis)}: %{{y}}<br>"
                            f"{variable_label(color_var)}: "
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
        coord_limits = {
            "latitude": (latmin, latmax),
            "longitude": (lonmin, lonmax),
        }

        def padded(lo, hi, frac=0.02):
            # Margin so Scattergl markers on the edges aren't clipped by the
            # axis border; ticks stay computed from the unpadded range.
            pad = (hi - lo) * frac
            return lo - pad, hi + pad

        def has_limits(var):
            lo, hi = coord_limits.get(var, (None, None))
            return lo is not None and hi is not None
        # -------------------------
        # X axis
        # -------------------------
        xticks = None
        xticktext = None
        if x_axis == "times":
            x_range = list(padded(df["times"].min(), df["times"].max()))
        elif x_axis in df.columns:
            xmin, xmax = resolve_range(
                visible_xrange,
                df[x_axis],
                *coord_limits.get(x_axis, (None, None))
            )
            xticks, digits = dynamic_ticks(xmin, xmax, nticks=6)
            if visible_xrange is None and not has_limits(x_axis):
                x_lo, x_hi = padded(xmin, xmax)
            else:
                x_lo, x_hi = xmin, xmax
            x_range = [x_lo, x_hi]
            if x_axis == "latitude":
                x_range = [x_hi, x_lo]
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
        ylabel = variable_label(y_axis)
        if y_axis in df.columns:
            if y_axis == "depth":
                if visible_yrange is not None:
                    ymin, ymax = min(visible_yrange), max(visible_yrange)
                else:
                    # Min/Max Depth are optional: a blank one uses the data's
                    # extent, and either order works (0-200 or 200-0).
                    depth = pd.to_numeric(df[y_axis], errors="coerce")
                    lo = depth.min() if zmin is None else zmin
                    hi = depth.max() if zmax is None else zmax
                    ymin, ymax = min(lo, hi), max(lo, hi)
                ylabel = "Depth (m)"
                # Min/Max Depth stay the limits, with room for points at them.
                y_lo, y_hi = (ymin, ymax) if visible_yrange else padded(ymin, ymax)
                y_range = [y_hi, y_lo]
            else:
                ymin, ymax = resolve_range(
                    visible_yrange,
                    df[y_axis],
                    *coord_limits.get(y_axis, (None, None))
                )
                if visible_yrange is None and not has_limits(y_axis):
                    y_range = list(padded(ymin, ymax))
                else:
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
        # Station labels sit above the plot, so the frame leaves room for them.
        station_labels_shown = (
            "True" in station
            and data.stations is not None
            and x_axis == "latitude"
            and y_axis == "depth"
        )
        frame = frame_layout(
            plot_w, plot_h, base_font,
            bar=colorbar_pos if color_mode == "continuous" else None,
            top_extra=(35 + int(base_font) + 8) if station_labels_shown else 0,
        )
        layout_kwargs = dict(
            dragmode="zoom",
            uirevision=f"{dataset}-{csv_file}",
            font=dict(size=base_font, color="black"),
            paper_bgcolor="white",
            plot_bgcolor="white",
            xaxis=dict(
                title=x_text or variable_label(x_axis),
                range=x_range,
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
                title=y_text or ylabel,
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
            layout_kwargs[axis_id] = dict(
                colorbar=colorbar_layout(
                    colorbar_pos, c_text or variable_label(color_var), base_font, nticks=10,
                    frame=frame,
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
        # A range outside the data (often east/west or north/south sign) would
        # otherwise just look like an empty plot.
        for axis_name in {x_axis, y_axis} & set(coord_limits):
            if axis_name not in df.columns or not has_limits(axis_name):
                continue
            lo, hi = sorted(coord_limits[axis_name])
            if not df[axis_name].between(lo, hi).any():
                fig.add_annotation(
                    text=(
                        f"No points between {lo:g} and {hi:g} {axis_name}. "
                        "Use negative values for south and west (-10 = 10°S or 10°W)."
                    ),
                    x=0.5, y=0.5, xref="paper", yref="paper",
                    showarrow=False, font=dict(size=14, color="red"),
                )
        apply_frame(fig, frame)
        return apply_selection(fig, selection, "main_plot", f"{x_axis}/{y_axis}")
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
            Input('ts_colorbar_pos', 'value'),
            Input('ts_xlabel', 'value'),
            Input('ts_ylabel', 'value'),
            Input('ts_clabel', 'value'),
            Input('ts_width', 'value'),
            Input('ts_height', 'value'),
        ],
        State('main_plot_selected_data', 'data'),
    )
    def update_ts_plot(dataset, csv_file, sub_sample, sampling_mode,
                    color_var, color_map, size, vmin, vmax,
                    hidden_opacity, fontsize, cruise_track_selection, colorbar_pos,
                    x_text, y_text, c_text, plot_w, plot_h, selection):
        # --------------------------------------------------------
        # 1️⃣ Load Data
        # --------------------------------------------------------
        if not csv_file:
            return go.Figure().add_annotation(
                text="⚠️ No CSV found", x=0.5, y=0.5, showarrow=False
            )
        axis_id = coloraxis_name(colorbar_pos)
        df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
        # Temperature/salinity under whatever name the file uses (t090c, sal00, ...).
        temp_col = find_alias(df.columns, "temperature")
        sal_col = find_alias(df.columns, "salinity")
        if temp_col is None or sal_col is None:
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
        tmin, tmax = df[temp_col].quantile([0.01, 0.99]).round().astype(int)
        smin, smax = df[sal_col].quantile([0.01, 0.99]).round().astype(int)
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
                f"{variable_label(color_var)}: %{{marker.color:.2f}}<extra></extra>"
            )
            coloraxis = dict(
                colorscale=(
                    get_palette(color_map)[0] if str(color_map).startswith('cmo.') else color_map
                ),
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
            ].tolist()
            hovertemplate = (
                "Salinity: %{x:.2f}<br>"
                "Temperature: %{y:.2f} °C<br>"
                f"{variable_label(color_var)}: %{{customdata[1]}}<extra></extra>"
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
                x=df[sal_col],
                y=df[temp_col],
                mode="markers",
                marker=dict(
                    size=size,
                    color=marker_color,
                    coloraxis=axis_id
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
        frame = frame_layout(plot_w, plot_h, base_font, bar=colorbar_pos)
        fig.update_layout(
            dragmode="zoom",
            uirevision='keep',
            font=dict(size=base_font, color='black'),
            paper_bgcolor='white',
            plot_bgcolor='white',
            xaxis=dict(
                title=x_text or 'Salinity (psu)',
                range=[smin, smax],
                # The Home button autoranges over every point; clip it to the
                # percentile window so a few outliers cannot stretch the view.
                autorangeoptions=dict(clipmin=smin, clipmax=smax),
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor='rgba(0, 0, 0, 0.1)',
                showline=True,
                linecolor='black',
                mirror=True,
                ticks='outside'
            ),
            yaxis=dict(
                title=y_text or 'Temperature (°C)',
                range=[tmin, tmax],
                autorangeoptions=dict(clipmin=tmin, clipmax=tmax),
                tickfont=dict(size=base_font),
                showgrid=True,
                gridcolor='rgba(0, 0, 0, 0.1)',
                showline=True,
                linecolor='black',
                mirror=True,
                ticks='outside'
            ),
            **{axis_id: dict(
                **coloraxis,
                colorbar=colorbar_layout(
                    colorbar_pos, c_text or colorbar_title, base_font, nticks=5,
                    frame=frame,
                )
            )}
        )
        if not numeric_color_mode:
            fig.update_coloraxes(
                colorbar=dict(
                    tickmode="array",
                    tickvals=list(range(len(categories))),
                    ticktext=categories,
                )
            )
        apply_frame(fig, frame)
        return apply_selection(fig, selection, "ts_plot", "salinity/temperature")

    # ============================================================
    # === Scatter Plot with Polynomial Fit ===
    # ============================================================
    def _scatter_frame(dataset, csv_file, sub_sample, sampling_mode, cruise_track_selection):
        """Load data and apply the cruise-track filter, as the other plots do."""
        df = data.load_data(dataset, csv_file, sub_sample=sub_sample, mode=sampling_mode)
        if (
            cruise_track_selection
            and cruise_track_selection.get("mode") != "all"
            and cruise_track_selection.get("selected_ids") is not None
        ):
            ids = np.asarray(cruise_track_selection["selected_ids"], dtype=np.int32)
            df = df.loc[np.isin(df["point_id"].to_numpy(), ids)]
        return df

    def _fit_subset(df, fit_on, selection):
        """Rows used for the fit and a note on which data that is.

        fit_on is the "On selected" tick box: empty fits every point shown
        (after the cruise-track filter), ticked fits the current selection
        from any linked plot.
        """
        if not fit_on:
            return df, "Fitted on all points shown (cruise-track filter applied)."
        if selection and selection.get("selected_ids"):
            ids = np.asarray(selection["selected_ids"], dtype=np.int32)
            return df.loc[np.isin(df["point_id"].to_numpy(), ids)], "Fitted on selected points."
        return df, "Nothing selected yet; fitted on all points shown."

    def _axis_label(var):
        unit = get_unit(var)
        label = var.replace("_", " ").capitalize()
        return f"{label} ({unit})" if unit else label

    def _polynomial_fit(df, x_var, y_var, degree):
        if x_var not in df.columns or y_var not in df.columns:
            return None
        return polynomial_fit(
            pd.to_numeric(df[x_var], errors="coerce"),
            pd.to_numeric(df[y_var], errors="coerce"),
            degree,
        )

    def _fit_summary(fit, x_var, y_var, basis_note):
        """Equation, R², n and RMSE for the panel beside the scatter plot."""
        if fit is None:
            return html.Div("No fit. Choose a degree under Fit.", className='fit-note')
        title = {1: "Linear fit", 2: "2nd order fit", 3: "3rd order fit"}.get(
            fit["degree"], f"Polynomial fit (degree {fit['degree']})"
        )
        if "error" in fit:
            return [html.Div(title, className='fit-title'),
                    html.Div(f"{fit['error']} (n = {fit['n']:,})", className='fit-note')]
        superscripts = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
        terms = []
        for power in range(len(fit["coef"]) - 1, -1, -1):
            c = fit["coef"][power]
            sign = "−" if c < 0 else "+"
            x_part = "" if power == 0 else ("·x" if power == 1 else "·x" + str(power).translate(superscripts))
            terms.append((sign, f"{abs(c):.4g}{x_part}"))
        first_sign, first_term = terms[0]
        lines = [f"y = {'−' if first_sign == '−' else ''}{first_term}"]
        lines += [f"    {sign} {term}" for sign, term in terms[1:]]
        return [
            html.Div(title, className='fit-title'),
            html.Pre("\n".join(lines), className='fit-equation'),
            html.Div(f"R² = {fit['r2']:.4f}"),
            html.Div(f"RMSE = {fit['rmse']:.4g}"),
            html.Div(f"n = {fit['n']:,}"),
            html.Div(
                [html.Div(f"x = {_axis_label(x_var)}"), html.Div(f"y = {_axis_label(y_var)}")],
                className='fit-note',
            ),
            html.Div(basis_note, className='fit-note'),
        ]

    def _fit_shapes(fit, color="#d62728"):
        """Fit curve as a layout shape; unlike a trace, box/lasso selections
        don't dim it."""
        if fit is None or "error" in fit:
            return []
        path = "M " + " L ".join(
            f"{x:.10g},{y:.10g}" for x, y in zip(fit["curve_x"], fit["curve_y"])
        )
        return [dict(
            type="path", path=path, xref="x", yref="y",
            line=dict(color=color or "#d62728", width=2), layer="above",
        )]

    @app.callback(
        Output('scatter_plot', 'figure'),
        Output('scatter_fit_summary', 'children'),
        Input('dataset_selector', 'value'),
        Input('csv_selector', 'value'),
        Input('sub_sample', 'value'),
        Input('sampling_mode', 'value'),
        Input('scatter_x', 'value'),
        Input('scatter_y', 'value'),
        Input('scatter_color', 'value'),
        Input('scatter_color_map', 'value'),
        Input('size', 'value'),
        Input('scatter_v_min', 'value'),
        Input('scatter_v_max', 'value'),
        Input('scatter_x_min', 'value'),
        Input('scatter_x_max', 'value'),
        Input('scatter_y_min', 'value'),
        Input('scatter_y_max', 'value'),
        Input('scatter_fit_degree', 'value'),
        Input('hidden_opacity', 'value'),
        Input('plot_font_size', 'value'),
        Input('cruise_track_selection_store', 'data'),
        Input('scatter_colorbar_pos', 'value'),
        Input('scatter_xlabel', 'value'),
        Input('scatter_ylabel', 'value'),
        Input('scatter_clabel', 'value'),
        Input('scatter_width', 'value'),
        Input('scatter_height', 'value'),
        State('scatter_fit_selected', 'value'),
        State('main_plot_selected_data', 'data'),
        State('scatter_fit_color', 'value'),
    )
    def update_scatter_plot(dataset, csv_file, sub_sample, sampling_mode,
                            x_var, y_var, color_var, color_map, size, vmin, vmax,
                            x_min, x_max, y_min, y_max, degree,
                            hidden_opacity, fontsize, cruise_track_selection, colorbar_pos,
                            x_text, y_text, c_text, plot_w, plot_h,
                            fit_on, selection, fit_color):
        def message(text):
            fig = go.Figure().add_annotation(
                text=text, x=0.5, y=0.5, xref="paper", yref="paper",
                showarrow=False, font=dict(size=16, color="red")
            )
            return fig, None

        axis_id = coloraxis_name(colorbar_pos)
        if not csv_file:
            return message("⚠️ No CSV found")
        df = _scatter_frame(dataset, csv_file, sub_sample, sampling_mode, cruise_track_selection)
        if not x_var or not y_var or x_var not in df.columns or y_var not in df.columns:
            return message("Choose X and Y variables")
        df = df[df[x_var].notna() & df[y_var].notna()]
        if df.empty:
            return message(f"⚠️ No data with both {x_var} and {y_var}")

        hovertemplate = (
            f"{_axis_label(x_var)}: %{{x:.4g}}<br>"
            f"{_axis_label(y_var)}: %{{y:.4g}}"
        )
        marker = dict(size=size)
        coloraxis = None
        font_px = fontsize or 14
        frame = frame_layout(
            plot_w, plot_h, font_px,
            bar=colorbar_pos if (color_var and color_var != "none" and color_var in df.columns) else None,
        )
        if color_var and color_var != "none" and color_var in df.columns:
            color_values = pd.to_numeric(df[color_var], errors="coerce")
            if vmin is None or vmax is None:
                q = color_values.dropna().quantile([0.05, 0.95])
                vmin = q[0.05] if vmin is None else vmin
                vmax = q[0.95] if vmax is None else vmax
            palette, _ = get_palette(color_map or "Viridis")
            marker.update(color=color_values, coloraxis=axis_id)
            coloraxis = dict(
                colorscale=palette, cmin=vmin, cmax=vmax,
                # Horizontal under the plot, like the T-S colorbar.
                colorbar=colorbar_layout(
                    colorbar_pos, c_text or _axis_label(color_var), font_px, nticks=8,
                    frame=frame,
                ),
            )
            hovertemplate += f"<br>{_axis_label(color_var)}: %{{marker.color:.4g}}"
        else:
            marker.update(color="#1f77b4")
        hovertemplate += "<extra></extra>"

        fit_df, basis_note = _fit_subset(df, fit_on, selection)
        fit = _polynomial_fit(fit_df, x_var, y_var, degree)

        fig = go.Figure()
        fig.add_trace(
            go.Scattergl(
                x=df[x_var],
                y=df[y_var],
                mode="markers",
                marker=marker,
                customdata=df["point_id"].astype(np.int32).to_numpy(),
                selected=dict(marker=dict(opacity=1)),
                unselected=dict(marker=dict(opacity=hidden_opacity)),
                hovertemplate=hovertemplate,
                showlegend=False,
            )
        )

        def axis(title, var, lo, hi):
            out = dict(
                title=title, showgrid=True, gridcolor='rgba(0, 0, 0, 0.1)',
                showline=True, linecolor='black', mirror=True, ticks='outside',
            )
            if lo is not None and hi is not None:
                out["range"] = sorted([lo, hi])
            elif var not in ("depth", "latitude", "longitude"):
                # Start (and Home/autoscale) on the 1st-99th percentile window so a
                # few bad points cannot stretch the view; zoom out or set the
                # limits to see them.
                values = pd.to_numeric(df[var], errors="coerce").dropna()
                if not values.empty:
                    q_lo, q_hi = values.quantile([0.01, 0.99])
                    if q_hi > q_lo:
                        pad = 0.05 * (q_hi - q_lo)
                        window = [float(q_lo - pad), float(q_hi + pad)]
                        out["range"] = window
                        out["autorangeoptions"] = dict(clipmin=window[0], clipmax=window[1])
            return out

        fig.update_layout(
            dragmode="zoom",
            uirevision=f"{dataset}/{csv_file}/{x_var}/{y_var}",
            font=dict(size=fontsize or 14, color='black'),
            paper_bgcolor='white',
            plot_bgcolor='white',
            xaxis=axis(x_text or _axis_label(x_var), x_var, x_min, x_max),
            yaxis=axis(y_text or _axis_label(y_var), y_var, y_min, y_max),
            shapes=_fit_shapes(fit, fit_color),
        )
        if coloraxis:
            fig.update_layout(**{axis_id: coloraxis})
        for var, lo, hi in ((x_var, x_min, x_max), (y_var, y_min, y_max)):
            if lo is not None and hi is not None and not df[var].between(*sorted([lo, hi])).any():
                fig.add_annotation(
                    text=f"No points between {min(lo, hi):g} and {max(lo, hi):g} for {_axis_label(var)}.",
                    x=0.5, y=0.5, xref="paper", yref="paper",
                    showarrow=False, font=dict(size=14, color="red"),
                )
        apply_frame(fig, frame)
        return apply_selection(fig, selection, "scatter_plot", f"{x_var}/{y_var}"), _fit_summary(fit, x_var, y_var, basis_note)

    # --- Callback: Refit when the selection, "On selected" or line color changes ---
    # Only the curve shape and summary are patched, so the point selection
    # highlighted in the scatter plot is kept.
    @app.callback(
        Output('scatter_plot', 'figure', allow_duplicate=True),
        Output('scatter_fit_summary', 'children', allow_duplicate=True),
        Input('main_plot_selected_data', 'data'),
        Input('scatter_fit_selected', 'value'),
        Input('scatter_fit_color', 'value'),
        State('dataset_selector', 'value'),
        State('csv_selector', 'value'),
        State('sub_sample', 'value'),
        State('sampling_mode', 'value'),
        State('scatter_x', 'value'),
        State('scatter_y', 'value'),
        State('scatter_fit_degree', 'value'),
        State('cruise_track_selection_store', 'data'),
        prevent_initial_call=True
    )
    def refit_scatter_selection(selection, fit_on, fit_color, dataset, csv_file, sub_sample,
                                sampling_mode, x_var, y_var, degree, cruise_track_selection):
        if ctx.triggered_id == 'main_plot_selected_data' and not fit_on:
            raise dash.exceptions.PreventUpdate
        if not csv_file or not x_var or not y_var:
            raise dash.exceptions.PreventUpdate
        df = _scatter_frame(dataset, csv_file, sub_sample, sampling_mode, cruise_track_selection)
        fit_df, basis_note = _fit_subset(df, fit_on, selection)
        fit = _polynomial_fit(fit_df, x_var, y_var, degree)
        patched = Patch()
        patched["layout"]["shapes"] = _fit_shapes(fit, fit_color)
        return patched, _fit_summary(fit, x_var, y_var, basis_note)

    # ============================================================
    # === Vertical Profile Plot Update (Depth vs. Variable) ===
    # ============================================================
    def _position_label(latitude, longitude):
        return (
            f"{abs(latitude):.2f}{'N' if latitude >= 0 else 'S'} "
            f"{abs(longitude):.2f}{'E' if longitude >= 0 else 'W'}"
        )

    def _cast_style(cast_id, focus_cast, position=None):
        """Line style of one cast: bold when it is the Details point's cast,
        faded when another cast is in focus."""
        is_focus = focus_cast is not None and int(cast_id) == focus_cast
        faded = focus_cast is not None and not is_focus
        name = f"{int(cast_id)}"
        if position:
            name = f"{name} · {position}"
        return dict(
            width=3.5 if is_focus else (1.5 if faded else 2),
            marker_size=5 if is_focus else 4,
            opacity=0.35 if faded else 1,
            name=f"{name} ●" if is_focus else name,
        )

    @app.callback(
        Output('profile_plot', 'figure'),
        Output('profile_state', 'data'),
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
            Input('detail_point_id', 'data'),
            Input('detail_marker', 'value'),
            Input('detail_marker_color', 'value'),
            Input('profile_legend', 'value'),
            Input('profile_xlabel', 'value'),
            Input('profile_ylabel', 'value'),
            Input('profile_width', 'value'),
            Input('profile_height', 'value'),
        ],
        State('profile_state', 'data'),
    )
    def update_profile_plot(*args):
        plot_w, plot_h, font = args[-3], args[-2], args[6]
        result = _profile_figure(*args)
        figure, state = result if isinstance(result, tuple) else (result, None)
        # Message figures ("select points", "no data") get the same frame.
        if isinstance(figure, go.Figure) and figure.layout.width is None:
            apply_frame(figure, frame_layout(plot_w, plot_h, font or 10))
        return figure, state

    def _profile_figure(dataset, csv_file, sub_sample, sampling_mode,
                        color_var, color_map, fontsize,
                        selected_data, cruise_track_selection,
                        focus_id, show_marker, marker_color, show_legend,
                        x_text, y_text, plot_w, plot_h, prev_state):
        """
        Update the vertical profile plot.
        Behavior:
            - Cruise track selection is applied first.
            - Main-plot selection expands to full casts when cast exists.
            - The Details point (click or Prev/Next) adds its cast in bold on
              top of the selected casts, which fade; a dot marks its depth
              when the Details "Marker" box is ticked. The box selection
              itself is never changed by a click.
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
        # --- Details point: its row and cast (if the file has casts) ---
        # With the point marker off, the clicked point doesn't affect the
        # profile at all (no bold cast, no dot), so unticking clears it.
        focus_row = None
        focus_cast = None
        if focus_id is not None and show_marker:
            matches = df.loc[df["point_id"] == focus_id]
            if not matches.empty:
                focus_row = matches.iloc[0]
                if "cast" in df.columns:
                    cast_value = pd.to_numeric(focus_row["cast"], errors="coerce")
                    if pd.notna(cast_value):
                        focus_cast = int(round(cast_value))
        cast_numbers = (
            pd.to_numeric(df["cast"], errors="coerce").round()
            if "cast" in df.columns else None
        )
        selected_ids = None
        if isinstance(selected_data, dict):
            selected_ids = selected_data.get("selected_ids")
        has_point_selection = bool(selected_ids)
        if has_point_selection:
            ids = np.asarray(selected_ids, dtype=np.int32)
            if "cast" in df.columns:
                mask = np.isin(df["point_id"].to_numpy(), ids)
                selected_casts = set(cast_numbers[mask].dropna().astype(int))
                if focus_cast is not None:
                    selected_casts.add(focus_cast)
                if selected_casts:
                    df = df.loc[cast_numbers.isin(selected_casts)]
            else:
                mask = np.isin(df["point_id"].to_numpy(), ids)
                df = df.loc[mask]
        elif focus_cast is not None and not has_cruise_selection:
            # Only a clicked point: show its whole cast.
            df = df.loc[cast_numbers == focus_cast]
        elif not has_cruise_selection:
            fig.add_annotation(
                text="Select or click points<br>to show profiles",
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
        df = df.copy()
        for col in set(required_cols):
            df[col] = pd.to_numeric(df[col], errors="coerce")
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
            casts = [int(c) for c in unique_casts]
            shown_focus = focus_cast if focus_cast in casts else None
            # Median position of each cast, shown in the legend.
            cast_positions = {
                int(cast_id): _position_label(row["latitude"], row["longitude"])
                for cast_id, row in df.groupby("cast")[["latitude", "longitude"]].median().iterrows()
            }
            # Dot at the Details point's depth: always the trace after the casts.
            dot_x, dot_y = [], []
            if focus_row is not None:
                fx = pd.to_numeric(focus_row.get(color_var), errors="coerce")
                fy = pd.to_numeric(focus_row.get("depth"), errors="coerce")
                if pd.notna(fx) and pd.notna(fy):
                    dot_x, dot_y = [float(fx)], [float(fy)]
            state = {"key": [dataset, csv_file, sub_sample, sampling_mode,
                             color_var, color_map, fontsize, casts]}
            # Only the Details point / marker changed and the same casts are
            # drawn: restyle in place instead of rebuilding every cast trace.
            point_only = {"detail_point_id", "detail_marker", "detail_marker_color"}
            triggered = {prop.split(".")[0] for prop in ctx.triggered_prop_ids}
            if triggered and triggered <= point_only and prev_state == state:
                patched = Patch()
                for i, cast_id in enumerate(casts):
                    style = _cast_style(cast_id, shown_focus, cast_positions.get(cast_id))
                    patched["data"][i]["line"]["width"] = style["width"]
                    patched["data"][i]["marker"]["size"] = style["marker_size"]
                    patched["data"][i]["opacity"] = style["opacity"]
                    patched["data"][i]["name"] = style["name"]
                patched["data"][len(casts)]["x"] = dot_x
                patched["data"][len(casts)]["y"] = dot_y
                patched["data"][len(casts)]["marker"]["line"]["color"] = marker_color or "#e00000"
                return patched, no_update
            # Casts in numeric order so each keeps its trace index for patching;
            # faded casts are see-through, so the bold one stays visible.
            for cast_id, g in df.groupby("cast", dropna=True):
                g = g.sort_values("depth")
                color = cast_colors.get(cast_id, "rgba(0,0,0,0.6)")
                style = _cast_style(cast_id, shown_focus, cast_positions.get(int(cast_id)))
                fig.add_trace(
                    go.Scattergl(
                        x=g[color_var],
                        y=g["depth"],
                        mode="lines+markers",
                        line=dict(color=color, width=style["width"]),
                        marker=dict(size=style["marker_size"], color=color),
                        opacity=style["opacity"],
                        name=style["name"],
                        hovertemplate=(
                            f"<b>Cast:</b> {int(cast_id)}<br>"
                            "<b>Depth:</b> %{y:.1f} m<br>"
                            f"<b>{color_var.replace('_', ' ').capitalize()}:</b> "
                            f"%{{x:.2f}} {get_unit(color_var)}"
                            "<extra></extra>"
                        ),
                    )
                )
            fig.add_trace(
                go.Scatter(
                    x=dot_x,
                    y=dot_y,
                    mode="markers",
                    marker=dict(
                        size=14,
                        color="rgba(0,0,0,0)",
                        line=dict(color=marker_color or "#e00000", width=2.5),
                    ),
                    hoverinfo="skip",
                    showlegend=False,
                )
            )
        else:
            state = None
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
        # Ring at the Details point's depth (median-profile mode; cast mode
        # adds it above as a fixed trace).
        if state is None and show_marker and focus_row is not None:
            focus_x = pd.to_numeric(focus_row.get(color_var), errors="coerce")
            focus_depth = pd.to_numeric(focus_row.get("depth"), errors="coerce")
            if pd.notna(focus_x) and pd.notna(focus_depth):
                fig.add_trace(
                    go.Scatter(
                        x=[focus_x],
                        y=[focus_depth],
                        mode="markers",
                        marker=dict(
                            size=14,
                            color="rgba(0,0,0,0)",
                            line=dict(color=marker_color or "#e00000", width=2.5),
                        ),
                        hoverinfo="skip",
                        showlegend=False,
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
            showlegend=bool(show_legend),
            legend=dict(
                title="Cast · Position",
                font=dict(size=(fontsize if fontsize else 10) - 1),
            ),
        )
        fig.update_yaxes(
            autorange="reversed",
            title=y_text or "Depth (m)",
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
            title=x_text or f"{color_var.replace('_', ' ').capitalize()} ({get_unit(color_var)})",
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
        legend_chars = 0
        if show_legend:
            entries = [len(str(t.name)) for t in fig.data if t.name and t.showlegend is not False]
            if entries:
                legend_chars = max(max(entries), len("Cast · Position"))
        frame = frame_layout(plot_w, plot_h, fontsize if fontsize else 10, legend_chars=legend_chars)
        return apply_frame(fig, frame), state

    # ============================================================
    # === Point Details: click a point, then step with Prev/Next ===
    # ============================================================
    @app.callback(
        Output('detail_point_id', 'data'),
        Input('main_plot', 'clickData'),
        Input('ts_plot', 'clickData'),
        Input('scatter_plot', 'clickData'),
        Input('detail-prev', 'n_clicks'),
        Input('detail-next', 'n_clicks'),
        Input('csv_selector', 'value'),
        State('main_plot_point_ids', 'data'),
        State('ts_plot_point_ids', 'data'),
        State('scatter_plot_point_ids', 'data'),
        State('detail_point_id', 'data'),
        State('dataset_selector', 'value'),
        State('sub_sample', 'value'),
        State('sampling_mode', 'value'),
        State('cruise_track_selection_store', 'data'),
        prevent_initial_call=True
    )
    def select_detail_point(main_click, ts_click, scatter_click, _prev, _next, csv_file,
                            main_point_ids, ts_point_ids, scatter_point_ids, current_id,
                            dataset, sub_sample, sampling_mode, cruise_track_selection):
        """Point shown in Details: the clicked point, or its neighbour in time
        among the rows the plots show (sampling and cruise-track filter applied)."""
        trigger = ctx.triggered_id
        if trigger == "csv_selector":
            return None
        clicks = {
            "main_plot": (main_click, main_point_ids),
            "ts_plot": (ts_click, ts_point_ids),
            "scatter_plot": (scatter_click, scatter_point_ids),
        }
        if trigger in clicks:
            click, point_ids = clicks[trigger]
            if not click or not click.get("points"):
                raise dash.exceptions.PreventUpdate
            point_id = get_point_id_from_event_point(click["points"][0], point_ids)
            if point_id is None:
                raise dash.exceptions.PreventUpdate
            return int(point_id)
        if current_id is None or not csv_file:
            raise dash.exceptions.PreventUpdate
        df = _scatter_frame(dataset, csv_file, sub_sample, sampling_mode, cruise_track_selection)
        if df.empty:
            raise dash.exceptions.PreventUpdate
        if "times" in df.columns:
            df = df.sort_values("times", kind="stable")
        ordered = df["point_id"].to_numpy()
        matches = np.nonzero(ordered == current_id)[0]
        if len(matches):
            position = matches[0] + (1 if trigger == "detail-next" else -1)
        else:
            # Current point was filtered out: continue from the nearest ID.
            ordered = np.sort(ordered)
            position = int(np.searchsorted(ordered, current_id))
            position -= 1 if trigger == "detail-prev" else 0
        if position < 0 or position >= len(ordered):
            raise dash.exceptions.PreventUpdate
        return int(ordered[position])

    # ============================================================
    # === Figure export (assets/export_figure.js) and settings record ===
    # ============================================================
    export_plots = {
        "cruise_track": "Track",
        "main_plot": "Transect",
        "ts_plot": "T-S",
        "profile_plot": "Profile",
        "scatter_plot": "Scatter",
    }

    # Which dropdowns of the plot each export panel can vary: the plot's own X, Y
    # and color (or profile variable) controls.
    multi_slots = {
        "main_plot": {"x": "x_axis_variable", "y": "y_axis_variable",
                      "color": "color_variable", "cmap": "color_map",
                      "vmin": "v_min", "vmax": "v_max",
                      "xlabel": "main_xlabel", "ylabel": "main_ylabel", "clabel": "main_clabel"},
        "ts_plot": {"color": "ts_color_variable", "cmap": "ts_color_map",
                    "vmin": "ts_v_min", "vmax": "ts_v_max",
                    "xlabel": "ts_xlabel", "ylabel": "ts_ylabel", "clabel": "ts_clabel"},
        "scatter_plot": {"x": "scatter_x", "y": "scatter_y",
                         "color": "scatter_color", "cmap": "scatter_color_map",
                         "vmin": "scatter_v_min", "vmax": "scatter_v_max",
                         "xlabel": "scatter_xlabel", "ylabel": "scatter_ylabel",
                         "clabel": "scatter_clabel"},
        "profile_plot": {"color": "profile_variable", "cmap": "profile_color_map",
                         "xlabel": "profile_xlabel", "ylabel": "profile_ylabel"},
    }
    # Color range slots are typed numbers, empty meaning auto; the rest are dropdowns.
    range_slots = ("vmin", "vmax")
    # Label text for this export only; blank means the automatic label.
    text_slots = ("xlabel", "ylabel", "clabel")
    free_slots = range_slots + text_slots
    option_sources = sorted({
        component
        for slots in multi_slots.values()
        for slot, component in slots.items()
        if slot not in free_slots
    })
    slot_names = ("x", "y", "color", "cmap") + free_slots
    range_sources = sorted({
        component
        for slots in multi_slots.values()
        for slot, component in slots.items()
        if slot in free_slots
    })

    @app.callback(
        Output("export-modal-backdrop", "className"),
        Output("export_target", "data"),
        Output("export-title", "children"),
        Output("export-status", "children"),
        Output("export-multi-block", "style"),
        Output("export-multi", "value"),
        [Input(f"export-open-{graph_id}", "n_clicks") for graph_id in export_plots]
        + [Input("export-close-x", "n_clicks")],
        prevent_initial_call=True,
    )
    def toggle_export_panel(*_clicks):
        trigger = ctx.triggered_id
        if trigger == "export-close-x" or not str(trigger).startswith("export-open-"):
            return ["download-modal-backdrop hidden"] + [no_update] * 5
        graph_id = str(trigger)[len("export-open-"):]
        return [
            "download-modal-backdrop",
            graph_id,
            f"Export figure: {export_plots.get(graph_id, graph_id)}",
            "",
            {} if multi_slots.get(graph_id) else {"display": "none"},
            [],
        ]

    # One row per panel; each row has its own dropdowns for what the plot can vary.
    slot_labels = {"x": "X", "y": "Y", "color": "Color", "cmap": "Colormap",
                   "vmin": "Color min", "vmax": "Color max",
                   "xlabel": "X label", "ylabel": "Y label", "clabel": "Color label"}

    # The rows' dropdowns are collected into one store (a list of dicts), so the
    # server callbacks below read plain data instead of pattern-matched values.
    app.clientside_callback(
        """
        function() {
            const rows = {};
            const lists = window.dash_clientside.callback_context.inputs_list || [];
            lists.forEach(function(entries) {
                (entries || []).forEach(function(entry) {
                    const slot = entry.id.type.replace('panel-', '');
                    const index = entry.id.index;
                    rows[index] = rows[index] || {id: index};
                    rows[index][slot] = entry.value;
                });
            });
            return Object.keys(rows).map(Number).sort(function(a, b) { return a - b; })
                .map(function(key) { return rows[key]; });
        }
        """,
        Output("export_rows", "data"),
        *[Input({"type": f"panel-{slot}", "index": ALL}, "value") for slot in slot_names],
        prevent_initial_call=True,
    )

    @app.callback(
        Output("export-panel-rows", "children"),
        Input("export-modal-backdrop", "className"),
        Input("export-panel-add", "n_clicks"),
        Input({"type": "panel-remove", "index": ALL}, "n_clicks"),
        State("export_target", "data"),
        State("export_rows", "data"),
        *[State(component, "options") for component in option_sources],
        *[State(component, "value") for component in option_sources + range_sources],
        prevent_initial_call=True,
    )
    def edit_panel_rows(open_class, _add, _removes, target, stored_rows, *states):
        triggered = ctx.triggered_id
        slots = multi_slots.get(target, {})
        if "hidden" in (open_class or ""):
            raise dash.exceptions.PreventUpdate
        if not slots:
            return []
        # Newly drawn remove buttons report n_clicks=None; only real clicks count.
        if isinstance(triggered, dict) and not (ctx.triggered and ctx.triggered[0].get("value")):
            raise dash.exceptions.PreventUpdate

        options = dict(zip(option_sources, states[: len(option_sources)]))
        live = dict(zip(option_sources + range_sources, states[len(option_sources):]))
        live_row = {slot: live.get(component) for slot, component in slots.items()}

        rows = [dict(row) for row in (stored_rows or [])]
        existing_ids = [row["id"] for row in rows]

        next_id = max(existing_ids, default=-1) + 1
        if triggered == "export-panel-add" and rows:
            # A new panel keeps the variables but not the label text, which belongs to them.
            rows.append({**rows[-1], "id": next_id, **{slot: None for slot in text_slots}})
        elif isinstance(triggered, dict):
            rows = [row for row in rows if row["id"] != triggered["index"]]
        elif triggered == "export-modal-backdrop" or not rows:
            rows = []
        if not rows:
            rows = [{"id": next_id, **live_row}]

        children = []
        for position, row in enumerate(rows):
            fields = []
            label_fields = []
            for slot in slot_names:
                if slot not in slots:
                    continue
                label = "Variable" if (target == "profile_plot" and slot == "color") else slot_labels[slot]
                if slot in text_slots:
                    label_fields.append(html.Div([
                        html.Label(label),
                        dcc.Input(
                            id={"type": f"panel-{slot}", "index": row["id"]},
                            type="text",
                            value=row.get(slot),
                            placeholder="auto",
                        ),
                    ], className="export-panel-field"))
                    continue
                if slot in range_slots:
                    control = dcc.Input(
                        id={"type": f"panel-{slot}", "index": row["id"]},
                        type="number",
                        value=row.get(slot),
                        placeholder="auto",
                    )
                    field_class = "export-panel-field export-panel-range"
                else:
                    control = dcc.Dropdown(
                        id={"type": f"panel-{slot}", "index": row["id"]},
                        options=options.get(slots[slot]) or [],
                        value=row.get(slot),
                        clearable=False,
                    )
                    field_class = "export-panel-field"
                fields.append(html.Div([html.Label(label), control], className=field_class))
            children.append(html.Div([
                html.Span(f"({chr(65 + position % 26)})", className="export-panel-letter"),
                *fields,
                html.Button(
                    "×",
                    id={"type": "panel-remove", "index": row["id"]},
                    className="modal-close-button",
                    title="Remove this panel",
                    style={} if len(rows) > 1 else {"visibility": "hidden"},
                ),
                html.Details([
                    html.Summary("Labels"),
                    html.Div(label_fields, className="export-panel-label-fields"),
                ], className="export-panel-labels"),
            ], className="export-panel-row"))
        return children

    # A page preset fills the page size, which can then be edited to anything.
    app.clientside_callback(
        """
        function(preset) {
            if (!preset || preset === 'none') {
                return [null, null];
            }
            return preset.split('x').map(Number);
        }
        """,
        Output("export-page-w", "value"),
        Output("export-page-h", "value"),
        Input("export-fit", "value"),
        prevent_initial_call=True,
    )

    app.clientside_callback(
        ClientsideFunction(namespace="exportFigure", function_name="exportFigure"),
        Output("export_sink", "data"),
        Input("export-download-button", "n_clicks"),
        State("export_target", "data"),
        State("export-format", "value"),
        State("export-width", "value"),
        State("export-dpi", "value"),
        State("export-background", "value"),
        State("export-multi", "value"),
        prevent_initial_call=True,
    )

    # The panels of a multi-panel export are composed in the browser.
    app.clientside_callback(
        ClientsideFunction(namespace="exportFigure", function_name="composePanels"),
        Output("export_sink_multi", "data"),
        Input("export_panels", "data"),
        State("export-format", "value"),
        State("export-width", "value"),
        State("export-dpi", "value"),
        State("export-background", "value"),
        State("export-rows", "value"),
        State("export-cols", "value"),
        State("export-letters", "value"),
        State("export_target", "data"),
        State("export-multi", "value"),
        State("export-page-w", "value"),
        State("export-page-h", "value"),
        State("export-margin", "value"),
        prevent_initial_call=True,
    )

    # Controls recorded in the settings file: (section, name, component id).
    settings_fields = [
        ("data", "dataset", "dataset_selector"),
        ("data", "file", "csv_selector"),
        ("data", "bin_size", "sub_sample"),
        ("data", "sampling_mode", "sampling_mode"),
        ("data", "max_time_gap_s", "max_gap_seconds"),
        ("data", "marker_size", "size"),
        ("data", "hidden_opacity", "hidden_opacity"),
        ("data", "font_size", "plot_font_size"),
        ("cruise_track", "x_axis", "cruise_track_xaxis"),
        ("cruise_track", "y_axis", "cruise_track_yaxis"),
        ("cruise_track", "x_label", "track_xlabel"),
        ("cruise_track", "y_label", "track_ylabel"),
        ("main_plot", "x_axis", "x_axis_variable"),
        ("main_plot", "y_axis", "y_axis_variable"),
        ("main_plot", "color", "color_variable"),
        ("main_plot", "colormap", "color_map"),
        ("main_plot", "color_min", "v_min"),
        ("main_plot", "color_max", "v_max"),
        ("main_plot", "depth_min", "z_min"),
        ("main_plot", "depth_max", "z_max"),
        ("main_plot", "latitude_min", "lat_min"),
        ("main_plot", "latitude_max", "lat_max"),
        ("main_plot", "longitude_min", "lon_min"),
        ("main_plot", "longitude_max", "lon_max"),
        ("main_plot", "colorbar", "main_colorbar_pos"),
        ("main_plot", "x_label", "main_xlabel"),
        ("main_plot", "y_label", "main_ylabel"),
        ("main_plot", "color_label", "main_clabel"),
        ("ts_plot", "color", "ts_color_variable"),
        ("ts_plot", "colormap", "ts_color_map"),
        ("ts_plot", "color_min", "ts_v_min"),
        ("ts_plot", "color_max", "ts_v_max"),
        ("ts_plot", "colorbar", "ts_colorbar_pos"),
        ("ts_plot", "x_label", "ts_xlabel"),
        ("ts_plot", "y_label", "ts_ylabel"),
        ("ts_plot", "color_label", "ts_clabel"),
        ("profile_plot", "variable", "profile_variable"),
        ("profile_plot", "colormap", "profile_color_map"),
        ("profile_plot", "legend", "profile_legend"),
        ("profile_plot", "x_label", "profile_xlabel"),
        ("profile_plot", "y_label", "profile_ylabel"),
        ("scatter_plot", "x", "scatter_x"),
        ("scatter_plot", "y", "scatter_y"),
        ("scatter_plot", "color", "scatter_color"),
        ("scatter_plot", "colormap", "scatter_color_map"),
        ("scatter_plot", "color_min", "scatter_v_min"),
        ("scatter_plot", "color_max", "scatter_v_max"),
        ("scatter_plot", "x_min", "scatter_x_min"),
        ("scatter_plot", "x_max", "scatter_x_max"),
        ("scatter_plot", "y_min", "scatter_y_min"),
        ("scatter_plot", "y_max", "scatter_y_max"),
        ("scatter_plot", "fit_degree", "scatter_fit_degree"),
        ("scatter_plot", "colorbar", "scatter_colorbar_pos"),
        ("scatter_plot", "x_label", "scatter_xlabel"),
        ("scatter_plot", "y_label", "scatter_ylabel"),
        ("scatter_plot", "color_label", "scatter_clabel"),
    ]

    @app.callback(
        Output("export_settings_download", "data"),
        Input("export-settings-button", "n_clicks"),
        State("export_target", "data"),
        State("export-format", "value"),
        State("export-width", "value"),
        State("export-dpi", "value"),
        State("export-background", "value"),
        State("url", "search"),
        State("cruise_track_selection_store", "data"),
        [State(component_id, "value") for _, _, component_id in settings_fields],
        prevent_initial_call=True,
    )
    def download_settings(_n, target, fmt, width_in, dpi, background,
                          url_search, track_selection, *values):
        import json

        sections: dict[str, dict] = {}
        for (section, name, _component), value in zip(settings_fields, values):
            if section in ("data", target):
                sections.setdefault(section, {})[name] = value
        selection = track_selection or {}
        selected_ids = selection.get("selected_ids")
        record = {
            "figure": export_plots.get(target, target),
            "exported_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "software": {
                "name": "Stingray Dashboard",
                "version": DASHBOARD_VERSION,
                "citation": CITATION_URL,
            },
            "export": {
                "format": fmt,
                "width_in": width_in,
                "height_in": "scaled with the width, as on screen",
                "dpi": dpi,
                "background": background,
            },
            "track_selection": (
                f"{len(selected_ids)} points selected on the track plot"
                if selected_ids else "all points"
            ),
            "settings": sections,
            "url_parameters": (url_search or "").lstrip("?"),
        }
        dataset = sections.get("data", {}).get("dataset") or "dataset"
        csv_file = sections.get("data", {}).get("file") or "file"
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", f"{dataset}_{csv_file}_{target}_settings").strip("_")
        return dcc.send_string(json.dumps(record, indent=2, default=str), f"{name}.json")

    # The refresh button also resets every plot: Home on each graph, the
    # cruise-track selection highlight cleared (assets/reset_view.js), and the
    # stored selections dropped so all plots show every point again.
    app.clientside_callback(
        ClientsideFunction(namespace="resetView", function_name="resetAll"),
        Output('reset_view_sink', 'data'),
        Input('refresh-button', 'n_clicks'),
        prevent_initial_call=True,
    )

    @app.callback(
        Output("cruise_track_selection_store", "data", allow_duplicate=True),
        Output("main_plot_selected_data", "data", allow_duplicate=True),
        Input("refresh-button", "n_clicks"),
        State("cruise_track_selection_store", "data"),
        prevent_initial_call=True,
    )
    def reset_selections(_n_clicks, previous):
        previous = previous or {}
        return {"mode": "all", "selected_ids": None, "_key": previous.get("_key")}, None

    # Ring the Details point on the transect, T-S and scatter plots,
    # browser-side (assets/details.js).
    app.clientside_callback(
        ClientsideFunction(namespace="details", function_name="markPoint"),
        Output('detail_marker_sink', 'data'),
        Input('detail_point_id', 'data'),
        Input('detail_marker', 'value'),
        Input('detail_marker_color', 'value'),
    )

    @app.callback(
        Output('click-output', 'children'),
        Input('detail_point_id', 'data'),
        State('dataset_selector', 'value'),
        State('csv_selector', 'value'),
        State('sub_sample', 'value'),
        State('sampling_mode', 'value'),
    )
    def display_click_data(point_id, dataset, csv_file, sub_sample, sampling_mode):
        """
        Display detailed information for the current Details point.
        Fetches the full row server-side using point_id.
        """
        if point_id is None:
            return "Click on a point to see full details."
        try:
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
            media_streams = {}
            for column in row.index:
                column = str(column)
                if column == "media":
                    stream, frame_column = "1", "frame"
                else:
                    match = re.fullmatch(r"media_(.+)", column)
                    if not match:
                        continue
                    stream = match.group(1)
                    frame_column = f"frame_{stream}"
                if stream in media_streams:
                    continue
                label, href = make_media_link(row.get(column), row.get(frame_column))
                if href:
                    media_streams[stream] = (label, href)
            meta_cols = {
                "point_id",
                "times", "latitude", "longitude", "depth", "timestamp", "matdate",
                "cruise", "cast",
            }
            # Cruise and cast identify the point, so they sit with the metadata
            # when the file has them.
            identity_rows = []
            cruise_value = clean(row.get("cruise"))
            if cruise_value is not None:
                identity_rows.append(info_row("🚢 Cruise:", str(cruise_value)))
            cast_raw = clean(row.get("cast"))
            cast_value = (
                pd.to_numeric(cast_raw, errors="coerce") if cast_raw is not None else np.nan
            )
            if pd.notna(cast_value):
                identity_rows.append(info_row("🔢 Cast:", str(int(round(cast_value)))))
            variable_details = []
            for var, value in row.items():
                var = str(var)
                if var in meta_cols or is_meta_var(var) or var.endswith("_std"):
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
                                "backgroundColor": "#fff",
                            },
                        ),
                    ],
                    key=f"image-block-{point_id}-{key_suffix}",
                    className="frame-block",
                )

            return html.Div(
                [
                    *[
                        image_block(
                            f"📽️ Camera stream {stream_number}:",
                            label,
                            href,
                            f"camera{stream_number}",
                        )
                        for stream_number, (label, href) in sorted(
                            media_streams.items(),
                            key=lambda item: (
                                (0, int(item[0]), "") if item[0].isdigit() else (1, 0, item[0])
                            ),
                        )
                    ],

                    info_row("⏳ Time:", format_time(row.get("times"))),
                    info_row("🌍 Latitude:", format_number(row.get("latitude"), "°")),
                    info_row("🌍 Longitude:", format_number(row.get("longitude"), "°")),
                    info_row("🌊 Depth:", format_number(row.get("depth"), " m")),
                    *identity_rows,

                    html.Hr(),

                    *variable_details,
                ]
            )
        except Exception as e:
            return f"⚠️ Error processing click data: {str(e)}"

    # ============================================================
    # === Several panels in one figure ===
    # ============================================================
    # Each panel is the plot's own figure, built by the plot's own callback with
    # the panel's variables swapped in, so it matches the plot on screen. The
    # browser (assets/export_figure.js) then lays the panels out in a grid.
    def _builder_dependencies(output_id, marker_input):
        """(component id, property) of a plot callback's inputs, then its states."""
        for key, spec in app.callback_map.items():
            if f"{output_id}.figure" not in key:
                continue
            inputs = [(dep["id"], dep["property"]) for dep in spec["inputs"]]
            if (marker_input, "value") not in inputs:
                continue
            return inputs + [(dep["id"], dep["property"]) for dep in spec["state"]]
        raise RuntimeError(f"No callback builds {output_id}")

    # One builder for every plot: separate callbacks with the same output and
    # input collapse into a single registration in Dash.
    panel_builders = {
        "main_plot": (update_main_plot, "main_plot", "x_axis_variable"),
        "ts_plot": (update_ts_plot, "ts_plot", "ts_color_variable"),
        "scatter_plot": (update_scatter_plot, "scatter_plot", "scatter_x"),
        "profile_plot": (update_profile_plot, "profile_plot", "profile_variable"),
    }
    panel_file_labels = {
        "main_plot": "transect",
        "ts_plot": "ts_temperature-salinity",
        "scatter_plot": "scatter",
        "profile_plot": "profile",
    }
    panel_dependencies = {
        target: _builder_dependencies(output_id, marker)
        for target, (_builder, output_id, marker) in panel_builders.items()
    }
    panel_states = []
    for dependency_list in panel_dependencies.values():
        for dependency in dependency_list:
            if dependency not in panel_states:
                panel_states.append(dependency)

    @app.callback(
        Output("export_panels", "data", allow_duplicate=True),
        Input("export-download-button", "n_clicks"),
        State("export_target", "data"),
        State("export_rows", "data"),
        *[State(component, prop) for component, prop in panel_states],
        prevent_initial_call=True,
    )
    def build_panels(n_clicks, target, rows, *values):
        if target not in panel_builders:
            raise dash.exceptions.PreventUpdate
        import json

        if not rows:
            return {"error": "Add at least one panel."}
        builder = panel_builders[target][0]
        dependencies = panel_dependencies[target]
        current = dict(zip(panel_states, values))
        try:
            figures = []
            names = []
            for row in rows:
                args = [current[dependency] for dependency in dependencies]
                for slot, component in multi_slots[target].items():
                    # A blank color range means auto, whatever the plot shows.
                    if slot in free_slots or row.get(slot) is not None:
                        args[dependencies.index((component, "value"))] = row.get(slot) or None
                result = builder(*args)
                figure = result[0] if isinstance(result, tuple) else result
                figures.append(json.loads(figure.to_json()))
                # File name from this panel's own variables, like a single export.
                chosen = [
                    args[dependencies.index((multi_slots[target][slot], "value"))]
                    for slot in ("x", "y", "color")
                    if slot in multi_slots[target]
                ]
                config = _plot_export_config(
                    current.get(("dataset_selector", "value")),
                    current.get(("csv_selector", "value")),
                    panel_file_labels[target],
                    *chosen,
                )
                names.append(config["toImageButtonOptions"]["filename"])
            # Panels with the same variables still get distinct names.
            seen = {}
            for position, name in enumerate(names):
                seen[name] = seen.get(name, 0) + 1
                if seen[name] > 1:
                    names[position] = f"{name}_{seen[name]}"
        except Exception as error:  # reported in the export dialog
            return {"error": f"Could not build the panels: {error}"}
        return {"figures": figures, "names": names, "target": target, "stamp": n_clicks}
