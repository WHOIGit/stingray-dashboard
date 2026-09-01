from __future__ import annotations

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


def test_dashboard_loads_example_dataset(tmp_path):
    """The dashboard should start with the example dataset available."""
    # Create the representative dashboard input and start the application.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Confirm the application and its representative dataset are discoverable.
    assert app is not None
    assert data.scan_datasets() == ["stingray"]
    assert data.get_csv_files("stingray") == ["example"]


def test_selected_csv_can_be_downloaded_and_logs_audit_entry(tmp_path):
    """The selected raw CSV should download and leave an audit record."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    client = app.server.test_client()
    result = _post_callback(
        client,
        "..download_dataframe_csv.data...download-modal-backdrop.className...download-status.children..",
        [
            {"id": "download_dataframe_csv", "property": "data"},
            {"id": "download-modal-backdrop", "property": "className"},
            {"id": "download-status", "property": "children"},
        ],
        [
            ("download-button", "n_clicks", 1),
            ("download-confirm-button", "n_clicks", 1),
            ("download-cancel-button", "n_clicks", None),
            ("download-cancel-x", "n_clicks", None),
        ],
        "download-confirm-button.n_clicks",
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("download-name", "value", "Jane Scientist"),
            ("download-email", "value", "science@example.org"),
            ("download-institution", "value", "WHOI"),
        ],
    )

    download = result["response"]["download_dataframe_csv"]["data"]
    assert download["filename"] == "example.csv"
    assert download["base64"] is True
    assert result["response"]["download-modal-backdrop"]["className"] == (
        "download-modal-backdrop hidden"
    )
    assert result["response"]["download-status"]["children"] == ""

    log_path = tmp_path / "logs" / "dashboard_downloads.log"
    assert log_path.is_file()
    log_text = log_path.read_text(encoding="utf-8")
    assert "name=Jane Scientist" in log_text
    assert "email=science@example.org" in log_text
    assert "institution=WHOI" in log_text
    assert "dataset=stingray" in log_text
    assert "file=example.csv" in log_text


def test_download_button_opens_email_modal(tmp_path):
    """The main download button should open the required-information modal."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    result = _post_callback(
        app.server.test_client(),
        "..download_dataframe_csv.data...download-modal-backdrop.className...download-status.children..",
        [
            {"id": "download_dataframe_csv", "property": "data"},
            {"id": "download-modal-backdrop", "property": "className"},
            {"id": "download-status", "property": "children"},
        ],
        [
            ("download-button", "n_clicks", 1),
            ("download-confirm-button", "n_clicks", None),
            ("download-cancel-button", "n_clicks", None),
            ("download-cancel-x", "n_clicks", None),
        ],
        "download-button.n_clicks",
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("download-name", "value", None),
            ("download-email", "value", None),
            ("download-institution", "value", None),
        ],
    )

    assert "download_dataframe_csv" not in result["response"]
    assert result["response"]["download-modal-backdrop"]["className"] == (
        "download-modal-backdrop"
    )
    assert result["response"]["download-status"]["children"] == ""


def test_download_modal_button_requires_complete_contact_info(tmp_path):
    """The modal download action should only enable for complete valid contact info."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    invalid = _post_callback(
        app.server.test_client(),
        "download-confirm-button.disabled",
        {"id": "download-confirm-button", "property": "disabled"},
        [
            ("download-name", "value", "Jane Scientist"),
            ("download-email", "value", "not-an-email"),
            ("download-institution", "value", "WHOI"),
        ],
        "download-email.value",
    )
    assert invalid["response"]["download-confirm-button"]["disabled"] is True

    valid = _post_callback(
        app.server.test_client(),
        "download-confirm-button.disabled",
        {"id": "download-confirm-button", "property": "disabled"},
        [
            ("download-name", "value", "Jane Scientist"),
            ("download-email", "value", "science@example.org"),
            ("download-institution", "value", "WHOI"),
        ],
        "download-email.value",
    )
    assert valid["response"]["download-confirm-button"]["disabled"] is False


def test_selected_csv_download_requires_valid_email(tmp_path):
    """Downloads should not start until the user provides a valid email."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    result = _post_callback(
        app.server.test_client(),
        "..download_dataframe_csv.data...download-modal-backdrop.className...download-status.children..",
        [
            {"id": "download_dataframe_csv", "property": "data"},
            {"id": "download-modal-backdrop", "property": "className"},
            {"id": "download-status", "property": "children"},
        ],
        [
            ("download-button", "n_clicks", 1),
            ("download-confirm-button", "n_clicks", 1),
            ("download-cancel-button", "n_clicks", None),
            ("download-cancel-x", "n_clicks", None),
        ],
        "download-confirm-button.n_clicks",
        [
            ("dataset_selector", "value", "stingray"),
            ("csv_selector", "value", "example"),
            ("download-name", "value", "Jane Scientist"),
            ("download-email", "value", "not-an-email"),
            ("download-institution", "value", "WHOI"),
        ],
    )

    assert "download_dataframe_csv" not in result["response"]
    assert result["response"]["download-modal-backdrop"]["className"] == (
        "download-modal-backdrop"
    )
    assert result["response"]["download-status"]["children"] == (
        "Enter a valid email to download."
    )


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


def test_main_plot_renders_example_dataset(tmp_path):
    """The main depth plot should render the representative observations."""
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
            ("lat_min", "value", None),
            ("lat_max", "value", None),
            ("lon_min", "value", None),
            ("lon_max", "value", None),
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

    # The figure contains the complete six-row example dataset.
    figure = result["response"]["main_plot"]["figure"]
    assert figure["data"]
    assert sum(len(trace["x"]) for trace in figure["data"]) == 6
    assert figure["layout"]["xaxis"]["title"]["text"] == "Latitude"
    assert figure["layout"]["yaxis"]["title"]["text"] == "Depth (m)"


def test_main_plot_uses_manual_coordinate_limits(tmp_path):
    """Manual lat/lon limits should apply to either main-plot axis."""
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

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
            ("y_axis_variable", "value", "longitude"),
            ("color_variable", "value", "cast"),
            ("color_map", "value", "Plotly"),
            ("size", "value", 5),
            ("v_min", "value", None),
            ("v_max", "value", None),
            ("z_min", "value", 0),
            ("z_max", "value", 30),
            ("lat_min", "value", 39.9),
            ("lat_max", "value", 40.2),
            ("lon_min", "value", -70.2),
            ("lon_max", "value", -69.9),
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
        "lat_min.value",
    )

    figure = result["response"]["main_plot"]["figure"]
    assert figure["layout"]["xaxis"]["range"] == [40.2, 39.9]
    assert "autorange" not in figure["layout"]["xaxis"]
    assert figure["layout"]["yaxis"]["range"] == [-70.2, -69.9]


def test_ts_plot_renders_numeric_and_categorical_data(tmp_path):
    """The T-S plot should accept scientific values and descriptive classes."""
    # Build the application around the representative dataset.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Exercise the two supported color-data contracts through the same plot.
    for color_variable, color_map in [
        ("chlorophyll", "Viridis"),
        ("sample_type", "Plotly"),
    ]:
        result = _post_callback(
            app.server.test_client(),
            "ts_plot.figure",
            {"id": "ts_plot", "property": "figure"},
            [
                ("dataset_selector", "value", "stingray"),
                ("csv_selector", "value", "example"),
                ("sub_sample", "value", 1),
                ("sampling_mode", "value", "subsample"),
                ("ts_color_variable", "value", color_variable),
                ("ts_color_map", "value", color_map),
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

        # The scatter trace contains every example observation.
        figure = result["response"]["ts_plot"]["figure"]
        assert len(figure["data"][0]["x"]) == 6
        assert figure["layout"]["xaxis"]["title"]["text"] == "Salinity (psu)"
        assert figure["layout"]["yaxis"]["title"]["text"] == "Temperature (°C)"


def test_ts_selection_initializes_profile_plot(tmp_path):
    """A T-S selection should populate the shared profile-selection store."""
    # Build the app and locate the selection-store callback.
    _write_example_csv(tmp_path)
    app = create_app(tmp_path)

    # Select point 3 in the T-S plot using its compact point-ID mapping.
    selection = _post_callback(
        app.server.test_client(),
        "main_plot_selected_data.data",
        {"id": "main_plot_selected_data", "property": "data"},
        [
            ("main_plot", "selectedData", None),
            (
                "ts_plot",
                "selectedData",
                {
                    "points": [
                        {
                            "curveNumber": 0,
                            "pointNumber": 0,
                        }
                    ]
                },
            ),
        ],
        "ts_plot.selectedData",
        [
            ("main_plot_point_ids", "data", [[0, 1, 2]]),
            ("ts_plot_point_ids", "data", [[3, 4, 5]]),
        ],
    )
    selected_data = selection["response"]["main_plot_selected_data"]["data"]
    assert selected_data == {"selected_ids": [3]}

    # Feed the shared selection into the profile callback as the browser does.
    profile = _post_callback(
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
            ("main_plot_selected_data", "data", selected_data),
            (
                "cruise_track_selection_store",
                "data",
                {"mode": "all", "selected_ids": None},
            ),
        ],
        "main_plot_selected_data.data",
    )

    # Point 3 belongs to cast 2, so its full vertical profile is displayed.
    figure = profile["response"]["profile_plot"]["figure"]
    assert len(figure["data"]) == 1
    assert figure["data"][0]["name"] == "Cast 2"


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


def test_main_plot_selection_renders_profiles(tmp_path):
    """Main-plot selections should render the corresponding complete casts."""
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
    assert {trace["name"] for trace in figure["data"]} == {"Cast 1", "Cast 2"}
