from tempfile import TemporaryDirectory

from stingray_dashboard.app import create_app


def main() -> None:
    with TemporaryDirectory() as work_dir:
        app = create_app(work_dir)
        assert app.server is not None


if __name__ == "__main__":
    main()
