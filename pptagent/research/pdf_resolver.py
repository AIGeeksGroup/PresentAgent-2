from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests

PDF_SIGNATURE = b"%PDF-"
PDF_MIME_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "binary/pdf",
}
PDF_LINK_HINTS = (
    "pdf",
    "download",
    "paper",
    "report",
    "preprint",
    "full text",
    "view",
)


@dataclass
class PdfCandidate:
    url: str
    anchor_text: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PdfResolutionResult:
    source_url: str
    success: bool
    topic: str = ""
    final_url: str = ""
    local_path: str = ""
    discovery_method: str = "none"
    content_type: str = ""
    error: str = ""
    tried_urls: list[str] = field(default_factory=list)
    candidates: list[PdfCandidate] = field(default_factory=list)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        return payload


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._current_href: str | None = None
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._current_href = href
            self._text_chunks = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        text = " ".join(chunk.strip() for chunk in self._text_chunks if chunk.strip())
        self.links.append((self._current_href, text))
        self._current_href = None
        self._text_chunks = []


class PdfResolver:
    def __init__(
        self,
        timeout: int = 20,
        user_agent: str = "PresentAgent/1.0",
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)

    def resolve_to_pdf(
        self,
        url: str,
        download_dir: str,
        topic: str = "",
    ) -> PdfResolutionResult:
        download_root = Path(download_dir)
        download_root.mkdir(parents=True, exist_ok=True)

        tried_urls: list[str] = []
        candidates: list[PdfCandidate] = []

        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:
            return PdfResolutionResult(
                source_url=url,
                success=False,
                topic=topic,
                error=f"failed to fetch source url: {exc}",
            )

        tried_urls.append(response.url)
        if self._looks_like_pdf(response, response.url):
            local_path = self._save_pdf_response(response, download_root)
            return PdfResolutionResult(
                source_url=url,
                success=True,
                topic=topic,
                final_url=response.url,
                local_path=str(local_path),
                discovery_method="direct",
                content_type=response.headers.get("Content-Type", ""),
                tried_urls=tried_urls,
            )

        html = response.text or ""
        for candidate in self._discover_pdf_candidates(
            page_url=response.url,
            html=html,
            topic=topic,
        ):
            candidates.append(candidate)
            tried_urls.append(candidate.url)
            try:
                candidate_response = self.session.get(
                    candidate.url,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                candidate_response.raise_for_status()
            except Exception:
                continue
            if not self._looks_like_pdf(candidate_response, candidate_response.url):
                continue
            local_path = self._save_pdf_response(candidate_response, download_root)
            return PdfResolutionResult(
                source_url=url,
                success=True,
                topic=topic,
                final_url=candidate_response.url,
                local_path=str(local_path),
                discovery_method="html_link",
                content_type=candidate_response.headers.get("Content-Type", ""),
                tried_urls=tried_urls,
                candidates=candidates,
            )

        return PdfResolutionResult(
            source_url=url,
            success=False,
            topic=topic,
            error="no downloadable pdf discovered from url",
            tried_urls=tried_urls,
            candidates=candidates,
        )

    def _discover_pdf_candidates(
        self,
        page_url: str,
        html: str,
        topic: str,
    ) -> list[PdfCandidate]:
        parser = _AnchorParser()
        parser.feed(html)

        candidates: list[PdfCandidate] = []
        for href, anchor_text in parser.links:
            absolute_url = urljoin(page_url, href)
            if not self._looks_like_pdf_link(absolute_url, anchor_text):
                continue
            candidates.append(
                PdfCandidate(
                    url=absolute_url,
                    anchor_text=anchor_text,
                    score=self._score_candidate(
                        source_page_url=page_url,
                        candidate_url=absolute_url,
                        anchor_text=anchor_text,
                        topic=topic,
                    ),
                )
            )

        candidates.sort(key=lambda candidate: candidate.score, reverse=True)
        return self._dedupe_candidates(candidates)

    def _score_candidate(
        self,
        source_page_url: str,
        candidate_url: str,
        anchor_text: str,
        topic: str,
    ) -> float:
        score = 0.0
        candidate_lower = candidate_url.lower()
        anchor_lower = anchor_text.lower()

        if ".pdf" in candidate_lower:
            score += 5.0
        if "pdf" in anchor_lower:
            score += 3.0
        if any(hint in anchor_lower for hint in PDF_LINK_HINTS):
            score += 2.0
        if urlparse(candidate_url).netloc == urlparse(source_page_url).netloc:
            score += 1.0

        topic_tokens = self._normalize_tokens(topic)
        if topic_tokens:
            candidate_tokens = self._normalize_tokens(f"{candidate_url} {anchor_text}")
            overlap = len(topic_tokens & candidate_tokens)
            score += min(4.0, overlap * 1.5)

        return score

    def _looks_like_pdf_link(self, url: str, anchor_text: str) -> bool:
        lower_url = url.lower()
        lower_text = anchor_text.lower()
        return (
            ".pdf" in lower_url
            or "pdf" in lower_text
            or "download" in lower_text
            or "full text" in lower_text
            or "technical report" in lower_text
        )

    def _looks_like_pdf(self, response: requests.Response, final_url: str) -> bool:
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type in PDF_MIME_TYPES:
            return True
        if final_url.lower().endswith(".pdf"):
            return True
        return response.content.startswith(PDF_SIGNATURE)

    def _save_pdf_response(self, response: requests.Response, download_root: Path) -> Path:
        final_url = response.url
        parsed = urlparse(final_url)
        filename = Path(parsed.path).name or "document.pdf"
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename or 'document'}.pdf"

        # Prefix the filename with a short hash to avoid collisions across sources.
        name_hash = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:8]
        safe_filename = self._sanitize_filename(f"{name_hash}_{filename}")
        destination = download_root / safe_filename
        destination.write_bytes(response.content)
        return destination

    def _dedupe_candidates(
        self,
        candidates: Iterable[PdfCandidate],
    ) -> list[PdfCandidate]:
        seen: set[str] = set()
        deduped: list[PdfCandidate] = []
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            deduped.append(candidate)
        return deduped

    def _normalize_tokens(self, text: str) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+", text.lower())
            if len(token) >= 3
        }

    def _sanitize_filename(self, value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
        return sanitized[:180]
