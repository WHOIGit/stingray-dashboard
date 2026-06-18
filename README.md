# stingray-dashboard

Installable Dash application for interactive exploration of NES-LTER Stingray /
ISIIS dashboard data.

## Installation

Install the dashboard directly from Git:

```bash
pip install "stingray-dashboard @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

For a Linux server deployment with Gunicorn:

```bash
pip install "stingray-dashboard[server] @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

## Data layout

The dashboard reads CSV data from a work directory. By default, it uses
`/dash_data` when that directory exists and otherwise uses `./dash_data`.

```text
dash_data/
  data/
    <dataset_name>/
      *.csv
  misc/
    NESLTER_station_list.csv
    NESLTER_transect_bathymetry.csv
```

Dataset folders below `data/` appear in the dataset selector. CSV files inside
the selected folder appear in the data-file selector. For example:

```text
dash_data/data/stingray/20230807_EN706.csv
```

The station and bathymetry tables are shipped with the package. An optional
`misc/` directory is needed only when a workspace provides replacement tables.

### CSV variables

The dashboard works best when each CSV contains:

- `times`, `latitude`, `longitude`, and `depth` for navigation and transect plots.
- `temperature` and `salinity` for the T-S diagram and density contours.
- `cast` for individual vertical profiles.
- Numeric sensor variables such as `chlorophyll`, `nitrate`, `par`, or `oxygen_concentration`.
- Optional `media` and `frame` columns for linked ISIIS imagery.

Common source names such as `lat`, `lon`, `t090`, `sal00`, and `pressure` are
normalized to canonical dashboard columns. Instrument-altitude sentinel values
equal to `9999.99` are treated as missing data so they do not distort plot
ranges or averages.

## Run locally

Start the installed application with an explicit work directory:

```bash
stingray-dashboard --work-dir dash_data --host 0.0.0.0 --port 8050
```

Open the dashboard at:

```text
http://localhost:8050
```

Display all command-line options:

```bash
stingray-dashboard --help
```

## Dashboard controls

- Use the top row to select dataset, CSV file, sampling mode, point size, opacity, font size, and refresh the file list.
- `Subsample` keeps every \(N\)-th observation and preserves the original point identifiers.
- `Average bins` computes means within cast- or deployment-aware groups of \(N\) observations.
- Short trailing average bins are discarded rather than combined across casts or time gaps.
- Each plot has its own option panel beside it.
- Plot dimensions are controlled by width and height inputs in each plot option panel.
- The URL query string stores the dashboard state for reproducible shared views.
- Cruise-track selections filter the main transect, T-S, and profile plots.
- Main plot selections synchronize with the T-S and profile plots.
- Cast coloring uses a continuous color scale to avoid creating one trace per cast.
- Multi-cast profile plots use WebGL traces for smoother rendering.

## Development and tests

Run these commands from the repository root so the editable install includes all
project packages and development dependencies:

```bash
# Create an isolated project-local Python environment.
python -m venv .venv

# Activate the environment on Linux or macOS.
source .venv/bin/activate

# Install the repository in editable mode with test dependencies.
pip install -e ".[dev]"

# Run the dashboard regression tests.
pytest packages/stingray-dashboard/tests
```

Runtime datasets below `dash_data/` are intentionally excluded from version
control. Keep large or institution-specific CSV files in the local workspace
and commit only source code, packaged reference tables, and tests.

## WHOI-hosted dashboard

Dashboard-ready files from `dash_data/data` can be copied to:

```text
\\vast.whoi.edu\proj\nes-lter\stingray_dashboard\dash_data\data
```

The hosted dashboard is available at:

```text
https://stingraydash.whoi.edu/
```

## Gunicorn deployment

The installable WSGI target is:

```text
stingray_dashboard.app:application
```

Run it with Gunicorn:

```bash
gunicorn --bind 0.0.0.0:8050 stingray_dashboard.app:application
```

For a container or server deployment, mount the dashboard workspace at
`/dash_data`:

```yaml
volumes:
  - /path/to/dashboard_data:/dash_data:ro
```

## Docker data requirements

The container treats `/dash_data` strictly as read-only input and never creates
files or directories there. A workspace normally contains only dataset files:

```text
dash_data/
  data/
    <dataset_name>/
      *.csv
```

The packaged station and bathymetry tables are used automatically. Add
`dash_data/misc/` only when supplying workspace-specific replacements. Changing
dataset files does not require rebuilding the image.

## Run the published container image

GitHub Actions builds `ghcr.io/anhph95/stingray-dashboard` from repository
source after every push to `main`. A Git tag such as `v2.1.0` also publishes
versioned `2.1.0` and `2.1` image tags.

Run the rolling image from the workspace that contains `dash_data/`:

```bash
# Download the newest image published from the main branch.
docker pull ghcr.io/anhph95/stingray-dashboard:latest

# Start the application with the current workspace data mounted read-only.
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  ghcr.io/anhph95/stingray-dashboard:latest
```

For reproducible production deployment, replace `latest` with a version such as
`2.1.0`. Stop and remove the container with:

```bash
# Remove the application container without touching the bind-mounted host data.
docker rm -f stingray-dashboard
```

## Run with Compose

Download the release Compose file from the repository:

```bash
# Fetch only the Compose definition; cloning the source repository is unnecessary.
curl -O https://raw.githubusercontent.com/anhph95/stingraytools/main/compose.ghcr.yml

# Start the published image with the selected dashboard workspace.
DASH_DATA_DIR=/absolute/path/to/dashboard_data \
  docker compose -f compose.ghcr.yml up -d
```

Update the application while preserving the mounted datasets:

```bash
# Pull the image selected by the Compose environment variables.
DASH_DATA_DIR=/absolute/path/to/dashboard_data \
  docker compose -f compose.ghcr.yml pull

# Recreate the service only when the downloaded image has changed.
DASH_DATA_DIR=/absolute/path/to/dashboard_data \
  docker compose -f compose.ghcr.yml up -d
```

Pin an application release and optionally change the host port:

```bash
# Select an immutable application version and expose it on host port 8051.
STINGRAY_DASHBOARD_IMAGE=ghcr.io/anhph95/stingray-dashboard:2.1.0 \
STINGRAY_DASHBOARD_PORT=8051 \
DASH_DATA_DIR=/absolute/path/to/dashboard_data \
  docker compose -f compose.ghcr.yml up -d
```

Stop the Compose deployment:

```bash
# Remove the Compose service and network while preserving host datasets.
DASH_DATA_DIR=/absolute/path/to/dashboard_data \
  docker compose -f compose.ghcr.yml down
```

## Build directly from GitHub

This workflow tests current Git source before a published image is available.
Docker downloads the repository and builds `Dockerfile.release` locally, so no
repository clone or Python installation is required:

```bash
# Build the release image from the main branch of the remote Git repository.
docker build \
  -f Dockerfile.release \
  -t stingray-dashboard:git \
  "https://github.com/anhph95/stingraytools.git#main"

# Run the locally built image from the workspace containing dash_data/.
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  stingray-dashboard:git
```

Replace `main` with a branch, tag, or full commit hash to select another source
revision.

## Build from a local checkout

Developers can build the exact checked-out source, including uncommitted local
changes. Run these commands from the repository root:

```bash
# Clone and enter the source repository.
git clone https://github.com/anhph95/stingraytools.git
cd stingraytools

# Build the dashboard package and assets from the current working tree.
docker build -f Dockerfile.release -t stingray-dashboard:local .

# Run the resulting image from the workspace containing dash_data/.
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  stingray-dashboard:local
```

Rebuild after changing application source, assets, dependencies, or Docker
configuration.

## Existing WHOI server deployment

The repository-root `Dockerfile` and `compose.yml` preserve the existing server
workflow. They install the dashboard package from the configured Git repository
and mount this server dataset:

```text
/srv/vast/nes-lter/Stingray/data/dashboard_data
```

The current server can continue using:

```bash
# Rebuild from the configured STINGRAYTOOLS_REF and restart the service.
docker compose up -d --build
```

The default `STINGRAYTOOLS_REF` is `main`. This workflow does not use the GHCR
image unless the server is deliberately migrated to `compose.ghcr.yml`.

## Container release process

Maintainers do not build or upload release images manually:

1. Push to `main` to publish `latest` and a commit-specific `sha-*` tag.
2. Create and push a version tag such as `v2.1.0` to publish `2.1.0` and `2.1`.
3. Make the container package public in the repository Packages settings so users can pull it without registry authentication.

The commit-specific tag provides an immutable deployment identity, a semantic
version identifies a supported release, and `latest` tracks the current
production branch.
