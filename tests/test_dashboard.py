from tempfile import TemporaryDirectory

import pandas as pd

from stingray_dashboard.app import create_app
from stingray_dashboard.data import canonicalize_columns


def test_canonicalize_columns_normalizes_non_string_labels() -> None:
    df = pd.DataFrame([[1, 2]], columns=[b"media", pd.Timestamp("2026-01-01")])

    result = canonicalize_columns(df)

    assert list(result.columns[:2]) == ["media", "2026-01-01 00:00:00"]


def main() -> None:
    with TemporaryDirectory() as work_dir:
        app = create_app(work_dir)
        assert app.server is not None


if __name__ == "__main__":
    main()
