# stingray-dashboard

Installable Dash application for interactive exploration of NES-LTER Stingray /
ISIIS dashboard data.

## Run the released image with Compose

This is the recommended deployment path. Docker pulls the released dashboard
image from GitHub Container Registry, so neither a source checkout nor a local
image build is required. The container reads dashboard-ready files from the
host workspace and never modifies them.

Unix shell:

```bash
# Download the Compose definition that runs the published GHCR image.
curl -O https://raw.githubusercontent.com/anhph95/stingraytools/main/compose.ghcr.yml

# Pull the selected release before starting the service.
DASH_DATA_DIR=/absolute/path/to/dash_data \
  docker compose -f compose.ghcr.yml pull

# Start the released image with dashboard data mounted read-only.
DASH_DATA_DIR=/absolute/path/to/dash_data \
STINGRAY_DEFAULT_DATASET=stingray \
  docker compose -f compose.ghcr.yml up -d --pull always
```

Windows PowerShell:

```powershell
# Download the Compose definition that runs the published GHCR image.
Invoke-WebRequest `
  -Uri "https://raw.githubusercontent.com/anhph95/stingraytools/main/compose.ghcr.yml" `
  -OutFile "compose.ghcr.yml"

# Select the host workspace and initial dataset.
$env:DASH_DATA_DIR = "C:\path\to\dash_data"
$env:STINGRAY_DEFAULT_DATASET = "stingray"

# Pull and start the released image.
docker compose -f compose.ghcr.yml pull
docker compose -f compose.ghcr.yml up -d --pull always
```

Open `http://127.0.0.1:8050`. Leave `STINGRAY_DEFAULT_DATASET` unset to open
the first dataset folder under `dash_data/data/`. Set
`STINGRAY_DASHBOARD_PORT=8051` when host port `8050` is unavailable.

The default image is `ghcr.io/anhph95/stingray-dashboard:latest`. For a
reproducible server deployment, set `STINGRAY_DASHBOARD_IMAGE` to a released
version tag such as `ghcr.io/anhph95/stingray-dashboard:2.1.0`.

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

By default, the dashboard opens the first dataset folder found under `data/`.
Set `--default-dataset` for the Python command or `STINGRAY_DEFAULT_DATASET` for
Docker and Compose deployments when a server should open a specific dataset.
If the requested dataset is absent, the dashboard falls back to the first
available folder so startup still succeeds.

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

## Run the released image directly

GitHub Actions builds `ghcr.io/anhph95/stingray-dashboard` from repository
source after every push to `main`. A Git tag such as `v2.1.0` also publishes
versioned `2.1.0` and `2.1` image tags.

Use this direct `docker run` workflow when Compose is not available:

```bash
# Download the newest image published from the main branch.
docker pull ghcr.io/anhph95/stingray-dashboard:latest

# Start the application with the current workspace data mounted read-only.
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8050:8050 \
  -e STINGRAY_DASHBOARD_PORT=8050 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  ghcr.io/anhph95/stingray-dashboard:latest
```

If host port `8050` is already in use, choose another host port without
rebuilding the image:

```bash
docker run -d \
  --name stingray-dashboard \
  --restart unless-stopped \
  -p 8051:8050 \
  -e STINGRAY_DASHBOARD_PORT=8051 \
  -v "$(pwd)/dash_data:/dash_data:ro" \
  ghcr.io/anhph95/stingray-dashboard:latest
```

For reproducible production deployment, replace `latest` with a version such as
`2.1.0`. Stop and remove the container with:

```bash
# Remove the application container without touching the bind-mounted host data.
docker rm -f stingray-dashboard
```

## Compose operations

The first section contains the complete recommended Compose installation.
These commands cover routine release updates and deployment customization.

Update the application while preserving the mounted datasets:

```bash
# Pull the image selected by the Compose environment variables.
DASH_DATA_DIR=/absolute/path/to/dash_data \
  docker compose -f compose.ghcr.yml pull

# Recreate the service only when the downloaded image has changed.
DASH_DATA_DIR=/absolute/path/to/dash_data \
  docker compose -f compose.ghcr.yml up -d --pull always
```

Pin an application release and optionally change the host port:

```bash
# Select an immutable application version and expose it on host port 8051.
STINGRAY_DASHBOARD_IMAGE=ghcr.io/anhph95/stingray-dashboard:2.1.0 \
STINGRAY_DASHBOARD_PORT=8051 \
DASH_DATA_DIR=/absolute/path/to/dash_data \
  docker compose -f compose.ghcr.yml up -d --pull always
```

Pin the initial dataset on a remote server when the mounted workspace contains
multiple dataset folders:

```bash
# Open dash_data/data/stingray by default while preserving the same read-only mount.
DASH_DATA_DIR=/mnt/stingray_share/dash_data \
STINGRAY_DEFAULT_DATASET=stingray \
  docker compose -f compose.ghcr.yml up -d --pull always
```

Leave `STINGRAY_DEFAULT_DATASET` unset to open the first available dataset
folder under `/dash_data/data`.

Stop the Compose deployment:

```bash
# Remove the Compose service and network while preserving host datasets.
DASH_DATA_DIR=/absolute/path/to/dash_data \
  docker compose -f compose.ghcr.yml down
```

## Build from a local checkout

This section is for dashboard development only. Normal users and deployments
should run a released GHCR image as described above. Developers can build the
exact checked-out source, including uncommitted local changes, from the
repository root:

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

## Python installation

Use the Python package workflow only when running without Docker.

Install the dashboard directly from Git:

```bash
# Install the dashboard package from the Git repository.
pip install "stingray-dashboard @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

For a Linux server deployment with Gunicorn:

```bash
# Install the dashboard package with server runtime dependencies.
pip install "stingray-dashboard[server] @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

Start the installed application with an explicit work directory:

```bash
# Run the installed command against the local dashboard workspace.
stingray-dashboard --work-dir dash_data --host 0.0.0.0 --port 8050
```

Select the initial dataset explicitly when checking processed Stingray output:

```bash
# Open dash_data/data/stingray first, even if other dataset folders exist.
stingray-dashboard \
  --work-dir dash_data \
  --default-dataset stingray \
  --host 127.0.0.1 \
  --port 8050
```

Display all command-line options:

```bash
# Show available command-line flags and defaults.
stingray-dashboard --help
```

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

## Container release process

Maintainers do not build or upload release images manually:

1. Push to `main` to publish `latest` and a commit-specific `sha-*` tag.
2. Create and push a version tag such as `v2.1.0` to publish `2.1.0` and `2.1`.
3. Make the container package public in the repository Packages settings so users can pull it without registry authentication.

The commit-specific tag provides an immutable deployment identity, a semantic
version identifies a supported release, and `latest` tracks the current
production branch.
