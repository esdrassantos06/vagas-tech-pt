#!/usr/bin/env sh
set -e

case "${1:-api}" in
  api)
    alembic upgrade head
    exec uvicorn ptjobs.api:app --host 0.0.0.0 --port 8000
    ;;
  scrape|notify|run)
    exec python -m ptjobs "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
