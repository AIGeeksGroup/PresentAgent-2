from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

import requests

from .content_resolver_constants import DOCX_MIME_TYPE, DOC_MIME_TYPES
from .content_resolver_html import ContentResolverHtmlMixin
from .content_resolver_io import ContentResolverIOMixin
from .content_resolver_markdown import ContentResolverMarkdownMixin
from .content_resolver_models import (
    ContentLinkCandidate,
    MediaCandidate,
    MediaStats,
    ResolvedContent,
)
from .content_resolver_navigation import ContentResolverNavigationMixin


class ContentResolver(
    ContentResolverHtmlMixin,
    ContentResolverNavigationMixin,
    ContentResolverMarkdownMixin,
    ContentResolverIOMixin,
):
    def __init__(
        self,
        timeout: int = 20,
        user_agent: str = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/135.0.0.0 Safari/537.36"
        ),
        min_text_chars: int = 4000,
        min_substantial_blocks: int = 5,
        min_block_chars: int = 150,
        max_block_link_ratio: float = 0.35,
        min_document_bytes: int = 50_000,
        max_depth: int = 2,
        max_children_per_node: int | None = None,
        session: requests.Session | None = None,
        child_link_selector=None,
        llm_enabled: bool | None = None,
        llm_model: str | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_max_candidates: int | None = None,
        llm_excerpt_chars: int | None = None,
    ) -> None:
        self.timeout = timeout
        self.min_text_chars = min_text_chars
        self.min_substantial_blocks = min_substantial_blocks
        self.min_block_chars = min_block_chars
        self.max_block_link_ratio = max_block_link_ratio
        self.min_document_bytes = min_document_bytes
        self.max_depth = max_depth
        self.max_children_per_node = max_children_per_node
        self.child_link_selector = child_link_selector
        self.llm_enabled = (
            llm_enabled
            if llm_enabled is not None
            else os.getenv("PRESENTAGENT_CONTENT_RESOLVER_USE_LLM", "1") != "0"
        )
        self.llm_model = llm_model or os.getenv("LANGUAGE_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1"
        self.llm_base_url = llm_base_url or os.getenv("API_BASE")
        self.llm_api_key = llm_api_key or os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY")
        self.llm_max_candidates = llm_max_candidates
        self.llm_excerpt_chars = llm_excerpt_chars
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault(
            "Accept",
            "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        )
        self.session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self.session.headers.setdefault("Cache-Control", "no-cache")
        self.session.headers.setdefault("Pragma", "no-cache")
        self.session.headers.setdefault("Upgrade-Insecure-Requests", "1")

    def resolve(
        self,
        url: str,
        output_dir: str,
        topic: str = "",
        goal: str = "",
        summary_hint: str = "",
    ) -> ResolvedContent:
        output_root = Path(output_dir)
        output_root.mkdir(parents=True, exist_ok=True)
        return self._visit(
            current_url=url,
            source_url=url,
            output_root=output_root,
            topic=topic,
            goal=goal,
            summary_hint=summary_hint,
            depth=0,
            visited=set(),
            tried_urls=[],
        )

    def resolve_best_media_source(
        self,
        urls: Iterable[str],
        output_dir: str,
        topic: str = "",
        goal: str = "",
        summary_hint: str = "",
        min_motion_media_count: int | None = None,
    ) -> ResolvedContent:
        first_success: ResolvedContent | None = None
        best_result: ResolvedContent | None = None

        for url in urls:
            result = self.resolve(
                url=url,
                output_dir=output_dir,
                topic=topic,
                goal=goal,
                summary_hint=summary_hint,
            )
            if result.success and first_success is None:
                first_success = result
            if not result.success or result.content_type != "html":
                continue
            if (
                min_motion_media_count is not None
                and self._motion_media_count(result) >= min_motion_media_count
            ):
                return result
            if best_result is None or self._media_rank_key(result) > self._media_rank_key(best_result):
                best_result = result

        if best_result is not None:
            return best_result
        if first_success is not None:
            return first_success
        return ResolvedContent(
            source_url="",
            success=False,
            error="no valid content found from input urls",
        )

    def _visit(
        self,
        *,
        current_url: str,
        source_url: str,
        output_root: Path,
        topic: str,
        goal: str,
        summary_hint: str,
        depth: int,
        visited: set[str],
        tried_urls: list[str],
    ) -> ResolvedContent:
        if depth > self.max_depth:
            return self._failure(
                source_url=source_url,
                goal=goal,
                summary_hint=summary_hint,
                error="max traversal depth reached before finding complete content",
                tried_urls=tried_urls,
            )

        normalized_url = self._normalize_url(current_url)
        if normalized_url in visited:
            return self._failure(
                source_url=source_url,
                goal=goal,
                summary_hint=summary_hint,
                error="cycle detected while traversing child links",
                tried_urls=tried_urls,
            )
        visited.add(normalized_url)

        try:
            response = self.session.get(
                current_url,
                timeout=self.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
        except Exception as exc:
            return self._failure(
                source_url=source_url,
                goal=goal,
                summary_hint=summary_hint,
                error=f"failed to fetch source url: {exc}",
                tried_urls=tried_urls,
            )

        final_url = self._normalize_url(response.url)
        visited.add(final_url)
        current_tried_urls = list(tried_urls)
        if final_url not in current_tried_urls:
            current_tried_urls.append(final_url)

        direct_result = self._resolve_direct_document(
            response=response,
            source_url=source_url,
            output_root=output_root,
            extraction_method="direct" if depth == 0 else "child_link",
            goal=goal,
            summary_hint=summary_hint,
            tried_urls=current_tried_urls,
        )
        if direct_result is not None:
            return direct_result

        if not self._is_html(response):
            return self._failure(
                source_url=source_url,
                content_type=self._content_type(response) or "binary",
                final_url=final_url,
                extraction_method="direct" if depth == 0 else "child_link",
                goal=goal,
                summary_hint=summary_hint,
                error="response is not a supported document or html content page",
                tried_urls=current_tried_urls,
            )

        assessment = self._assess_html(response.text or "")
        media_candidates, media_stats = self._extract_media_candidates(
            page_url=final_url,
            html_text=response.text or "",
        )
        media_candidates = self._download_media_candidates(
            media_candidates=media_candidates,
            output_root=output_root,
        )
        child_candidates = self._discover_child_candidates(final_url, response.text or "")
        if self.max_children_per_node is not None:
            child_candidates = child_candidates[: self.max_children_per_node]
        has_motion_media = self._has_explanatory_motion_media(
            html_text=response.text or "",
            assessment=assessment,
            topic=topic,
            goal=goal,
        )
        has_complete_content = self._has_enough_continuous_content(assessment)
        has_static_visual_media = self._has_static_visual_media(media_stats)
        has_direct_media_links = self._has_direct_media_links(media_stats)
        presentation_fitness_score = self._presentation_fitness_score(
            assessment=assessment,
            stats=media_stats,
            has_complete_content=has_complete_content,
            has_motion_media=has_motion_media,
        )

        llm_decision = self._decide_html_next_step(
            page_url=final_url,
            html_text=response.text or "",
            assessment=assessment,
            candidates=child_candidates,
            topic=topic,
            goal=goal,
            summary_hint=summary_hint,
        )
        if llm_decision.get("use_current_page"):
            text_to_save = self._build_presentation_ready_markdown(
                html_text=response.text or "",
                page_url=final_url,
                assessment=assessment,
                media_candidates=media_candidates,
                output_root=output_root,
            )
            document_path = self._save_source_markdown(
                title=assessment.title or final_url,
                text=text_to_save,
                output_root=output_root,
            )
            result = self._success(
                source_url=source_url,
                content_type="html",
                final_url=final_url,
                local_path=str(document_path),
                document_path=str(document_path),
                text_length=len(text_to_save),
                substantial_block_count=assessment.substantial_block_count,
                extraction_method="direct" if depth == 0 else "child_link",
                goal=goal,
                summary_hint=summary_hint,
                total_text_length=assessment.total_text_length,
                has_explanatory_motion_media=has_motion_media,
                has_complete_content=has_complete_content,
                has_static_visual_media=has_static_visual_media,
                has_direct_media_links=has_direct_media_links,
                presentation_fitness_score=presentation_fitness_score,
                media_candidates=media_candidates,
                media_stats=media_stats,
                tried_urls=current_tried_urls,
            )
            result.candidates = child_candidates
            return result

        next_child = self._select_child_candidate(
            page_url=final_url,
            html_text=response.text or "",
            llm_decision=llm_decision,
            candidates=child_candidates,
            assessment=assessment,
            media_stats=media_stats,
            topic=topic,
            goal=goal,
            summary_hint=summary_hint,
        )
        if next_child is not None:
            return self._visit(
                current_url=next_child.url,
                source_url=source_url,
                output_root=output_root,
                topic=topic,
                goal=goal,
                summary_hint=summary_hint,
                depth=depth + 1,
                visited=visited,
                tried_urls=current_tried_urls,
            )

        if has_complete_content and not self.llm_enabled:
            text_to_save = self._build_presentation_ready_markdown(
                html_text=response.text or "",
                page_url=final_url,
                assessment=assessment,
                media_candidates=media_candidates,
                output_root=output_root,
            )
            document_path = self._save_source_markdown(
                title=assessment.title or final_url,
                text=text_to_save,
                output_root=output_root,
            )
            result = self._success(
                source_url=source_url,
                content_type="html",
                final_url=final_url,
                local_path=str(document_path),
                document_path=str(document_path),
                text_length=len(text_to_save),
                substantial_block_count=assessment.best_run_block_count,
                extraction_method="direct" if depth == 0 else "child_link",
                goal=goal,
                summary_hint=summary_hint,
                total_text_length=assessment.total_text_length,
                has_explanatory_motion_media=has_motion_media,
                has_complete_content=has_complete_content,
                has_static_visual_media=has_static_visual_media,
                has_direct_media_links=has_direct_media_links,
                presentation_fitness_score=presentation_fitness_score,
                media_candidates=media_candidates,
                media_stats=media_stats,
                tried_urls=current_tried_urls,
            )
            result.candidates = child_candidates
            return result

        if has_motion_media or (has_complete_content and (has_static_visual_media or has_direct_media_links)):
            local_path = self._save_html(
                html_text=response.text or "",
                title=assessment.title or final_url,
                final_url=final_url,
                output_root=output_root,
            )
            document_path = self._save_source_markdown(
                title=assessment.title or final_url,
                text=self._build_presentation_ready_markdown(
                    html_text=response.text or "",
                    page_url=final_url,
                    assessment=assessment,
                    media_candidates=media_candidates,
                    output_root=output_root,
                ),
                output_root=output_root,
            )
            result = self._success(
                source_url=source_url,
                content_type="html",
                final_url=final_url,
                local_path=str(local_path),
                document_path=str(document_path),
                text_length=assessment.total_text_length,
                substantial_block_count=assessment.substantial_block_count,
                extraction_method="direct" if depth == 0 else "child_link",
                goal=goal,
                summary_hint=summary_hint,
                total_text_length=assessment.total_text_length,
                has_explanatory_motion_media=has_motion_media,
                has_complete_content=has_complete_content,
                has_static_visual_media=has_static_visual_media,
                has_direct_media_links=has_direct_media_links,
                presentation_fitness_score=presentation_fitness_score,
                media_candidates=media_candidates,
                media_stats=media_stats,
                tried_urls=current_tried_urls,
            )
            result.candidates = child_candidates
            return result

        failure = self._failure(
            source_url=source_url,
            content_type="html",
            final_url=final_url,
            text_length=assessment.best_run_char_count,
            substantial_block_count=assessment.best_run_block_count,
            extraction_method="direct" if depth == 0 else "child_link",
            goal=goal,
            summary_hint=summary_hint,
            total_text_length=assessment.total_text_length,
            has_explanatory_motion_media=has_motion_media,
            has_complete_content=has_complete_content,
            has_static_visual_media=has_static_visual_media,
            has_direct_media_links=has_direct_media_links,
            presentation_fitness_score=presentation_fitness_score,
            error="html page does not contain enough continuous text and no child content link was found",
            tried_urls=current_tried_urls,
            media_candidates=media_candidates,
            media_stats=media_stats,
        )
        failure.candidates = child_candidates
        return failure

    def _resolve_direct_document(
        self,
        *,
        response: requests.Response,
        source_url: str,
        output_root: Path,
        extraction_method: str,
        goal: str,
        summary_hint: str,
        tried_urls: list[str],
    ) -> ResolvedContent | None:
        final_url = self._normalize_url(response.url)
        content_type = self._content_type(response)

        if self._looks_like_pdf(response, final_url):
            if len(response.content) < self.min_document_bytes and not response.content.startswith(b"%PDF-1."):
                return self._failure(
                    source_url=source_url,
                    content_type="pdf",
                    final_url=final_url,
                    extraction_method=extraction_method,
                    goal=goal,
                    summary_hint=summary_hint,
                    error="pdf too small to be treated as complete content",
                    tried_urls=tried_urls,
                )
            local_path = self._save_binary(response.content, final_url, output_root, ".pdf")
            return self._success(
                source_url=source_url,
                content_type="pdf",
                final_url=final_url,
                local_path=str(local_path),
                extraction_method=extraction_method,
                goal=goal,
                summary_hint=summary_hint,
                tried_urls=tried_urls,
            )

        if content_type in DOC_MIME_TYPES or final_url.lower().endswith((".doc", ".docx")):
            if len(response.content) < self.min_document_bytes:
                return self._failure(
                    source_url=source_url,
                    content_type="doc",
                    final_url=final_url,
                    extraction_method=extraction_method,
                    goal=goal,
                    summary_hint=summary_hint,
                    error="document too small to be treated as complete content",
                    tried_urls=tried_urls,
                )
            extension = ".docx" if content_type == DOCX_MIME_TYPE or final_url.lower().endswith(".docx") else ".doc"
            local_path = self._save_binary(response.content, final_url, output_root, extension)
            return self._success(
                source_url=source_url,
                content_type="doc",
                final_url=final_url,
                local_path=str(local_path),
                extraction_method=extraction_method,
                goal=goal,
                summary_hint=summary_hint,
                tried_urls=tried_urls,
            )

        return None


__all__ = [
    "ContentLinkCandidate",
    "ContentResolver",
    "MediaCandidate",
    "MediaStats",
    "ResolvedContent",
]
