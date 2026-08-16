#!/bin/sh
set -e
# UVICORN_RELOAD=1 aktiviert Auto-Reload (Dev). Default aus – spart Disk-I/O auf Windows.
case "${UVICORN_RELOAD:-0}" in
  1|true|TRUE|yes|YES)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
      --reload --reload-dir /app/app --reload-dir /app/agents
    ;;
  *)
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
esac
