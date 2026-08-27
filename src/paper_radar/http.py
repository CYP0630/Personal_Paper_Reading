from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class FetchError(RuntimeError):
    pass


@dataclass(slots=True)
class HttpClient:
    cache_dir: Path
    timeout: float = 25.0
    offline: bool = False
    user_agent: str = "personal-paper-radar/0.1 (+https://github.com/)"

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(
        self,
        url: str,
        *,
        params: Mapping[str, str | int] | None = None,
        headers: Mapping[str, str] | None = None,
        ttl_seconds: int = 3600,
    ) -> bytes:
        final_url = self._url(url, params)
        cache_path = self._cache_path(final_url)
        cached = self._read_cache(cache_path, ttl_seconds)
        if cached is not None:
            return cached
        if self.offline:
            raise FetchError(f"Offline cache miss for {final_url}")

        request_headers = {"User-Agent": self.user_agent, "Accept": "*/*"}
        request_headers.update(headers or {})
        request = urllib.request.Request(final_url, headers=request_headers)

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                self._write_cache(cache_path, body)
                return body
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
                if isinstance(exc, urllib.error.HTTPError) and exc.code < 500 and exc.code != 429:
                    break
                if attempt < 2:
                    time.sleep(0.5 * (2**attempt))
        raise FetchError(f"Failed to fetch {final_url}: {last_error}") from last_error

    @staticmethod
    def json(body: bytes) -> object:
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _url(url: str, params: Mapping[str, str | int] | None) -> str:
        if not params:
            return url
        query = urllib.parse.urlencode(params)
        return f"{url}{'&' if '?' in url else '?'}{query}"

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.cache"

    @staticmethod
    def _read_cache(path: Path, ttl_seconds: int) -> bytes | None:
        if not path.exists():
            return None
        if ttl_seconds >= 0 and time.time() - path.stat().st_mtime > ttl_seconds:
            return None
        return path.read_bytes()

    @staticmethod
    def _write_cache(path: Path, body: bytes) -> None:
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(path)

