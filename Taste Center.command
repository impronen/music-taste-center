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

if [[ ! -x .venv/bin/python ]]; then
  echo "First run: setting up the Python environment…"
  if ! { python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }; then
    echo "Setup failed."
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
