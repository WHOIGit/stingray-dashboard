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
Common source names such as `lat`, `lon`, `t090`, `sal00`, and `pressure` are
normalized automatically. Altitude values equal to `9999.99` are treated as
missing.

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

## Run the released image

```bash
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  ghcr.io/WHOIGit/stingray-dashboard:3.2.0
```

## Run from Python

```bash
pip install "stingray-dashboard @ git+https://github.com/WHOIGit/stingray-dashboard.git"
stingray-dashboard --work-dir dash_data --host 0.0.0.0 --port 8050
```

For Gunicorn deployments:

```bash
pip install "stingray-dashboard[server] @ git+https://github.com/WHOIGit/stingray-dashboard.git"
gunicorn --bind 0.0.0.0:8050 stingray_dashboard.app:application
```

## Dashboard controls

The dashboard supports dataset and CSV selection, subsampling or averaging,
plot-variable selection, plot sizing, linked cruise-track filtering, and
download of the selected CSV. The URL stores dashboard state for reproducible
views.

When `media` and `frame` columns are present, selected observations can link to
a compatible frame service such as
[stingray-frame-viewer](https://github.com/WHOIGit/stingray-frame-viewer).

## Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e "."
python tests/test_dashboard.py
```

Runtime data under `dash_data/` is excluded from version control. Keep source
code, tests, and packaged reference tables in the repository.

## License and citation

Stingray Dashboard is distributed under the MIT License. See [LICENSE](LICENSE).

Please cite [CITATION.cff](CITATION.cff).
