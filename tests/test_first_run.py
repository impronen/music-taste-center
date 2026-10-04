"""What a newcomer runs into first: a wrong time zone name, and a file the importer can't read."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from mtc.__main__ import main

ROOT = Path(__file__).resolve().parent.parent


def python(code: str, **env) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT,
                          env={**os.environ, **env})


class TimeZoneTests(unittest.TestCase):
    def test_a_wrong_time_zone_name_is_a_clear_one_line_error_not_a_traceback(self):
        for bad in ("Mars/Olympus", "Helsinki", "", "../etc/passwd"):
            r = python("import mtc.config", MTC_TZ=bad)
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("is not a known time zone name", r.stderr, bad)
            self.assertNotIn("Traceback", r.stderr, bad)
            self.assertEqual(len(r.stderr.strip().splitlines()), 1, bad)

    def test_a_valid_name_is_used(self):
        r = python("import mtc.config as c; print(c.TZ.key)", MTC_TZ="America/New_York")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "America/New_York"))


class UnreadableFileTests(unittest.TestCase):
    def run_import(self, text: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "in.csv"
            csv_path.write_text(text, encoding="utf-8")
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["--db", str(Path(tmp) / "t.db"), "import", str(csv_path)]), 0)
            return out.getvalue()

    def test_a_semicolon_file_says_what_is_missing(self):
        out = self.run_import("artist;album;track;date\nCher;Believe;Believe;04 Oct 2026, 16:42\n")
        self.assertIn("added 0", out)
        self.assertIn("Nothing could be read", out)

    def test_a_good_file_gets_no_hint(self):
        out = self.run_import("Cher,Believe,Believe,04 Oct 2026 16:42\n")
        self.assertIn("added 1", out)
        self.assertNotIn("Nothing could be read", out)


if __name__ == "__main__":
    unittest.main()
