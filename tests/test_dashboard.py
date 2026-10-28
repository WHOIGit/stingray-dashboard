from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from stingray_dashboard.app import create_app
from stingray_dashboard.callbacks import (
    _deserialize_url_value,
    _get_cruise_track_selection_ids,
    _url_param_text,
)
from stingray_dashboard.data import canonicalize_columns
from stingray_dashboard.plot_utils import (
    apply_selection,
    get_point_id_from_event_point,
    selection_shapes,
)
from stingray_dashboard.science import polynomial_fit


def test_canonicalize_columns_normalizes_non_string_labels() -> None:
    df = pd.DataFrame([[1, 2]], columns=[b"media", pd.Timestamp("2026-01-01")])

    result = canonicalize_columns(df)

    assert list(result.columns[:2]) == ["media", "2026-01-01 00:00:00"]


def test_selection_id_accepts_nested_customdata() -> None:
    assert get_point_id_from_event_point({"customdata": [42, "temperature"]}) == 42


def test_selection_id_falls_back_to_trace_point_ids() -> None:
    event_point = {"curveNumber": 1, "pointNumber": 2}
    trace_point_ids = [[10, 11], [20, 21, 22]]

    assert get_point_id_from_event_point(event_point, trace_point_ids) == 22


def test_cruise_track_selection_uses_stable_point_ids() -> None:
    selected = {
        "points": [
            {"customdata": [12], "pointNumber": 4},
            {"meta": 27, "pointNumber": 9},
        ]
    }

    assert _get_cruise_track_selection_ids(selected) == [12, 27]


def test_polynomial_fit_recovers_quadratic_and_skips_nan() -> None:
    x = np.linspace(-3, 3, 50)
    y = 2 * x**2 - 0.5 * x + 1
    x_with_gap = np.append(x, np.nan)
    y_with_gap = np.append(y, 5.0)

    fit = polynomial_fit(x_with_gap, y_with_gap, 2)

    assert fit["n"] == 50
    assert np.allclose(fit["coef"], [1, -0.5, 2])
    assert np.isclose(fit["r2"], 1.0)


def test_url_round_trip_keeps_emptied_controls() -> None:
    bathymetry = {"key": "bathymetry", "default": ["True"], "type": "list"}
    z_min = {"key": "zmin", "default": 0, "type": "float"}
    lat_min = {"key": "latmin", "default": None, "type": "float"}

    assert _url_param_text(bathymetry, ["True"]) is None
    assert _url_param_text(bathymetry, []) == ""
    assert _deserialize_url_value("", "list", ["True"]) == []
    assert _url_param_text(z_min, None) == ""
    assert _deserialize_url_value("", "float", 0) is None
    assert _url_param_text(lat_min, None) is None
    assert _deserialize_url_value(_url_param_text(z_min, 25.5), "float", 0) == 25.5


def test_apply_selection_highlights_ids_and_restores_source_box() -> None:
    fig = go.Figure([
        go.Scattergl(x=[1, 2, 3], y=[1, 2, 3], customdata=[[10, "a"], [11, "b"], [12, "c"]]),
        go.Scatter(x=[0, 1], y=[0, 1]),
    ])
    selection = {
        "selected_ids": [11, 12],
        "source": "main_plot",
        "axes": "latitude/depth",
        "selections": selection_shapes({"range": {"x": [1.5, 3.5], "y": [1.5, 3.5]}}),
    }

    apply_selection(fig, selection, "main_plot", "latitude/depth")

    assert list(fig.data[0].selectedpoints) == [1, 2]
    assert fig.data[1].selectedpoints is None
    assert fig.layout.selections[0].type == "rect"


def test_apply_selection_skips_box_when_axes_changed() -> None:
    fig = go.Figure([go.Scattergl(x=[1], y=[1], customdata=[10])])
    selection = {
        "selected_ids": [10],
        "source": "scatter_plot",
        "axes": "chlorophyll/backscattering",
        "selections": selection_shapes({"lassoPoints": {"x": [0, 1, 1], "y": [0, 0, 1]}}),
    }

    apply_selection(fig, selection, "scatter_plot", "salinity/temperature")

    assert list(fig.data[0].selectedpoints) == [0]
    assert not fig.layout.selections


def test_polynomial_fit_needs_enough_points() -> None:
    assert polynomial_fit([1, 2], [3, 4], 0) is None
    assert "error" in polynomial_fit([1, 2], [3, 4], 2)


def main() -> None:
    with TemporaryDirectory() as work_dir:
        app = create_app(work_dir)
        assert app.server is not None


if __name__ == "__main__":
    main()
