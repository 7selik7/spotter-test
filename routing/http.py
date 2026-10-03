import threading

import httpx
from django.conf import settings

from .exceptions import UpstreamTimeout, UpstreamUnavailable

_client = None
_lock = threading.Lock()


def client() -> httpx.Client:
    """One keep-alive client per process: no TCP/TLS handshake on every request."""
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                _client = httpx.Client(
                    timeout=settings.HTTP_TIMEOUT_SECONDS,
                    headers={"User-Agent": settings.HTTP_USER_AGENT},
                )
    return _client


class CallCounter:
    """Counts external calls made while serving one request (reported in `meta`)."""

    def __init__(self):
        self.calls = []

    def add(self, name):
        self.calls.append(name)

    @property
    def total(self):
        return len(self.calls)


def request(method, url, *, counter: CallCounter, name: str, **kwargs) -> httpx.Response:
    counter.add(name)
    try:
        response = client().request(method, url, **kwargs)
    except httpx.TimeoutException as exc:
        raise UpstreamTimeout(f"{name}: timeout") from exc
    except httpx.HTTPError as exc:
        raise UpstreamUnavailable(f"{name}: {exc.__class__.__name__}") from exc
    if response.status_code >= 500:
        raise UpstreamUnavailable(f"{name}: HTTP {response.status_code}")
    return response
