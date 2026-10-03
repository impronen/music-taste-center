"""Polite JSON-over-HTTP base for metadata APIs: one request at a time, a minimum interval
between requests, an identifying User-Agent and retries with backoff for transient failures.
The transport and clock are injectable so tests never touch the network."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from . import config

# (url, headers, timeout) -> (http status, body)
Transport = Callable[[str, dict[str, str], float], tuple[int, bytes]]


def urllib_transport(url: str, headers: dict[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


class ApiError(Exception):
    """Gave up after retries (network, 5xx, rate limiting)."""


class NotFound(ApiError):
    """The service doesn't know this artist/album."""


class Fatal(ApiError):
    """Retrying won't help (bad API key, suspended access): stop the whole run."""


class Transient(Exception):
    """Raised by `_interpret` to request a retry."""


class JsonApi:
    base_url = ""
    min_interval = 1.0
    retries = 4
    timeout = 20.0

    def __init__(
        self,
        transport: Transport = urllib_transport,
        *,
        min_interval: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.transport = transport
        self.sleep = sleep
        self.clock = clock
        if min_interval is not None:
            self.min_interval = min_interval
        self._last = -1e9
        self.requests = 0

    def _throttle(self) -> None:
        wait = self._last + self.min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last = self.clock()

    def get(self, path: str, params: dict[str, str]) -> dict:
        url = self.base_url + path + "?" + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        headers = {"User-Agent": config.USER_AGENT, "Accept": "application/json"}
        last_error = "unknown"
        for attempt in range(self.retries + 1):
            if attempt:
                self.sleep(min(60.0, 2.0 * 2 ** (attempt - 1)))
            self._throttle()
            self.requests += 1
            try:
                status, body = self.transport(url, headers, self.timeout)
            except (OSError, TimeoutError) as exc:  # URLError is an OSError
                last_error = f"network: {exc}"
                continue
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                data = None
            try:
                return self._interpret(status, data)
            except Transient as exc:
                last_error = str(exc)
        raise ApiError(f"gave up after {self.retries + 1} attempts: {last_error}")

    def _interpret(self, status: int, data: dict | None) -> dict:
        """Return the payload, or raise NotFound / Fatal / Transient."""
        raise NotImplementedError
