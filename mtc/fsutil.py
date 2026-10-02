"""File reading that survives cloud placeholders (iCloud/OneDrive) and non-UTF-8 exports."""
import errno
import time
from pathlib import Path

_TRANSIENT = {errno.EDEADLK, errno.EAGAIN, errno.EIO, errno.ETIMEDOUT, errno.EINTR}
ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def read_bytes(path: str | Path, attempts: int = 6, delay: float = 0.5) -> bytes:
    """Read a file, retrying transient errors raised while a cloud file is being downloaded."""
    for attempt in range(attempts):
        try:
            return Path(path).read_bytes()
        except OSError as exc:
            if exc.errno not in _TRANSIENT or attempt == attempts - 1:
                raise
            time.sleep(delay * (2**attempt))
    raise AssertionError("unreachable")


def decode(data: bytes) -> tuple[str, str]:
    """Decode with utf-8-sig, then cp1252, then latin-1 (which never fails). Returns (text, encoding)."""
    for enc in ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise AssertionError("latin-1 decodes any byte string")


def read_text(path: str | Path) -> tuple[str, str]:
    return decode(read_bytes(path))
