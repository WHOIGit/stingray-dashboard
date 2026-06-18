#!/bin/sh
set -eu

HOST_PORT="${STINGRAY_DASHBOARD_PORT:-8050}"
PUBLIC_URL="${STINGRAY_DASHBOARD_PUBLIC_URL:-http://127.0.0.1:${HOST_PORT}}"

cat <<EOF
Stingray Dashboard is starting.
Open: ${PUBLIC_URL}
Container port: 8050
Data mount: /dash_data
EOF

exec gunicorn --bind 0.0.0.0:8050 stingray_dashboard.app:application
