# stingray-dashboard

Installable Dash application for the NES-LTER Stingray dashboard.

```bash
pip install "stingray-dashboard[server] @ git+https://github.com/anhph95/StingrayTools.git#subdirectory=packages/stingray-dashboard"
```

Command-line entrypoint:

```bash
stingray-dashboard --help
```

Production WSGI target:

```text
stingray_dashboard.app:application
```
