from __future__ import annotations

import numpy as np
import pandas as pd

from stingray_dashboard import data
from stingray_dashboard.app import create_app


def _write_example_csv(work_dir):
    """Create a compact dataset that exercises casts and sensor plotting."""
    # Clear process-wide caches so each temporary dataset is tested independently.
    data.DATA_CACHE.clear()
    data.AVERAGE_CACHE.clear()

    # Build two short casts with all coordinates required by dashboard plots.
    frame = pd.DataFrame(
        {
            "temperature": [10.0, 9.5, 9.0, 11.0, 10.5, 10.0],
            "salinity": [33.0, 33.1, 33.2, 32.8, 32.9, 33.0],
            "depth": [0.0, 10.0, 20.0, 0.0, 10.0, 20.0],
            "cast": [1, 1, 1, 2, 2, 2],
            "latitude": [40.0, 40.0, 40.0, 40.1, 40.1, 40.1],
            "longitude": [-70.0, -70.0, -70.0, -70.1, -70.1, -70.1],
            "altitude": [5.0, 9999.99, 4.0, 6.0, 5.0, 4.0],
            "chlorophyll": [1.0, 1.2, 1.4, 0.8, 1.0, 1.2],
            "times": pd.date_range("2023-01-01", periods=6, freq="min"),
        }
    )

    # Write the file using the dashboard's expected work-directory contract.
    dataset_dir = work_dir / "data" / "stingray"
    dataset_dir.mkdir(parents=True)
    frame.to_csv(dataset_dir / "example.csv", index=False)


def _post_callback(client, output, outputs, inputs, changed, states=None):
    """Submit the same JSON payload used by Dash's browser renderer."""
    # Convert compact tuples into Dash callback request records.
    payload = {
        "output": output,
        "outputs": outputs,
        "inputs": [
            {"id": component_id, "property": prop, "value": value}
            for component_id, prop, value in inputs
        ],
        "state": [
            {"id": component_id, "property": prop, "value": value}
            for component_id, prop, value in (states or [])
        ],
        "changedPropIds": [changed],
    }

    # Execute the callback through Flask so serialization is tested too.
    response = client.post("/_dash-update-component", json=payload)
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.get_json()


def test_canonicalize_columns_replaces_altitude_sentinel():
    """Invalid instrument altitude sentinels must not enter calculations."""
    # Preserve valid values while replacing the documented sentinel with NaN.
    frame = pd.DataFrame({"altitude": [3.5, 9999.99, 9999.98, np.nan]})
    result = data.canonicalize_columns(frame)

    assert result.loc[0, "altitude"] == 3.5
    assert np.isnan(result.loc[1, "altitude"])
    assert result.loc[2, "altitude"] == 9999.98
    assert np.isnan(result.loc[3, "altitude"])


def test_unmatched_mirrored_selection_selects_no_points(tmp_path):
    """An ID absent from another plot must not clear its selection styling."""
    # Create the app and locate Dash's generated duplicate-output callback key.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)
    callback_key = next(
        key for key in app.callback_map
        if key.startswith("..main_plot.figure@")
    )

    # Select an ID that does not exist in either compact point-ID store.
    result = _post_callback(
        app.server.test_client(),
        callback_key,
        [
            {"id": "main_plot", "property": "figure"},
            {"id": "ts_plot", "property": "figure"},
        ],
        [
            (
                "main_plot",
                "selectedData",
                {
                    "points": [
                        {
                            "customdata": 999,
                            "curveNumber": 0,
                            "pointNumber": 0,
                        }
                    ]
                },
            ),
            ("ts_plot", "selectedData", None),
        ],
        "main_plot.selectedData",
        [
            ("main_plot_point_ids", "data", [[0, 1, 2]]),
            ("ts_plot_point_ids", "data", [[0, 1, 2]]),
        ],
    )

    # Dash represents "select none" as an empty list, not null.
    for plot_id in ("main_plot", "ts_plot"):
        operation = result["response"][plot_id]["figure"]["operations"][0]
        assert operation["params"]["value"] == []


def test_cast_coloring_uses_one_continuous_trace(tmp_path):
    """Many casts should not create one expensive trace per cast."""
    # Build the app around the compact cast dataset.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Request the transect using cast as its color variable.
    result = _post_callback(
        app.server.test_client(),
        "main_plot.figure",
        {"id": "main_plot", "property": "figure"},
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("sub_sample", "value", 1),
            ("sampling_mode", "value", "subsample"),
            ("x_axis_variable", "value", "latitude"),
            ("y_axis_variable", "value", "depth"),
            ("color_variable", "value", "cast"),
            ("color_map", "value", "Plotly"),
            ("size", "value", 5),
            ("v_min", "value", None),
            ("v_max", "value", None),
            ("z_min", "value", 0),
            ("z_max", "value", 30),
            ("hidden_opacity", "value", 0.1),
            ("plot_font_size", "value", 14),
            ("bathymetry", "value", []),
            ("station", "value", []),
            (
                "cruise_track_selection_store",
                "data",
                {"mode": "all", "selected_ids": None},
            ),
            ("main_plot", "relayoutData", None),
        ],
        "color_variable.value",
    )

    # Both casts are encoded in one WebGL trace with a continuous color axis.
    figure = result["response"]["main_plot"]["figure"]
    assert len(figure["data"]) == 1
    assert figure["data"][0]["type"] == "scattergl"
    assert figure["data"][0]["marker"]["coloraxis"] == "coloraxis"


def test_cast_profiles_use_webgl_traces(tmp_path):
    """Profile rendering should use WebGL for many simultaneous casts."""
    # Build the app around the compact cast dataset.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Request profiles for both casts.
    result = _post_callback(
        app.server.test_client(),
        "profile_plot.figure",
        {"id": "profile_plot", "property": "figure"},
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("sub_sample", "value", 1),
            ("sampling_mode", "value", "subsample"),
            ("profile_variable", "value", "chlorophyll"),
            ("profile_color_map", "value", "Plotly"),
            ("plot_font_size", "value", 14),
            ("main_plot_selected_data", "data", None),
            (
                "cruise_track_selection_store",
                "data",
                {"mode": "all", "selected_ids": None},
            ),
        ],
        "profile_variable.value",
    )

    # Each cast remains individually selectable while using WebGL rendering.
    figure = result["response"]["profile_plot"]["figure"]
    assert len(figure["data"]) == 2
    assert all(trace["type"] == "scattergl" for trace in figure["data"])
