from __future__ import annotations

import hashlib
import mimetypes
import re
from pathlib import Path
from typing import Any
from urllib.parse import urldefrag, urlparse

from .content_resolver_models import MediaCandidate, MediaStats, ResolvedContent


class ContentResolverIOMixin:
    def _success(
        self,
        *,
        source_url: str,
        content_type: str,
        final_url: str,
        local_path: str,
        extraction_method: str,
        goal: str,
        summary_hint: str,
        tried_urls: list[str],
        text_length: int = 0,
        substantial_block_count: int = 0,
        total_text_length: int = 0,
        has_explanatory_motion_media: bool = False,
        has_complete_content: bool = False,
        has_static_visual_media: bool = False,
        has_direct_media_links: bool = False,
        presentation_fitness_score: int = 0,
        document_path: str = "",
        media_candidates: list[MediaCandidate] | None = None,
        media_stats: MediaStats | None = None,
        external_signals: dict[str, Any] | None = None,
    ) -> ResolvedContent:
        return ResolvedContent(
            source_url=source_url,
            success=True,
            content_type=content_type,
            final_url=final_url,
            local_path=local_path,
            document_path=document_path or local_path,
            text_length=text_length,
            substantial_block_count=substantial_block_count,
            extraction_method=extraction_method,
            goal=goal,
            summary_hint=summary_hint,
            total_text_length=total_text_length,
            has_explanatory_motion_media=has_explanatory_motion_media,
            has_complete_content=has_complete_content,
            has_static_visual_media=has_static_visual_media,
            has_direct_media_links=has_direct_media_links,
            presentation_fitness_score=presentation_fitness_score,
            tried_urls=list(tried_urls),
            media_candidates=list(media_candidates or []),
            media_stats=media_stats or MediaStats(),
            external_signals=dict(external_signals or {}),
        )

    def _failure(
        self,
        *,
        source_url: str,
        error: str,
        tried_urls: list[str],
        content_type: str = "none",
        final_url: str = "",
        extraction_method: str = "none",
        goal: str = "",
        summary_hint: str = "",
        text_length: int = 0,
        substantial_block_count: int = 0,
        total_text_length: int = 0,
        has_explanatory_motion_media: bool = False,
        has_complete_content: bool = False,
        has_static_visual_media: bool = False,
        has_direct_media_links: bool = False,
        presentation_fitness_score: int = 0,
        document_path: str = "",
        media_candidates: list[MediaCandidate] | None = None,
        media_stats: MediaStats | None = None,
        external_signals: dict[str, Any] | None = None,
    ) -> ResolvedContent:
        return ResolvedContent(
            source_url=source_url,
            success=False,
            content_type=content_type,
            final_url=final_url,
            document_path=document_path,
            text_length=text_length,
            substantial_block_count=substantial_block_count,
            extraction_method=extraction_method,
            goal=goal,
            summary_hint=summary_hint,
            error=error,
            total_text_length=total_text_length,
            has_explanatory_motion_media=has_explanatory_motion_media,
            has_complete_content=has_complete_content,
            has_static_visual_media=has_static_visual_media,
            has_direct_media_links=has_direct_media_links,
            presentation_fitness_score=presentation_fitness_score,
            tried_urls=list(tried_urls),
            media_candidates=list(media_candidates or []),
            media_stats=media_stats or MediaStats(),
            external_signals=dict(external_signals or {}),
        )

    def _save_binary(
        self,
        content: bytes,
        final_url: str,
        output_root: Path,
        fallback_extension: str,
    ) -> Path:
        name = Path(urlparse(final_url).path).name or f"document{fallback_extension}"
        if "." not in name:
            name = f"{name}{fallback_extension}"
        prefix = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:8]
        destination = output_root / self._sanitize_filename(f"{prefix}_{name}")
        destination.write_bytes(content)
        return destination

    def _save_text(
        self,
        *,
        title: str,
        text: str,
        final_url: str,
        output_root: Path,
    ) -> Path:
        prefix = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:8]
        destination = output_root / f"{prefix}_{self._sanitize_filename(title or 'webpage')}.md"
        destination.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
        return destination

    def _save_source_markdown(
        self,
        *,
        title: str,
        text: str,
        output_root: Path,
    ) -> Path:
        destination = output_root / "source.md"
        destination.write_text(f"# {title}\n\n{text}\n", encoding="utf-8")
        return destination

    def _save_html(
        self,
        *,
        html_text: str,
        title: str,
        final_url: str,
        output_root: Path,
    ) -> Path:
        prefix = hashlib.md5(final_url.encode("utf-8")).hexdigest()[:8]
        destination = output_root / f"{prefix}_{self._sanitize_filename(title or 'webpage')}.html"
        destination.write_text(html_text, encoding="utf-8")
        return destination

    def _sanitize_filename(self, value: str) -> str:
        return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:180].strip("._") or "content"

    def _normalize_url(self, url: str) -> str:
        return urldefrag(url).url

    def _download_media_candidates(
        self,
        *,
        media_candidates: list[MediaCandidate],
        output_root: Path,
    ) -> list[MediaCandidate]:
        if not media_candidates:
            return []
        assets_root = output_root / "assets"
        assets_root.mkdir(parents=True, exist_ok=True)
        downloaded: dict[str, str] = {}
        localized_candidates: list[MediaCandidate] = []
        for candidate in media_candidates:
            local_path = downloaded.get(candidate.url, "")
            if not local_path:
                local_path = self._download_media_asset(candidate.url, assets_root)
                if local_path:
                    downloaded[candidate.url] = local_path
            localized_candidates.append(
                MediaCandidate(
                    url=candidate.url,
                    media_type=candidate.media_type,
                    local_path=local_path,
                    tag=candidate.tag,
                    alt_text=candidate.alt_text,
                    title_text=candidate.title_text,
                    figure_caption=candidate.figure_caption,
                )
            )
        return localized_candidates

    def _download_media_asset(self, url: str, assets_root: Path) -> str:
        try:
            response = self.session.get(
                url,
                timeout=self.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception:
            return ""

        content = response.content or b""
        if not content:
            return ""

        extension = self._guess_asset_extension(url, getattr(response, "headers", {}))
        basename = Path(urlparse(url).path).name or f"asset{extension}"
        if "." not in basename:
            basename = f"{basename}{extension}"
        destination = assets_root / self._sanitize_filename(
            f"{hashlib.md5(url.encode('utf-8')).hexdigest()[:8]}_{basename}"
        )
        try:
            destination.write_bytes(content)
        except OSError:
            return ""
        return str(destination)

    def _guess_asset_extension(self, url: str, headers: dict[str, str]) -> str:
        guessed = Path(urlparse(url).path).suffix
        if guessed:
            return guessed
        content_type = (headers or {}).get("Content-Type", "").split(";", 1)[0].strip().lower()
        return mimetypes.guess_extension(content_type) or ".bin"
