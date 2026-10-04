#!/bin/zsh
# Double-click in Finder to start Taste Center and open it in the browser.
# Closing the Terminal window (or Ctrl+C) stops the server.
cd "${0:A:h}" || exit 1
PORT="${MTC_PORT:-8765}"
URL="http://127.0.0.1:$PORT"

pause() { echo; echo "Press any key to close."; read -k1 -s; }

if curl -sf -o /dev/null "$URL/api/overview"; then
  echo "Taste Center is already running at $URL"
  open "$URL"
  exit 0
fi

# First run, or a half-finished earlier setup: (re)create the Python environment.
if ! .venv/bin/python -c 'import fastapi, uvicorn' 2>/dev/null; then
  if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
    echo "Taste Center needs Python 3.13 or newer (this Mac has: $(python3 --version 2>&1))."
    echo "Install it from https://www.python.org/downloads/ and double-click this file again."
    pause
    exit 1
  fi
  echo "First run: setting up the Python environment (needs an internet connection)…"
  rm -rf .venv  # nothing in it works yet
  if ! { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }; then
    echo "Setup failed. Check your internet connection and double-click this file again."
    rm -rf .venv
    pause
    exit 1
  fi
fi

# Open the browser once the server answers.
( for _ in {1..75}; do
    curl -sf -o /dev/null "$URL/api/overview" && { open "$URL"; break; }
    sleep 0.2
  done ) &

echo "Taste Center running at $URL"
echo "Close this window or press Ctrl+C to stop it."
echo
.venv/bin/python app.py serve --port "$PORT" || { echo "Server stopped with an error."; pause; }
