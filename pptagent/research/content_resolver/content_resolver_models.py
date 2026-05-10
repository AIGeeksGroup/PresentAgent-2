from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ContentLinkCandidate:
    url: str
    anchor_text: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MediaCandidate:
    url: str
    media_type: str
    local_path: str = ""
    tag: str = ""
    alt_text: str = ""
    title_text: str = ""
    figure_caption: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MediaStats:
    gif_count: int = 0
    video_count: int = 0
    figure_count: int = 0
    image_count: int = 0
    direct_media_url_count: int = 0
    animation_hint_count: int = 0

    @property
    def motion_count(self) -> int:
        return self.gif_count + self.video_count

    @property
    def static_visual_count(self) -> int:
        return self.image_count + self.figure_count

    @property
    def embeddable_media_count(self) -> int:
        return self.video_count + self.direct_media_url_count

    @property
    def total_visual_count(self) -> int:
        return self.motion_count + self.static_visual_count

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["motion_count"] = self.motion_count
        payload["static_visual_count"] = self.static_visual_count
        payload["embeddable_media_count"] = self.embeddable_media_count
        payload["total_visual_count"] = self.total_visual_count
        return payload


@dataclass
class ResolvedContent:
    source_url: str
    success: bool
    content_type: str = "none"
    final_url: str = ""
    local_path: str = ""
    document_path: str = ""
    text_length: int = 0
    substantial_block_count: int = 0
    extraction_method: str = "none"
    goal: str = ""
    summary_hint: str = ""
    error: str = ""
    total_text_length: int = 0
    has_explanatory_motion_media: bool = False
    has_complete_content: bool = False
    has_static_visual_media: bool = False
    has_direct_media_links: bool = False
    presentation_fitness_score: int = 0
    tried_urls: list[str] = field(default_factory=list)
    candidates: list[ContentLinkCandidate] = field(default_factory=list)
    media_candidates: list[MediaCandidate] = field(default_factory=list)
    media_stats: MediaStats = field(default_factory=MediaStats)
    external_signals: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["candidates"] = [candidate.to_dict() for candidate in self.candidates]
        payload["media_candidates"] = [candidate.to_dict() for candidate in self.media_candidates]
        payload["media_stats"] = self.media_stats.to_dict()
        return payload


@dataclass
class _HtmlAssessment:
    title: str
    full_text: str
    best_run_text: str
    best_run_char_count: int
    best_run_block_count: int
    total_text_length: int
    substantial_block_count: int


@dataclass
class _TextBlock:
    text: str
    link_text_length: int = 0

    @property
    def link_ratio(self) -> float:
        if not self.text:
            return 0.0
        return self.link_text_length / max(len(self.text), 1)


@dataclass
class _MarkdownBlock:
    kind: str
    text: str = ""
    level: int = 0
    url: str = ""
    caption: str = ""


__all__ = [
    "ContentLinkCandidate",
    "MediaCandidate",
    "MediaStats",
    "ResolvedContent",
    "_HtmlAssessment",
    "_MarkdownBlock",
    "_TextBlock",
]
