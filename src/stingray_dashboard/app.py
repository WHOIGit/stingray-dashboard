from __future__ import annotations

import argparse
from pathlib import Path

import dash

from .callbacks import register_callbacks
from .data import init_data_dirs, load_auxiliary_data
from .layout import make_layout


# ============================================
# App factory, CLI, and WSGI entrypoints
# ============================================

def cli(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(description="Stingray Dashboard Server")
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--work-dir", type=str, default=None)

    return parser.parse_args(argv)


def resolve_assets_dir() -> Path:
    """
    Locate the packaged Dash assets directory.

    Install invariant:
      assets_dir = package_dir / "assets"

    This keeps the dashboard static files coupled to the Python package, so a
    Git or wheel install carries the code and visual assets together.  The
    current working directory is only checked as a development fallback.
    """
    here = Path(__file__).resolve()
    search_roots = [here.parent, Path.cwd()]

    for root in search_roots:
        candidate = root / "assets"
        if candidate.is_dir():
            return candidate

    return Path.cwd() / "assets"


def create_app(work_dir: str | Path | None = None) -> dash.Dash:
    """
    Build and configure the Dash application.
    """
    init_data_dirs(work_dir)
    load_auxiliary_data()

    dash_app = dash.Dash(
        __name__,
        assets_folder=str(resolve_assets_dir()),
        assets_url_path="/assets",
    )

    dash_app.layout = make_layout()
    register_callbacks(dash_app)

    return dash_app


def main(argv: list[str] | None = None) -> None:
    args = cli(argv)
    app = create_app(work_dir=args.work_dir)
    app.run(host=args.host, port=args.port, threaded=True, debug=False)


application = create_app().server


if __name__ == "__main__":
    main()
