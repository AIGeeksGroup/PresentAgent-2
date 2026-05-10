from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

from pptagent.llms import LLM
from pptagent.utils import package_join, pbasename

from .adapter import DeepResearchAdapter
from .content_resolver import ResolvedContent
from .dossier import ResearchSource

MEDIA_HOST_TOKENS = (
    "x.com",
    "twitter.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "tiktok.com",
    "bilibili.com",
    "instagram.com",
)
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
URL_RE = re.compile(r"https?://[^\s<>\]\)\"']+")
DOCUMENT_MEDIA_NEEDS_PROMPT = open(
    package_join("prompts", "document_media_needs.txt"), encoding="utf-8"
).read()
DOCUMENT_MEDIA_CANDIDATE_SELECTOR_PROMPT = open(
    package_join("prompts", "document_media_candidate_selector.txt"), encoding="utf-8"
).read()
DOCUMENT_MEDIA_PLACEMENT_PROMPT = open(
    package_join("prompts", "document_media_placement.txt"), encoding="utf-8"
).read()


@dataclass
class DocumentMediaNeed:
    concept: str
    rationale: str
    search_query: str


@dataclass
class DocumentMediaAttachment:
    concept: str
    search_query: str
    final_url: str
    local_media_paths: list[str]


@dataclass
class _MediaResolvedCandidate:
    source: ResearchSource
    result: ResolvedContent


class DocumentMediaResearcher:
    def __init__(self, adapter: DeepResearchAdapter | None = None):
        self.adapter = adapter or DeepResearchAdapter()

    def enrich_markdown(
        self,
        *,
        markdown_text: str,
        workspace_dir: str,
        language_model: LLM,
        deepresearch_root: str,
        conda_env: str = "",
        conda_executable: str = "conda",
        max_concepts: int = 3,
        max_wait_seconds: float = 600.0,
        poll_interval_seconds: float = 60.0,
        progress_callback=None,
    ) -> tuple[str, list[DocumentMediaAttachment]]:
        needs = self._extract_media_needs(
            markdown_text=markdown_text,
            language_model=language_model,
            max_concepts=max_concepts,
        )
        attachments: list[DocumentMediaAttachment] = []
        if not needs:
            return markdown_text, attachments

        media_root = Path(workspace_dir) / "external_media"
        media_root.mkdir(parents=True, exist_ok=True)

        for index, need in enumerate(needs, start=1):
            if progress_callback is not None:
                progress_callback(
                    f"[doc-media] concept={index}/{len(needs)} query={need.search_query}"
                )
            slug = self.adapter._slugify_topic(need.concept)
            need_workspace = media_root / slug
            need_workspace.mkdir(parents=True, exist_ok=True)

            sources = self._run_deepresearch_media_search(
                need=need,
                workspace_dir=str(need_workspace),
                deepresearch_root=deepresearch_root,
                conda_env=conda_env,
                conda_executable=conda_executable,
                max_wait_seconds=max_wait_seconds,
                progress_callback=progress_callback,
            )
            if progress_callback is not None:
                progress_callback(
                    f"[doc-media] platform-sources={len(sources)} concept={need.concept}"
                )
            if not sources:
                continue

            candidates = self._resolve_media_candidates(
                sources=sources,
                output_dir=str(need_workspace),
                concept=need.concept,
                progress_callback=progress_callback,
            )
            if not candidates:
                continue

            selected = self._choose_best_media_candidate(
                need=need,
                candidates=candidates,
                language_model=language_model,
            )
            media_paths = self._collect_local_motion_media(selected.result)
            if not media_paths:
                continue

            attachments.append(
                DocumentMediaAttachment(
                    concept=need.concept,
                    search_query=need.search_query,
                    final_url=selected.result.final_url or selected.result.source_url,
                    local_media_paths=media_paths,
                )
            )

        if not attachments:
            return markdown_text, attachments

        augmented_markdown = self._insert_media_near_relevant_content(
            markdown_text=markdown_text,
            attachments=attachments,
            language_model=language_model,
        )
        report_payload = {
            "needs": [asdict(need) for need in needs],
            "attachments": [asdict(item) for item in attachments],
        }
        (media_root / "external_media_report.json").write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return augmented_markdown, attachments

    def _extract_media_needs(
        self,
        *,
        markdown_text: str,
        language_model: LLM,
        max_concepts: int,
    ) -> list[DocumentMediaNeed]:
        prompt = DOCUMENT_MEDIA_NEEDS_PROMPT.format(
            max_concepts=max_concepts,
            source_markdown=markdown_text[:12000],
        )
        payload = language_model(prompt, return_json=True, temperature=0)
        if not isinstance(payload, list):
            return []
        needs: list[DocumentMediaNeed] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            concept = str(item.get("concept", "")).strip()
            search_query = str(item.get("search_query", "")).strip()
            if not concept or not search_query:
                continue
            needs.append(
                DocumentMediaNeed(
                    concept=concept,
                    rationale=str(item.get("rationale", "")).strip(),
                    search_query=search_query,
                )
            )
            if len(needs) >= max_concepts:
                break
        return needs

    def _run_deepresearch_media_search(
        self,
        *,
        need: DocumentMediaNeed,
        workspace_dir: str,
        deepresearch_root: str,
        conda_env: str,
        conda_executable: str,
        max_wait_seconds: float,
        progress_callback=None,
    ) -> list[ResearchSource]:
        workspace = Path(workspace_dir)
        dataset_path = workspace / "media_query.jsonl"
        report_path = workspace / "deepresearch_media.log"
        prepared_dataset = self.adapter.prepare_deepresearch_eval_data(
            question=need.search_query,
            deepresearch_root=deepresearch_root,
            dataset_path=str(dataset_path),
        )
        process, report_handle = self.adapter.launch_deepresearch(
            deepresearch_root=deepresearch_root,
            dataset_path=prepared_dataset,
            report_path=str(report_path),
            conda_env=conda_env or None,
            conda_executable=conda_executable,
            run_label="document_media_deepresearch",
        )
        deadline = time.monotonic() + max(max_wait_seconds, 0.0)
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                time.sleep(5.0)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)
            if report_handle is not None:
                report_handle.close()
        report_text = report_path.read_text(encoding="utf-8", errors="ignore") if report_path.exists() else ""
        return self._collect_platform_sources(report_text)

    def _collect_platform_sources(self, report_text: str) -> list[ResearchSource]:
        selected: list[ResearchSource] = []
        seen: set[str] = set()
        tool_call_urls: list[str] = []
        for tool_call in TOOL_CALL_RE.findall(report_text):
            try:
                payload = json.loads(tool_call)
            except json.JSONDecodeError:
                continue
            arguments = payload.get("arguments", {})
            for key in ("url", "urls"):
                urls = arguments.get(key, [])
                if isinstance(urls, str):
                    urls = [urls]
                for url in urls:
                    if isinstance(url, str):
                        tool_call_urls.append(url.strip())
        raw_urls = tool_call_urls + URL_RE.findall(report_text)
        for normalized_url in raw_urls:
            if not normalized_url or normalized_url in seen:
                continue
            lowered_url = normalized_url.lower()
            hostname = (urlparse(normalized_url).netloc or "").lower()
            if not any(
                token in hostname or token in lowered_url for token in MEDIA_HOST_TOKENS
            ) and not any(ext in lowered_url for ext in (".gif", ".mp4", ".webm", ".mov", ".m4v")):
                continue
            seen.add(normalized_url)
            selected.append(
                ResearchSource(
                    title=hostname or normalized_url,
                    url=normalized_url,
                    snippet="",
                    source_type="video",
                )
            )
            if len(selected) >= 8:
                break
        return selected

    def _resolve_media_candidates(
        self,
        *,
        sources: list[ResearchSource],
        output_dir: str,
        concept: str,
        progress_callback=None,
    ) -> list[_MediaResolvedCandidate]:
        resolved: list[_MediaResolvedCandidate] = []
        for source in sources:
            if progress_callback is not None:
                progress_callback(f"[doc-media] resolve-media-source url={source.url}")
            result = self.adapter.resolve_source_content(
                url=source.url,
                output_dir=output_dir,
                topic=concept,
                goal=f"Retrieve media asset only for concept: {concept}",
                summary_hint=source.snippet,
            )
            if not result.success:
                continue
            if not self._has_motion_asset(result):
                continue
            resolved.append(_MediaResolvedCandidate(source=source, result=result))
            if len(resolved) >= 5:
                break
        return resolved

    def _has_motion_asset(self, result: ResolvedContent) -> bool:
        return bool(
            result.media_stats.motion_count > 0
            or any(
                (getattr(candidate, "local_path", "") or "").lower().endswith(
                    (".gif", ".mp4", ".webm", ".mov", ".m4v")
                )
                for candidate in result.media_candidates
            )
        )

    def _choose_best_media_candidate(
        self,
        *,
        need: DocumentMediaNeed,
        candidates: list[_MediaResolvedCandidate],
        language_model: LLM,
    ) -> _MediaResolvedCandidate:
        if len(candidates) == 1:
            return candidates[0]

        lines: list[str] = []
        for idx, candidate in enumerate(candidates, start=1):
            stats = candidate.result.media_stats
            lines.extend(
                [
                    f"Candidate {idx}",
                    f"URL: {candidate.result.final_url or candidate.result.source_url}",
                    f"Title: {candidate.source.title}",
                    f"Snippet: {candidate.source.snippet[:500]}",
                    f"Motion media count: {stats.motion_count}",
                    f"Video count: {stats.video_count}",
                    f"GIF count: {stats.gif_count}",
                    f"Media urls: {json.dumps([item.url for item in candidate.result.media_candidates[:5]], ensure_ascii=False)}",
                    "",
                ]
            )
        try:
            prompt = DOCUMENT_MEDIA_CANDIDATE_SELECTOR_PROMPT.format(
                concept=need.concept,
                search_query=need.search_query,
                rationale=need.rationale,
                candidate_blocks="\n".join(lines).strip(),
            )
            payload = language_model(prompt, return_json=True, temperature=0)
            pick = int(payload.get("pick", 1))
        except Exception:
            pick = 1
        pick = max(1, min(pick, len(candidates)))
        return candidates[pick - 1]

    def _collect_local_motion_media(self, result: ResolvedContent) -> list[str]:
        collected: list[str] = []
        seen: set[str] = set()
        for candidate in result.media_candidates:
            local_path = getattr(candidate, "local_path", "") or ""
            if not local_path or local_path in seen:
                continue
            lowered = local_path.lower()
            if not any(
                lowered.endswith(ext)
                for ext in (".gif", ".mp4", ".webm", ".mov", ".m4v")
            ):
                continue
            normalized_path = self._normalize_motion_asset(local_path)
            if not normalized_path or normalized_path in seen:
                continue
            seen.add(normalized_path)
            collected.append(normalized_path)
            if len(collected) >= 2:
                break
        return collected

    def _normalize_motion_asset(self, local_path: str) -> str:
        lowered = local_path.lower()
        if lowered.endswith((".mp4", ".webm", ".mov", ".m4v")):
            return local_path
        if not lowered.endswith(".gif"):
            return ""
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path is None:
            return ""
        destination = str(Path(local_path).with_suffix(".mp4"))
        if os.path.exists(destination):
            return destination
        try:
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-i",
                    local_path,
                    "-movflags",
                    "faststart",
                    "-pix_fmt",
                    "yuv420p",
                    destination,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            return ""
        return destination if os.path.exists(destination) else ""

    def _insert_media_near_relevant_content(
        self,
        *,
        markdown_text: str,
        attachments: list[DocumentMediaAttachment],
        language_model: LLM | None = None,
    ) -> str:
        blocks = [block.strip() for block in re.split(r"\n\s*\n", markdown_text) if block.strip()]
        if not blocks:
            return markdown_text

        placement_indexes = self._choose_attachment_positions_with_llm(
            blocks=blocks,
            attachments=attachments,
            language_model=language_model,
        )
        insertions: dict[int, list[str]] = {}
        for attachment_idx, attachment in enumerate(attachments):
            media_block = self._render_attachment_block(attachment)
            target_index = placement_indexes[attachment_idx]
            insertions.setdefault(target_index, []).append(media_block)

        merged_blocks: list[str] = []
        for index, block in enumerate(blocks):
            merged_blocks.append(block)
            for media_block in insertions.get(index, []):
                merged_blocks.append(media_block)

        trailing_blocks = insertions.get(len(blocks), [])
        merged_blocks.extend(trailing_blocks)
        return "\n\n".join(merged_blocks).rstrip() + "\n"

    def _choose_attachment_positions_with_llm(
        self,
        *,
        blocks: list[str],
        attachments: list[DocumentMediaAttachment],
        language_model: LLM | None,
    ) -> dict[int, int]:
        if language_model is None or not blocks or not attachments:
            raise ValueError("language_model, blocks, and attachments are required for media placement")

        block_summaries = []
        for index, block in enumerate(blocks):
            preview = block.strip()
            if len(preview) > 500:
                preview = preview[:500] + "..."
            block_summaries.append(
                {
                    "index": index,
                    "content": preview,
                }
            )

        attachment_summaries = []
        for index, attachment in enumerate(attachments):
            attachment_summaries.append(
                {
                    "attachment_index": index,
                    "concept": attachment.concept,
                    "search_query": attachment.search_query,
                    "source_url": attachment.final_url,
                    "media_files": [pbasename(path) for path in attachment.local_media_paths],
                    "media_description": f"{attachment.concept} animation/video resource from {attachment.final_url or 'external source'}",
                }
            )

        prompt = DOCUMENT_MEDIA_PLACEMENT_PROMPT.format(
            source_blocks=json.dumps(block_summaries, ensure_ascii=False, indent=2),
            media_attachments=json.dumps(attachment_summaries, ensure_ascii=False, indent=2),
        )
        payload = language_model(prompt, return_json=True, temperature=0)

        placements: dict[int, int] = {}
        if not isinstance(payload, list):
            raise ValueError("document media placement response must be a JSON list")

        for item in payload:
            if not isinstance(item, dict):
                raise ValueError("each placement item must be a JSON object")
            try:
                attachment_index = int(item.get("attachment_index", -1))
                block_index = int(item.get("block_index", -1))
            except Exception as exc:
                raise ValueError("invalid attachment_index or block_index in media placement response") from exc
            if attachment_index < 0 or attachment_index >= len(attachments):
                raise ValueError(f"attachment_index out of range: {attachment_index}")
            if block_index < 0 or block_index > len(blocks):
                raise ValueError(f"block_index out of range: {block_index}")
            placements[attachment_index] = block_index
        missing_indexes = [idx for idx in range(len(attachments)) if idx not in placements]
        if missing_indexes:
            raise ValueError(f"missing placement results for attachments: {missing_indexes}")
        return placements

    def _render_attachment_block(self, attachment: DocumentMediaAttachment) -> str:
        lines: list[str] = []
        for media_path in attachment.local_media_paths:
            lines.append(f'<video src="{media_path}"></video>')
            lines.append(
                f"Caption: {attachment.concept} animation from {pbasename(media_path)}"
            )
            if attachment.final_url:
                lines.append(f"Source page: {attachment.final_url}")
        return "\n".join(lines).strip()
