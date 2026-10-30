# Stingray Dashboard

Stingray Dashboard is a read-only Dash application for exploring dashboard-ready
Stingray CSV products.

## Data layout

The application reads `/dash_data` when that directory exists; otherwise it
reads `./dash_data`.

```text
dash_data/
  data/
    <platform_project>/
      <cruise>.csv
  misc/
    NESLTER_station_list.csv
    NESLTER_transect_bathymetry.csv
```

Dataset folders appear in the dataset selector. CSV files within the selected
folder appear in the file selector. The application never modifies the data
directory.

CSV files work best with `times`, `latitude`, `longitude`, and `depth`. Sensor
variables, `cast`, and optional `media` and `frame` columns are supported.
Position and depth names such as `lat`, `lon`, and `depsm` are normalized
automatically. Instrument columns keep their own names; temperature, salinity,
chlorophyll, pressure, and backscattering are recognized under common aliases
(for example SeaBird `t090c`, `sal00`, `prdm`, `fleco_afl`) for the T-S plot,
default variables, and units. Text columns that are mostly numbers are converted
on load; plot variable lists show numeric columns only. Altitude values equal to
`9999.99` are treated as missing.

Station and bathymetry tables are bundled with the package. Files in
`dash_data/misc/` override the bundled tables when present.

## Run with Compose

From the directory containing `dash_data/`:

```bash
curl -O https://raw.githubusercontent.com/WHOIGit/stingray-dashboard/main/compose.ghcr.yml
docker compose -f compose.ghcr.yml pull
docker compose -f compose.ghcr.yml up -d --pull always
```

Open [http://127.0.0.1:8050](http://127.0.0.1:8050). Set
`STINGRAY_DASHBOARD_PORT` when the host port is unavailable. Set
`STINGRAY_DEFAULT_DATASET` to choose the initial dataset folder.

The compose file runs Gunicorn with 2 worker processes of 4 threads each. Set
`GUNICORN_CMD_ARGS` to change this, for example on a small server:

```bash
GUNICORN_CMD_ARGS="--worker-class gthread --workers 1 --threads 8 --timeout 120" docker compose -f compose.ghcr.yml up -d
```

Each worker process keeps its own in-memory data cache, so memory use grows with
the number of workers. After an update, reload open browser tabs.

## Run the released image

```bash
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  -e GUNICORN_CMD_ARGS="--worker-class gthread --workers 2 --threads 4 --timeout 120" \
  ghcr.io/whoigit/stingray-dashboard:3.4.0
```

## Run from Python

Python 3.11 or newer is required.

```bash
pip install "stingray-dashboard @ git+https://github.com/WHOIGit/stingray-dashboard.git"
stingray-dashboard --work-dir dash_data --host 0.0.0.0 --port 8050
```

For Gunicorn deployments:

```bash
pip install "stingray-dashboard[server] @ git+https://github.com/WHOIGit/stingray-dashboard.git"
gunicorn --bind 0.0.0.0:8050 --worker-class gthread --workers 2 --threads 4 \
  --timeout 120 stingray_dashboard.app:application
```

## Dashboard controls

The dashboard has a cruise track, transect, T-S, vertical profile, and scatter
plot, with linked selection, a Details panel with camera frames, shareable URLs,
and figure export (PNG, SVG, TIFF) with multi-panel figures that can be fitted
to a page. See the [usage guide](docs/usage.md).

When `media` and `frame` columns are present, selected observations can link to
a compatible frame service such as
[stingray-frame-viewer](https://github.com/WHOIGit/stingray-frame-viewer).

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "."
pip install pytest
python -m pytest tests
```

Runtime data under `dash_data/` is excluded from version control. Keep source
code, tests, and packaged reference tables in the repository.

## License and citation

Stingray Dashboard is distributed under the MIT License. See [LICENSE](LICENSE).

The MIT License covers the source code only. The logos in
`src/stingray_dashboard/assets/` belong to WHOI, NES-LTER and the LTER Network
and are not licensed for reuse. The bundled reference tables in
`src/stingray_dashboard/data_reference/` keep the terms of their original
sources; cite those sources when reusing them.

Please cite [CITATION.cff](CITATION.cff).
