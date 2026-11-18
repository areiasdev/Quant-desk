#!/usr/bin/env bash
# Keeps the PAPER bot running: restarts it after a crash, 60 s later.
# Usage (macOS/Linux):  ./run_bot.sh        Stop: Ctrl+C (twice) or close the terminal.
# Env (optional): PYTHON=path/to/python  RESTART_DELAY=60  MAX_RESTARTS=0 (0 = forever)
cd "$(dirname "$0")" || exit 1
PY="${PYTHON:-.venv/bin/python}"
DELAY="${RESTART_DELAY:-60}"
MAX="${MAX_RESTARTS:-0}"
n=0
while true; do
  echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC  starting paper bot (PAPER ONLY)"
  "$PY" -m bot "$@"
  code=$?
  if [ "$code" -eq 2 ] || [ "$code" -eq 3 ]; then
    echo "bot stopped with code $code (config problem or live mode refused): not restarting"
    exit "$code"
  fi
  n=$((n + 1))
  if [ "$MAX" -gt 0 ] && [ "$n" -ge "$MAX" ]; then
    echo "reached MAX_RESTARTS=$MAX, exiting"
    exit "$code"
  fi
  echo "$(date -u '+%Y-%m-%d %H:%M:%S') UTC  bot exited with code $code; restarting in ${DELAY}s"
  sleep "$DELAY"
done
