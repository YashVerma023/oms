#!/bin/sh
# Wait for MySQL, provision the schema once, then serve.
#
# Provisioning is done here rather than by each gunicorn worker: `create_app`
# runs it on import, so four workers would race to create the same tables on
# every restart. It is idempotent either way, but once is clearer in the log.
set -eu

: "${DB_HOST:=db}"
: "${DB_PORT:=3306}"
: "${WEB_CONCURRENCY:=4}"
: "${GUNICORN_TIMEOUT:=120}"
: "${DB_WAIT_SECONDS:=60}"

echo "OMP: waiting for MySQL at ${DB_HOST}:${DB_PORT}"
waited=0
until python -c "
import socket, sys
s = socket.socket()
s.settimeout(2)
try:
    s.connect(('${DB_HOST}', ${DB_PORT}))
except OSError:
    sys.exit(1)
" 2>/dev/null; do
    waited=$((waited + 2))
    if [ "$waited" -ge "$DB_WAIT_SECONDS" ]; then
        echo "OMP: MySQL did not answer within ${DB_WAIT_SECONDS}s - giving up." >&2
        exit 1
    fi
    sleep 2
done
echo "OMP: MySQL is up after ${waited}s"

# Creates the database, tables, added columns, widened keys and the default
# logins. Safe to repeat.
echo "OMP: provisioning the schema"
python -c "import app" >/dev/null

# The allocation check is CPU-bound on a few thousand rows, so a request can
# take seconds; the timeout is generous on purpose.
echo "OMP: starting gunicorn with ${WEB_CONCURRENCY} worker(s)"
exec gunicorn \
    --bind 0.0.0.0:8000 \
    --workers "${WEB_CONCURRENCY}" \
    --timeout "${GUNICORN_TIMEOUT}" \
    --graceful-timeout 30 \
    --access-logfile - \
    --error-logfile - \
    --forwarded-allow-ips '*' \
    app:app
