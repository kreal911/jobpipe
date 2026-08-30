"""Tiny, polite HTTP layer. Stdlib only."""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "jobpipe/1.0 (personal job search; contact kirill911men@gmail.com)"
_LAST_CALL: dict[str, float] = {}
MIN_GAP = 1.0  # seconds between calls to the same host


class FetchError(RuntimeError):
    pass


def _throttle(url: str) -> None:
    host = urllib.parse.urlsplit(url).netloc
    last = _LAST_CALL.get(host, 0.0)
    gap = time.time() - last
    if gap < MIN_GAP:
        time.sleep(MIN_GAP - gap)
    _LAST_CALL[host] = time.time()


def get_json(url: str, timeout: int = 30, retries: int = 3) -> dict | list:
    return _request(url, None, timeout, retries)


def post_json(url: str, body: dict, timeout: int = 30, retries: int = 3) -> dict | list:
    return _request(url, json.dumps(body).encode(), timeout, retries)


def _request(url: str, data: bytes | None, timeout: int, retries: int):
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    last_err: Exception | None = None
    for attempt in range(retries):
        _throttle(url)
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                return json.loads(payload.decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (404, 401, 403):
                raise FetchError(f"{url} -> HTTP {e.code}") from e
            time.sleep(2 ** attempt)
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(2 ** attempt)
    raise FetchError(f"{url} failed after {retries} tries: {last_err}")
