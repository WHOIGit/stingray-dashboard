from tempfile import TemporaryDirectory

import pandas as pd

from stingray_dashboard.app import create_app
from stingray_dashboard.data import canonicalize_columns
from stingray_dashboard.plot_utils import get_point_id_from_event_point


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


def main() -> None:
    with TemporaryDirectory() as work_dir:
        app = create_app(work_dir)
        assert app.server is not None


if __name__ == "__main__":
    main()
