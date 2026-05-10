from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ResearchSource:
    title: str
    url: str
    snippet: str = ""
    source_type: str = "web"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchSource":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            snippet=data.get("snippet", ""),
            source_type=data.get("source_type", "web"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchOutlineSection:
    title: str
    summary: str
    bullet_points: list[str] = field(default_factory=list)
    source_refs: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchOutlineSection":
        return cls(
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            bullet_points=list(data.get("bullet_points", [])),
            source_refs=list(data.get("source_refs", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchOutlineCandidate:
    title: str
    sections: list[ResearchOutlineSection] = field(default_factory=list)
    rationale: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchOutlineCandidate":
        return cls(
            title=data.get("title", ""),
            sections=[
                ResearchOutlineSection.from_dict(section)
                for section in data.get("sections", [])
            ],
            rationale=data.get("rationale", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "sections": [section.to_dict() for section in self.sections],
            "rationale": self.rationale,
        }


@dataclass
class ResearchMediaCandidate:
    title: str
    url: str
    media_type: str = "web"
    rationale: str = ""
    source_ref: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchMediaCandidate":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            media_type=data.get("media_type", "web"),
            rationale=data.get("rationale", ""),
            source_ref=data.get("source_ref"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchDossier:
    topic: str
    summary: str
    sources: list[ResearchSource] = field(default_factory=list)
    outline_candidates: list[ResearchOutlineCandidate] = field(default_factory=list)
    media_candidates: list[ResearchMediaCandidate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw_result: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResearchDossier":
        return cls(
            topic=data.get("topic", ""),
            summary=data.get("summary", ""),
            sources=[
                ResearchSource.from_dict(source) for source in data.get("sources", [])
            ],
            outline_candidates=[
                ResearchOutlineCandidate.from_dict(candidate)
                for candidate in data.get("outline_candidates", [])
            ],
            media_candidates=[
                ResearchMediaCandidate.from_dict(candidate)
                for candidate in data.get("media_candidates", [])
            ],
            metadata=dict(data.get("metadata", {})),
            raw_result=data.get("raw_result"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "sources": [source.to_dict() for source in self.sources],
            "outline_candidates": [
                candidate.to_dict() for candidate in self.outline_candidates
            ],
            "media_candidates": [
                candidate.to_dict() for candidate in self.media_candidates
            ],
            "metadata": self.metadata,
            "raw_result": self.raw_result,
        }

    @property
    def best_outline(self) -> ResearchOutlineCandidate | None:
        if len(self.outline_candidates) == 0:
            return None
        return self.outline_candidates[0]
