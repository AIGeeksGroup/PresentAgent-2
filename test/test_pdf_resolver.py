from __future__ import annotations

from pathlib import Path

from pptagent.research.pdf_resolver import PdfResolver


class FakeResponse:
    def __init__(
        self,
        url: str,
        content: bytes,
        *,
        text: str = "",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.content = content
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}

    def get(self, url: str, **_: object) -> FakeResponse:
        if url not in self.responses:
            raise RuntimeError(f"unexpected url: {url}")
        return self.responses[url]


def test_resolve_direct_pdf(tmp_path: Path):
    session = FakeSession(
        {
            "https://example.com/paper.pdf": FakeResponse(
                "https://example.com/paper.pdf",
                b"%PDF-1.7 direct pdf bytes",
                headers={"Content-Type": "application/pdf"},
            )
        }
    )
    resolver = PdfResolver(session=session)

    result = resolver.resolve_to_pdf(
        "https://example.com/paper.pdf",
        str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.discovery_method == "direct"
    assert Path(result.local_path).exists()
    assert Path(result.local_path).read_bytes().startswith(b"%PDF-")


def test_resolve_pdf_from_html_candidates_with_topic_match(tmp_path: Path):
    html = """
    <html>
      <body>
        <a href="/downloads/related-publication.pdf">Related publication PDF</a>
        <a href="/downloads/flow-matching-guide.pdf">Download Flow Matching Guide PDF</a>
      </body>
    </html>
    """
    session = FakeSession(
        {
            "https://ai.meta.com/research/publications/flow-matching-guide-and-code/": FakeResponse(
                "https://ai.meta.com/research/publications/flow-matching-guide-and-code/",
                html.encode("utf-8"),
                text=html,
                headers={"Content-Type": "text/html"},
            ),
            "https://ai.meta.com/downloads/flow-matching-guide.pdf": FakeResponse(
                "https://ai.meta.com/downloads/flow-matching-guide.pdf",
                b"%PDF-1.7 flow matching",
                headers={"Content-Type": "application/pdf"},
            ),
        }
    )
    resolver = PdfResolver(session=session)

    result = resolver.resolve_to_pdf(
        "https://ai.meta.com/research/publications/flow-matching-guide-and-code/",
        str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.discovery_method == "html_link"
    assert result.final_url == "https://ai.meta.com/downloads/flow-matching-guide.pdf"
    assert len(result.candidates) >= 1


def test_resolve_to_pdf_returns_failure_when_no_pdf_can_be_downloaded(tmp_path: Path):
    html = """
    <html>
      <body>
        <a href="/publications/other-paper">Related publication</a>
      </body>
    </html>
    """
    session = FakeSession(
        {
            "https://example.com/article": FakeResponse(
                "https://example.com/article",
                html.encode("utf-8"),
                text=html,
                headers={"Content-Type": "text/html"},
            )
        }
    )
    resolver = PdfResolver(session=session)

    result = resolver.resolve_to_pdf(
        "https://example.com/article",
        str(tmp_path),
        topic="flow matching",
    )

    assert result.success is False
    assert "no downloadable pdf" in result.error
