from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

from .content_resolver_models import MediaCandidate, _MarkdownBlock, _TextBlock


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._href = href
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        anchor_text = " ".join(chunk.strip() for chunk in self._chunks if chunk.strip())
        self.links.append((self._href, anchor_text))
        self._href = None
        self._chunks = []


class _ReadableTextParser(HTMLParser):
    BLOCK_TAGS = {
        "p",
        "article",
        "main",
        "section",
        "div",
        "li",
        "blockquote",
        "pre",
        "td",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.blocks: list[_TextBlock] = []
        self._chunks: list[str] = []
        self._skip_depth = 0
        self._in_title = False
        self._in_anchor_depth = 0
        self._current_link_text_length = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if lowered == "title":
            self._in_title = True
        if lowered == "a":
            self._in_anchor_depth += 1
        if lowered in self.BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if lowered == "title":
            self._in_title = False
        if lowered == "a" and self._in_anchor_depth > 0:
            self._in_anchor_depth -= 1
        if lowered in self.BLOCK_TAGS:
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        cleaned = " ".join(data.split())
        if not cleaned:
            return
        if self._in_title:
            self.title = f"{self.title} {cleaned}".strip()
            return
        self._chunks.append(cleaned)
        if self._in_anchor_depth > 0:
            self._current_link_text_length += len(cleaned)

    def _flush(self) -> None:
        if not self._chunks:
            return
        block = html.unescape(" ".join(self._chunks)).strip()
        if block:
            self.blocks.append(
                _TextBlock(
                    text=block,
                    link_text_length=self._current_link_text_length,
                )
            )
        self._chunks = []
        self._current_link_text_length = 0


class _StructuredMarkdownParser(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "svg"}
    CONTAINER_SKIP_TAGS = {"nav", "header", "footer", "aside", "form"}
    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    TEXT_TAGS = {"p", "li", "blockquote", "pre"}
    DISPLAY_MATH_PATTERN = re.compile(
        r"(\\\[(?:.|\n)*?\\\]|\$\$(?:.|\n)*?\$\$|\\begin\{(?:equation\*?|align\*?|aligned\*?|gather\*?|multline\*?)\}(?:.|\n)*?\\end\{(?:equation\*?|align\*?|aligned\*?|gather\*?|multline\*?)\})",
        re.DOTALL,
    )

    def __init__(self, page_url: str) -> None:
        super().__init__()
        self.page_url = page_url
        self.title = ""
        self.blocks: list[_MarkdownBlock] = []
        self._skip_depth = 0
        self._container_skip_depth = 0
        self._in_title = False
        self._in_anchor_depth = 0
        self._current_tag: str | None = None
        self._current_chunks: list[str] = []
        self._current_link_text_length = 0
        self._figure_depth = 0
        self._in_figcaption = False
        self._figcaption_chunks: list[str] = []
        self._figure_media_indexes: list[int] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if lowered in self.CONTAINER_SKIP_TAGS:
            self._container_skip_depth += 1
            return
        if self._skip_depth > 0 or self._container_skip_depth > 0:
            return
        if lowered == "title":
            self._in_title = True
            return
        if lowered == "a":
            self._in_anchor_depth += 1
        if lowered == "figure":
            self._flush_text_block()
            self._figure_depth += 1
            self._figure_media_indexes = []
            self._figcaption_chunks = []
            return
        if lowered == "figcaption":
            self._in_figcaption = True
            return
        if lowered in self.HEADING_TAGS or lowered in self.TEXT_TAGS:
            self._flush_text_block()
            self._current_tag = lowered
            self._current_chunks = []
            self._current_link_text_length = 0
            return
        if lowered in {"img", "video", "source"}:
            self._flush_text_block()
            attr_map = dict(attrs)
            src = (attr_map.get("src") or "").strip()
            if not src:
                return
            absolute_url = urljoin(self.page_url, src)
            media_type = self._infer_media_type(lowered, absolute_url)
            caption = (
                (attr_map.get("alt") or "").strip()
                or (attr_map.get("title") or "").strip()
            )
            self.blocks.append(
                _MarkdownBlock(
                    kind=media_type,
                    url=absolute_url,
                    caption=caption,
                )
            )
            if self._figure_depth > 0:
                self._figure_media_indexes.append(len(self.blocks) - 1)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if lowered in self.CONTAINER_SKIP_TAGS and self._container_skip_depth > 0:
            self._container_skip_depth -= 1
            return
        if self._skip_depth > 0 or self._container_skip_depth > 0:
            return
        if lowered == "title":
            self._in_title = False
            return
        if lowered == "a" and self._in_anchor_depth > 0:
            self._in_anchor_depth -= 1
        if lowered == "figcaption":
            self._in_figcaption = False
            return
        if lowered == "figure" and self._figure_depth > 0:
            caption = html.unescape(
                " ".join(chunk.strip() for chunk in self._figcaption_chunks if chunk.strip())
            ).strip()
            if caption:
                for index in self._figure_media_indexes:
                    if not self.blocks[index].caption:
                        self.blocks[index].caption = caption
            self._figure_depth -= 1
            self._figure_media_indexes = []
            self._figcaption_chunks = []
            return
        if lowered == self._current_tag:
            self._flush_text_block()

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0 or self._container_skip_depth > 0:
            return
        raw_text = html.unescape(data)
        cleaned = " ".join(raw_text.split())
        if not cleaned:
            return
        if self._in_title:
            self.title = f"{self.title} {cleaned}".strip()
            return
        if self._in_figcaption:
            self._figcaption_chunks.append(cleaned)
            return
        if self._current_tag is None:
            self._append_loose_text_blocks(raw_text)
            return
        self._current_chunks.append(cleaned)
        if self._in_anchor_depth > 0:
            self._current_link_text_length += len(cleaned)

    def _flush_text_block(self) -> None:
        if self._current_tag is None or not self._current_chunks:
            self._current_tag = None
            self._current_chunks = []
            self._current_link_text_length = 0
            return
        text = html.unescape(" ".join(self._current_chunks)).strip()
        if text:
            if self._current_tag in self.HEADING_TAGS:
                self.blocks.append(
                    _MarkdownBlock(
                        kind="heading",
                        text=text,
                        level=int(self._current_tag[1]),
                    )
                )
            else:
                block_kind = "bullet" if self._current_tag == "li" else "paragraph"
                self.blocks.append(_MarkdownBlock(kind=block_kind, text=text))
        self._current_tag = None
        self._current_chunks = []
        self._current_link_text_length = 0

    def _append_loose_text_blocks(self, raw_text: str) -> None:
        if not self.DISPLAY_MATH_PATTERN.search(raw_text):
            return
        parts = self.DISPLAY_MATH_PATTERN.split(raw_text)
        for part in parts:
            if not part:
                continue
            stripped = part.strip()
            if not stripped:
                continue
            if self.DISPLAY_MATH_PATTERN.fullmatch(stripped):
                self.blocks.append(_MarkdownBlock(kind="paragraph", text=stripped))
                continue
            normalized = " ".join(stripped.split())
            if len(normalized) >= 40 and re.search(r"[A-Za-z]", normalized):
                self.blocks.append(_MarkdownBlock(kind="paragraph", text=normalized))

    def _infer_media_type(self, tag: str, src: str) -> str:
        lowered_src = src.lower()
        if ".gif" in lowered_src:
            return "image"
        if any(ext in lowered_src for ext in (".mp4", ".webm", ".mov")) or tag == "video":
            return "video"
        return "image"


class _MediaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.media_candidates: list[MediaCandidate] = []
        self.figure_count = 0
        self._figure_depth = 0
        self._current_figure_indexes: list[int] = []
        self._in_figcaption = False
        self._figcaption_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        attr_map = dict(attrs)
        if lowered == "figure":
            self.figure_count += 1
            self._figure_depth += 1
            self._current_figure_indexes = []
            self._figcaption_chunks = []
            return
        if lowered == "figcaption":
            self._in_figcaption = True
            return
        if lowered not in {"img", "video", "source"}:
            return

        src = (attr_map.get("src") or "").strip()
        if not src:
            return
        candidate = MediaCandidate(
            url=src,
            media_type=self._infer_media_type(lowered, src),
            tag=lowered,
            alt_text=(attr_map.get("alt") or "").strip(),
            title_text=(attr_map.get("title") or "").strip(),
        )
        self.media_candidates.append(candidate)
        if self._figure_depth > 0:
            self._current_figure_indexes.append(len(self.media_candidates) - 1)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "figcaption":
            self._in_figcaption = False
            return
        if lowered == "figure" and self._figure_depth > 0:
            caption = " ".join(chunk.strip() for chunk in self._figcaption_chunks if chunk.strip())
            if caption:
                for index in self._current_figure_indexes:
                    self.media_candidates[index].figure_caption = caption
            self._figure_depth -= 1
            self._current_figure_indexes = []
            self._figcaption_chunks = []

    def handle_data(self, data: str) -> None:
        if self._in_figcaption:
            self._figcaption_chunks.append(data)

    def _infer_media_type(self, tag: str, src: str) -> str:
        lowered_src = src.lower()
        if ".gif" in lowered_src:
            return "gif"
        if ".mp4" in lowered_src:
            return "video"
        if ".webm" in lowered_src:
            return "video"
        if ".mov" in lowered_src:
            return "video"
        if tag == "video":
            return "video"
        return "image"


__all__ = [
    "_AnchorParser",
    "_MediaParser",
    "_ReadableTextParser",
    "_StructuredMarkdownParser",
]
