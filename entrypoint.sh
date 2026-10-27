#!/usr/bin/env sh
set -e

case "${1:-api}" in
  api)
    alembic upgrade head
    exec uvicorn ptjobs.api:app --host 0.0.0.0 --port 8000 --no-server-header --proxy-headers --forwarded-allow-ips='*'
    ;;
  scrape|notify|run)
    exec python -m ptjobs "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
