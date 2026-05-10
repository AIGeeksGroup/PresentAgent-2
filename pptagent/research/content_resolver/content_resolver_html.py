from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from .content_resolver_constants import PDF_MIME_TYPES, PDF_SIGNATURE
from .content_resolver_models import ContentLinkCandidate, MediaCandidate, MediaStats, ResolvedContent, _HtmlAssessment, _TextBlock
from .content_resolver_parsers import _AnchorParser, _MediaParser, _ReadableTextParser


class ContentResolverHtmlMixin:
    def _assess_html(self, html_text: str) -> _HtmlAssessment:
        parser = _ReadableTextParser()
        parser.feed(html_text)
        blocks = self._clean_blocks(parser.blocks)

        best_run_blocks: list[_TextBlock] = []
        current_run_blocks: list[_TextBlock] = []
        substantial_block_count = 0

        for block in blocks:
            is_substantial = (
                len(block.text) >= self.min_block_chars
                and block.link_ratio <= self.max_block_link_ratio
            )
            if is_substantial:
                substantial_block_count += 1
                current_run_blocks.append(block)
            else:
                if self._run_char_count(current_run_blocks) > self._run_char_count(best_run_blocks):
                    best_run_blocks = list(current_run_blocks)
                current_run_blocks = []

        if self._run_char_count(current_run_blocks) > self._run_char_count(best_run_blocks):
            best_run_blocks = list(current_run_blocks)

        all_text = "\n\n".join(block.text for block in blocks).strip()
        best_run_text = "\n\n".join(block.text for block in best_run_blocks).strip()
        return _HtmlAssessment(
            title=parser.title.strip(),
            full_text=all_text,
            best_run_text=best_run_text,
            best_run_char_count=len(best_run_text),
            best_run_block_count=len(best_run_blocks),
            total_text_length=len(all_text),
            substantial_block_count=substantial_block_count,
        )

    def _has_enough_continuous_content(self, assessment: _HtmlAssessment) -> bool:
        return (
            assessment.best_run_char_count >= self.min_text_chars
            and assessment.best_run_block_count >= self.min_substantial_blocks
        )

    def _discover_child_candidates(
        self,
        page_url: str,
        html_text: str,
    ) -> list[ContentLinkCandidate]:
        parser = _AnchorParser()
        parser.feed(html_text)

        candidates: list[ContentLinkCandidate] = []
        for href, anchor_text in parser.links:
            absolute_url = urljoin(page_url, href)
            if not self._looks_followable(absolute_url):
                continue
            normalized_url = self._normalize_url(absolute_url)
            if normalized_url == self._normalize_url(page_url):
                continue
            candidates.append(
                ContentLinkCandidate(
                    url=normalized_url,
                    anchor_text=anchor_text,
                )
            )
        return self._dedupe(candidates)

    def _extract_media_candidates(
        self,
        *,
        page_url: str,
        html_text: str,
    ) -> tuple[list[MediaCandidate], MediaStats]:
        parser = _MediaParser()
        parser.feed(html_text)

        normalized_candidates: list[MediaCandidate] = []
        seen: set[tuple[str, str]] = set()
        stats = MediaStats(figure_count=parser.figure_count)
        direct_media_urls, animation_hint_count = self._extract_media_link_signals(
            page_url=page_url,
            html_text=html_text,
        )
        stats.direct_media_url_count = len(direct_media_urls)
        stats.animation_hint_count = animation_hint_count

        for candidate in parser.media_candidates:
            absolute_url = urljoin(page_url, candidate.url)
            normalized_url = self._normalize_url(absolute_url)
            key = (normalized_url, candidate.media_type)
            if key in seen:
                continue
            seen.add(key)
            normalized_candidate = MediaCandidate(
                url=normalized_url,
                media_type=candidate.media_type,
                local_path="",
                tag=candidate.tag,
                alt_text=candidate.alt_text,
                title_text=candidate.title_text,
                figure_caption=candidate.figure_caption,
            )
            normalized_candidates.append(normalized_candidate)
            if normalized_candidate.media_type == "gif":
                stats.gif_count += 1
            elif normalized_candidate.media_type == "video":
                stats.video_count += 1
            elif normalized_candidate.media_type == "image":
                stats.image_count += 1

        return normalized_candidates, stats

    def _extract_media_link_signals(
        self,
        *,
        page_url: str,
        html_text: str,
    ) -> tuple[list[str], int]:
        attr_pattern = re.compile(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
        url_pattern = re.compile(r"""https?://[^\s"'<>]+|//[^\s"'<>]+""", re.IGNORECASE)
        media_hints = (
            ".gif",
            ".mp4",
            ".webm",
            ".mov",
            "youtube.com",
            "youtu.be",
            "vimeo.com",
            "animation",
            "animated",
            "demo",
            "lottie",
        )
        discovered_urls: list[str] = []
        seen: set[str] = set()
        raw_candidates = list(attr_pattern.findall(html_text)) + list(url_pattern.findall(html_text))
        for candidate in raw_candidates:
            raw_candidate = candidate.strip()
            if not raw_candidate:
                continue
            try:
                absolute_url = urljoin(page_url, raw_candidate)
                normalized = self._normalize_url(absolute_url)
            except ValueError:
                continue
            lowered = normalized.lower()
            if normalized in seen:
                continue
            if any(hint in lowered for hint in media_hints):
                seen.add(normalized)
                discovered_urls.append(normalized)

        lowered_html = html_text.lower()
        animation_hint_count = sum(
            lowered_html.count(keyword)
            for keyword in (" animation", "animated", "interactive", "demo", "lottie", "autoplay")
        )
        return discovered_urls, animation_hint_count

    def _clean_blocks(self, blocks: list[_TextBlock]) -> list[_TextBlock]:
        cleaned: list[_TextBlock] = []
        for block in blocks:
            normalized = " ".join(block.text.split())
            if len(normalized) < 20:
                continue
            cleaned.append(
                _TextBlock(
                    text=normalized,
                    link_text_length=min(block.link_text_length, len(normalized)),
                )
            )
        return cleaned

    def _run_char_count(self, blocks: list[_TextBlock]) -> int:
        return len("\n\n".join(block.text for block in blocks).strip())

    def _has_explanatory_motion_media(
        self,
        *,
        html_text: str,
        assessment: _HtmlAssessment,
        topic: str,
        goal: str,
    ) -> bool:
        if assessment.total_text_length < 800:
            return False

        lower_html = html_text.lower()
        motion_media_pattern = re.compile(
            r"(<video\b|<source\b|\.gif(?:[\?#\"'])|\.mp4(?:[\?#\"'])|\.webm(?:[\?#\"'])|\.mov(?:[\?#\"'])|lottie|animation)",
            re.IGNORECASE,
        )
        if not motion_media_pattern.search(lower_html):
            return "youtube.com" in lower_html or "youtu.be" in lower_html or "vimeo.com" in lower_html

        context_text = f"{assessment.title} {assessment.full_text} {topic} {goal}".lower()
        explanatory_pattern = re.compile(
            r"(visual|animation|animated|gif|video|interactive|demo|walkthrough|illustrat|explain)",
            re.IGNORECASE,
        )
        if explanatory_pattern.search(context_text):
            return True

        topic_tokens = [token for token in re.findall(r"[a-z0-9]+", topic.lower()) if len(token) >= 4]
        return bool(topic_tokens) and all(token in context_text for token in topic_tokens)

    def _looks_followable(self, url: str) -> bool:
        lower_url = url.lower()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return False
        if lower_url.endswith(
            (
                ".jpg",
                ".jpeg",
                ".png",
                ".gif",
                ".svg",
                ".webp",
                ".css",
                ".js",
                ".xml",
                ".json",
                ".zip",
                ".tar",
                ".gz",
                ".mp4",
                ".mp3",
            )
        ):
            return False
        return True

    def _looks_like_pdf(self, response, final_url: str) -> bool:
        content_type = self._content_type(response)
        if content_type in PDF_MIME_TYPES:
            return True
        if self._is_pdf_like_url(final_url):
            return True
        return response.content.startswith(PDF_SIGNATURE)

    def _is_pdf_like_url(self, url: str) -> bool:
        lower_url = url.lower()
        parsed = urlparse(lower_url)
        path = parsed.path or ""
        return (
            path.endswith(".pdf")
            or "/pdf/" in path
            or ".pdf?" in lower_url
            or ".pdf&" in lower_url
        )

    def _is_html(self, response) -> bool:
        return "html" in self._content_type(response)

    def _content_type(self, response) -> str:
        return response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()

    def _dedupe(self, candidates: list[ContentLinkCandidate]) -> list[ContentLinkCandidate]:
        deduped: list[ContentLinkCandidate] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.url in seen:
                continue
            seen.add(candidate.url)
            deduped.append(candidate)
        return deduped

    def _media_rank_key(self, result: ResolvedContent) -> tuple[int, int, int, int]:
        stats = result.media_stats
        return (
            1 if result.has_complete_content else 0,
            stats.motion_count + stats.embeddable_media_count,
            stats.total_visual_count,
            result.total_text_length,
        )

    def _motion_media_count(self, result: ResolvedContent) -> int:
        return result.media_stats.motion_count

    def _has_static_visual_media(self, stats: MediaStats) -> bool:
        return stats.static_visual_count > 0

    def _has_direct_media_links(self, stats: MediaStats) -> bool:
        return stats.direct_media_url_count > 0

    def _presentation_fitness_score(
        self,
        *,
        assessment: _HtmlAssessment,
        stats: MediaStats,
        has_complete_content: bool,
        has_motion_media: bool,
    ) -> int:
        score = 0
        if has_complete_content:
            score += 100
        score += min(stats.static_visual_count, 12) * 3
        score += min(stats.motion_count, 6) * 15
        score += min(stats.direct_media_url_count, 4) * 10
        if has_motion_media:
            score += 20
        if assessment.total_text_length >= self.min_text_chars:
            score += 10
        return score
