from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener


try:
    import requests as _requests
except Exception:
    _requests = None


if _requests is not None:
    Session = _requests.Session
    Response = _requests.Response

    def create_session() -> Any:
        return _requests.Session()

else:

    @dataclass
    class Response:
        url: str
        content: bytes
        status_code: int
        headers: dict[str, str]

        @property
        def text(self) -> str:
            return self.content.decode("utf-8", errors="ignore")

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError(f"http {self.status_code}")

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self._opener = build_opener()

        def get(
            self,
            url: str,
            *,
            timeout: int | float | None = None,
            allow_redirects: bool = True,
        ) -> Response:
            request = Request(url, headers=dict(self.headers), method="GET")
            try:
                with self._opener.open(request, timeout=timeout) as resp:
                    final_url = resp.geturl() if allow_redirects else url
                    content = resp.read()
                    headers = dict(resp.info().items())
                    status_code = getattr(resp, "status", None) or getattr(resp, "code", 200) or 200
                    return Response(
                        url=final_url,
                        content=content,
                        status_code=int(status_code),
                        headers=headers,
                    )
            except HTTPError as exc:
                content = exc.read() if hasattr(exc, "read") else b""
                headers = dict(exc.headers.items()) if exc.headers else {}
                return Response(
                    url=exc.geturl() or url,
                    content=content,
                    status_code=int(exc.code),
                    headers=headers,
                )
            except URLError as exc:
                raise RuntimeError(f"network error: {exc}") from exc

    def create_session() -> Session:
        return Session()

