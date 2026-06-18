from __future__ import annotations

import os

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
            "sample_type": ["surface", "surface", "deep", "surface", "deep", "deep"],
            "times": pd.date_range("2023-01-01", periods=6, freq="min"),
        }
    )

    # Write the file using the dashboard's expected work-directory contract.
    dataset_dir = work_dir / "data" / "stingray"
    dataset_dir.mkdir(parents=True)
    frame.to_csv(dataset_dir / "example.csv", index=False)


def test_read_only_workspace_without_misc_uses_packaged_references(tmp_path):
    """A data-only workspace must start without creating override directories."""
    # Create the complete dataset before removing directory write permissions.
    _write_example_csv(tmp_path)
    original_mode = tmp_path.stat().st_mode
    os.chmod(tmp_path, 0o555)

    try:
        # Build the application against a workspace that cannot accept writes.
        app = create_app(tmp_path)

        # Confirm startup succeeded and packaged reference tables were loaded.
        assert app is not None
        assert data.stations is not None
        assert data.bathy is not None

        # The optional override directory must not be created as a side effect.
        assert not (tmp_path / "misc").exists()
    finally:
        # Restore permissions so pytest can remove the temporary workspace.
        os.chmod(tmp_path, original_mode)


def test_missing_data_and_misc_directories_are_not_created(tmp_path):
    """An empty input workspace must remain unchanged during application startup."""
    # Start the application before creating any dashboard input directories.
    app = create_app(tmp_path)

    # A valid empty dashboard still loads its packaged auxiliary references.
    assert app is not None
    assert data.stations is not None
    assert data.bathy is not None
    assert data.scan_datasets() == []

    # Input paths are observational and therefore must never be materialized.
    assert not (tmp_path / "data").exists()
    assert not (tmp_path / "misc").exists()


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


def test_ts_plot_supports_categorical_color_variable(tmp_path):
    """String color variables should render categories instead of raising."""
    # Build the application with a categorical sensor-like column.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Request the T-S plot using strings for marker color.
    result = _post_callback(
        app.server.test_client(),
        "ts_plot.figure",
        {"id": "ts_plot", "property": "figure"},
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("sub_sample", "value", 1),
            ("sampling_mode", "value", "subsample"),
            ("ts_color_variable", "value", "sample_type"),
            ("ts_color_map", "value", "Plotly"),
            ("size", "value", 5),
            ("ts_v_min", "value", None),
            ("ts_v_max", "value", None),
            ("hidden_opacity", "value", 0.1),
            ("plot_font_size", "value", 14),
            (
                "cruise_track_selection_store",
                "data",
                {"mode": "all", "selected_ids": None},
            ),
        ],
        "ts_color_variable.value",
    )

    # Categories remain in one WebGL trace and retain point IDs for linking.
    figure = result["response"]["ts_plot"]["figure"]
    scatter = figure["data"][0]
    assert scatter["type"] == "scattergl"
    assert len(scatter["marker"]["color"]) == 6
    assert scatter["customdata"][0] == [0, "surface"]
    assert figure["layout"]["coloraxis"]["colorbar"]["ticktext"] == [
        "deep",
        "surface",
    ]


def test_profile_requires_selection_before_plotting_casts(tmp_path):
    """Profile rendering should not draw every cast before a selection is made."""
    # Build the app around the compact cast dataset.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Request profiles without selecting points in the main or T-S plot.
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

    # The profile panel should stay idle instead of drawing every cast.
    figure = result["response"]["profile_plot"]["figure"]
    assert figure.get("data", []) == []
    assert "Select points" in figure["layout"]["annotations"][0]["text"]


def test_selected_cast_profiles_use_webgl_traces(tmp_path):
    """Selected points should expand to their full casts and use WebGL traces."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

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
            ("main_plot_selected_data", "data", {"selected_ids": [0, 3]}),
            (
                "cruise_track_selection_store",
                "data",
                {"mode": "all", "selected_ids": None},
            ),
        ],
        "main_plot_selected_data.data",
    )

    figure = result["response"]["profile_plot"]["figure"]
    assert len(figure["data"]) == 2
    assert all(trace["type"] == "scattergl" for trace in figure["data"])
