from __future__ import annotations

import html
import os
from pathlib import Path

from .content_resolver_models import MediaCandidate, _HtmlAssessment, _MarkdownBlock
from .content_resolver_parsers import _StructuredMarkdownParser


class ContentResolverMarkdownMixin:
    def _build_presentation_ready_markdown(
        self,
        *,
        html_text: str,
        page_url: str,
        assessment: _HtmlAssessment,
        media_candidates: list[MediaCandidate],
        output_root: Path,
    ) -> str:
        parser = _StructuredMarkdownParser(page_url=page_url)
        parser.feed(html_text)
        blocks = self._filter_markdown_blocks(
            parser.blocks,
            title=assessment.title or parser.title,
        )
        blocks = self._localize_markdown_media_blocks(
            blocks=blocks,
            media_candidates=media_candidates,
            output_root=output_root,
        )
        markdown_text = self._render_markdown_blocks(blocks).strip()
        if markdown_text:
            return markdown_text
        return (assessment.best_run_text or assessment.full_text).strip()

    def _filter_markdown_blocks(
        self,
        blocks: list[_MarkdownBlock],
        *,
        title: str,
    ) -> list[_MarkdownBlock]:
        filtered: list[_MarkdownBlock] = []
        normalized_titles = self._normalized_title_variants(title)

        for block in blocks:
            if block.kind in {"paragraph", "bullet", "heading"}:
                normalized = self._normalize_text_for_compare(block.text)
                if not normalized:
                    continue
                if normalized in normalized_titles:
                    continue
                if self._looks_like_boilerplate_text(normalized):
                    continue
                if self._looks_like_macro_block(block.text):
                    continue
                filtered.append(
                    _MarkdownBlock(
                        kind=block.kind,
                        text=block.text.strip(),
                        level=block.level,
                        url=block.url,
                        caption=block.caption.strip(),
                    )
                )
                continue
            if block.kind in {"image", "video"} and block.url:
                filtered.append(block)

        return self._drop_heading_clusters(filtered)

    def _localize_markdown_media_blocks(
        self,
        *,
        blocks: list[_MarkdownBlock],
        media_candidates: list[MediaCandidate],
        output_root: Path,
    ) -> list[_MarkdownBlock]:
        if not media_candidates:
            return blocks
        local_media_map: dict[str, str] = {}
        for candidate in media_candidates:
            if not candidate.local_path:
                continue
            try:
                relative_path = os.path.relpath(candidate.local_path, output_root)
            except ValueError:
                relative_path = candidate.local_path
            local_media_map[self._normalize_url(candidate.url)] = relative_path.replace("\\", "/")

        if not local_media_map:
            return blocks

        localized: list[_MarkdownBlock] = []
        for block in blocks:
            if block.kind not in {"image", "video"} or not block.url:
                localized.append(block)
                continue
            local_url = local_media_map.get(self._normalize_url(block.url))
            if local_url is None:
                localized.append(block)
                continue
            localized.append(
                _MarkdownBlock(
                    kind=block.kind,
                    text=block.text,
                    level=block.level,
                    url=local_url,
                    caption=block.caption,
                )
            )
        return localized

    def _render_markdown_blocks(self, blocks: list[_MarkdownBlock]) -> str:
        rendered: list[str] = []
        for block in blocks:
            if block.kind == "heading":
                level = min(max(block.level, 2), 6)
                rendered.append(f"{'#' * level} {block.text.strip()}")
            elif block.kind == "paragraph":
                rendered.append(block.text.strip())
            elif block.kind == "bullet":
                rendered.append(f"- {block.text.strip()}")
            elif block.kind == "image":
                caption = block.caption.strip() or "image"
                rendered.append(f"![{caption}]({block.url})")
                if block.caption.strip():
                    rendered.append(f"Caption: {block.caption.strip()}")
            elif block.kind == "video":
                rendered.append(f"<video src=\"{block.url}\"></video>")
                if block.caption.strip():
                    rendered.append(f"Caption: {block.caption.strip()}")
        return "\n\n".join(chunk for chunk in rendered if chunk.strip())

    def _drop_heading_clusters(self, blocks: list[_MarkdownBlock]) -> list[_MarkdownBlock]:
        cleaned: list[_MarkdownBlock] = []
        idx = 0
        while idx < len(blocks):
            if blocks[idx].kind != "heading":
                cleaned.append(blocks[idx])
                idx += 1
                continue
            start = idx
            while idx < len(blocks) and blocks[idx].kind == "heading":
                idx += 1
            cluster = blocks[start:idx]
            if len(cluster) >= 4 and all(len(block.text) <= 100 for block in cluster):
                continue
            cleaned.extend(cluster)
        return cleaned

    def _normalize_text_for_compare(self, text: str) -> str:
        return " ".join(html.unescape(text).split()).strip().lower()

    def _normalized_title_variants(self, title: str) -> set[str]:
        variants = {self._normalize_text_for_compare(title)}
        for delimiter in ("|", "-", "—"):
            if delimiter in title:
                variants.add(self._normalize_text_for_compare(title.split(delimiter, 1)[0]))
        return {variant for variant in variants if variant}

    def _looks_like_boilerplate_text(self, normalized_text: str) -> bool:
        if len(normalized_text) <= 2:
            return True
        boilerplate_markers = (
            "toggle navigation",
            "skip to main content",
            "table of contents",
            "privacy policy",
            "terms of use",
            "cookie policy",
            "all authors contributed equally",
        )
        if normalized_text in boilerplate_markers:
            return True
        if normalized_text.startswith("click here"):
            return True
        if normalized_text.count("toggle") >= 1 and len(normalized_text) < 40:
            return True
        if normalized_text in {"home", "about", "blog", "search"}:
            return True
        return False

    def _looks_like_macro_block(self, text: str) -> bool:
        normalized = text.strip()
        if not normalized.startswith("$$"):
            return False
        macro_markers = normalized.count(r"\def") + normalized.count(r"\newcommand")
        return macro_markers >= 2

