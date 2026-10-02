"""Entry point: `.venv/bin/python app.py` serves the UI at http://127.0.0.1:8765."""
import sys

from mtc.__main__ import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["serve"]))
