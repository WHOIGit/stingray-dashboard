# stingray-dashboard

Installable Dash application for interactive exploration of NES-LTER Stingray / ISIIS dashboard data.

## Installation

Install the dashboard directly from Git:

```bash
pip install "stingray-dashboard @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

For a Linux server deployment with Gunicorn:

```bash
pip install "stingray-dashboard[server] @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

## Data Layout

The dashboard reads CSV data from a work directory. By default it uses `/dash_data` when that directory exists, otherwise `./dash_data`.

Expected structure:

```text
dash_data/
  data/
    <dataset_name>/
      *.csv
  misc/
    NESLTER_station_list.csv
    NESLTER_transect_bathymetry.csv
```

Dataset folders under `data/` appear in the dashboard dataset dropdown. CSV files inside the selected dataset folder appear in the data-file dropdown.

## Run Locally

```bash
stingray-dashboard --work-dir dash_data --host 0.0.0.0 --port 8050
```

Then open:

```text
http://localhost:8050
```

Command help:

```bash
stingray-dashboard --help
```

## Production

The WSGI target is:

```text
stingray_dashboard.app:application
```

Example Gunicorn command:

```bash
gunicorn --bind 0.0.0.0:8050 stingray_dashboard.app:application
```

For container or server deployments, mount the dashboard data directory at `/dash_data`:

```yaml
volumes:
  - /path/to/dashboard_data:/dash_data:ro
```

## Usage Notes

- Use the top row to select dataset, CSV file, sampling mode, point size, opacity, font size, and refresh the file list.
- Each plot has its own option panel beside it.
- Plot dimensions are controlled by the width and height inputs in each plot option panel.
- The URL query string stores the current dashboard state, so copied URLs can reproduce selected variables, plot dimensions, and display options.
- The cruise-track selection filters the main transect plot, T-S plot, and profile plot.
- Main plot selections synchronize with the T-S plot and profile plot.
