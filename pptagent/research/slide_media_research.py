from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from pptagent.document.element import (
    IMAGE_CAPTION_PROMPT,
    _build_gif_caption_prompt,
    _cleanup_temp_files,
    _extract_gif_summary_frames,
    _prepare_caption_image,
)
from pptagent.llms import LLM
from pptagent.utils import get_logger, package_join, pbasename

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
logger = get_logger(__name__)
SLIDE_MEDIA_DEEPRESEARCH_LOCK = threading.Lock()
SLIDE_MEDIA_SELECTION_STATE_LOCK = threading.Lock()
SLIDE_MEDIA_EXTERNAL_WINNERS: dict[str, str] = {}

SLIDE_MEDIA_REVIEW_PROMPT = open(
    package_join("prompts", "slide_media_review.txt"), encoding="utf-8"
).read()
SLIDE_MEDIA_SELECTOR_PROMPT = open(
    package_join("prompts", "slide_media_selector.txt"), encoding="utf-8"
).read()
SLIDE_MEDIA_LINK_JUDGE_PROMPT = open(
    package_join("prompts", "slide_media_link_judge.txt"), encoding="utf-8"
).read()


@dataclass
class SlideMediaAsset:
    local_path: str
    media_type: str
    caption: str
    source_url: str
    duration_seconds: float | None = None


@dataclass
class SlideMediaReview:
    keep_indexes: list[int]
    need_external_media: bool
    external_media_type: str
    search_query: str
    rationale: str


class SlideMediaResearcher:
    def __init__(self, adapter: DeepResearchAdapter | None = None):
        self.adapter = adapter or DeepResearchAdapter()

    def review_existing_media(
        self,
        *,
        presentation_outline: str,
        slide_content: str,
        slide_description: str,
        candidate_assets: list[SlideMediaAsset],
        language_model: LLM,
    ) -> SlideMediaReview:
        logger.info(
            "[slide_media:review:start] slide_description_chars=%s candidate_assets=%s",
            len(slide_description),
            len(candidate_assets),
        )
        try:
            candidate_blocks = []
            for index, asset in enumerate(candidate_assets):
                candidate_blocks.append(
                    {
                        "index": index,
                        "path": asset.local_path,
                        "caption": asset.caption,
                        "source_url": asset.source_url,
                        "media_type": asset.media_type,
                    }
                )
            prompt = SLIDE_MEDIA_REVIEW_PROMPT.format(
                presentation_outline=presentation_outline,
                slide_content=slide_content,
                slide_description=slide_description,
                candidate_media=json.dumps(candidate_blocks, ensure_ascii=False, indent=2),
            )
            logger.info(
                "[slide_media:review:llm_call:start] candidate_assets=%s prompt_chars=%s",
                len(candidate_assets),
                len(prompt),
            )
            payload = language_model(prompt, return_json=True, temperature=0)
            logger.info(
                "[slide_media:review:llm_call:done] candidate_assets=%s need_external_media=%s external_media_type=%s rationale=%r",
                len(candidate_assets),
                payload.get("need_external_media", False),
                payload.get("external_media_type", "none"),
                str(payload.get("rationale", "")).strip(),
            )
            raw_indexes = payload.get("keep_indexes", [])
            if not isinstance(raw_indexes, list):
                raw_indexes = []
            keep_indexes: list[int] = []
            for item in raw_indexes:
                try:
                    index = int(item)
                except Exception:
                    continue
                if 0 <= index < len(candidate_assets) and index not in keep_indexes:
                    keep_indexes.append(index)
            external_media_type = str(
                payload.get("external_media_type", "none")
            ).strip().lower()
            if external_media_type not in {"image", "video", "none"}:
                external_media_type = "none"
            need_external_media = bool(payload.get("need_external_media", False))
            search_query = str(payload.get("search_query", "")).strip()
            if not need_external_media or external_media_type == "none":
                need_external_media = False
                external_media_type = "none"
                search_query = ""
            review = SlideMediaReview(
                keep_indexes=keep_indexes,
                need_external_media=need_external_media,
                external_media_type=external_media_type,
                search_query=search_query,
                rationale=str(payload.get("rationale", "")).strip(),
            )
            logger.info(
                "[slide_media:review:done] kept=%s need_external_media=%s external_media_type=%s search_query=%r rationale=%r",
                len(review.keep_indexes),
                review.need_external_media,
                review.external_media_type,
                review.search_query,
                review.rationale,
            )
            return review
        except Exception:
            logger.exception(
                "[slide_media:review:error] candidate_assets=%s slide_description_chars=%s",
                len(candidate_assets),
                len(slide_description),
            )
            raise

    def retrieve_for_slide(
        self,
        *,
        presentation_outline: str,
        slide_content: str,
        slide_description: str,
        language_model: LLM,
        vision_model: LLM | None,
        workspace_dir: str,
        deepresearch_root: str,
        conda_env: str = "",
        conda_executable: str = "conda",
        max_wait_seconds: float = 420.0,
        preferred_media_type: str = "",
        preferred_search_query: str = "",
    ) -> SlideMediaAsset | None:
        preferred_media_type = preferred_media_type.strip().lower()
        preferred_search_query = preferred_search_query.strip()
        if preferred_media_type not in {"image", "video"}:
            return None
        search_query = preferred_search_query or self._fallback_search_query(
            slide_description=slide_description,
            slide_content=slide_content,
            media_type=preferred_media_type,
        )
        if not search_query.strip():
            return None

        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        run_key = str(workspace.parent.resolve())
        workspace_key = str(workspace.resolve())
        with SLIDE_MEDIA_SELECTION_STATE_LOCK:
            winner = SLIDE_MEDIA_EXTERNAL_WINNERS.get(run_key, "")
        if winner and winner != workspace_key:
            logger.info(
                "[slide_media:deepresearch:skip] workspace=%s reason=external_media_already_selected winner=%s",
                workspace_key,
                winner,
            )
            return None
        logger.info(
            "[slide_media:deepresearch:wait] media_type=%s query=%r",
            preferred_media_type,
            search_query,
        )
        with SLIDE_MEDIA_DEEPRESEARCH_LOCK:
            with SLIDE_MEDIA_SELECTION_STATE_LOCK:
                winner = SLIDE_MEDIA_EXTERNAL_WINNERS.get(run_key, "")
            if winner and winner != workspace_key:
                logger.info(
                    "[slide_media:deepresearch:skip] workspace=%s reason=external_media_already_selected winner=%s",
                    workspace_key,
                    winner,
                )
                return None
            logger.info(
                "[slide_media:deepresearch:start] media_type=%s query=%r max_wait_seconds=%s",
                preferred_media_type,
                search_query,
                max_wait_seconds,
            )
            selected_asset = self._run_deepresearch_media_search(
                search_query=search_query,
                presentation_outline=presentation_outline,
                slide_content=slide_content,
                slide_description=slide_description,
                language_model=language_model,
                vision_model=vision_model,
                workspace_dir=str(workspace),
                deepresearch_root=deepresearch_root,
                conda_env=conda_env,
                conda_executable=conda_executable,
                max_wait_seconds=max_wait_seconds,
                media_type=preferred_media_type,
            )
        if selected_asset is None:
            return None
        with SLIDE_MEDIA_SELECTION_STATE_LOCK:
            SLIDE_MEDIA_EXTERNAL_WINNERS.setdefault(run_key, workspace_key)
        return selected_asset

    def _fallback_search_query(
        self,
        *,
        slide_description: str,
        slide_content: str,
        media_type: str,
    ) -> str:
        lines = [line.strip() for line in slide_content.splitlines()]
        informative_lines = [
            line
            for line in lines
            if line
            and not line.lower().startswith(("title:", "images:", "videos:"))
        ]
        summary = " ".join(informative_lines[:3]).strip()
        base = slide_description.strip()
        if summary:
            base = f"{base} {summary}".strip()
        suffix = "animation" if media_type == "video" else "figure"
        return f"{base} {suffix}".strip()

    def _run_deepresearch_media_search(
        self,
        *,
        search_query: str,
        presentation_outline: str,
        slide_content: str,
        slide_description: str,
        language_model: LLM,
        vision_model: LLM | None,
        workspace_dir: str,
        deepresearch_root: str,
        conda_env: str,
        conda_executable: str,
        max_wait_seconds: float,
        media_type: str,
    ) -> SlideMediaAsset | None:
        workspace = Path(workspace_dir)
        dataset_path = workspace / "slide_media_query.jsonl"
        report_path = workspace / "slide_media_deepresearch.log"
        prepared_dataset = self.adapter.prepare_deepresearch_eval_data(
            question=search_query,
            deepresearch_root=deepresearch_root,
            dataset_path=str(dataset_path),
        )
        process, report_handle = self.adapter.launch_deepresearch(
            deepresearch_root=deepresearch_root,
            dataset_path=prepared_dataset,
            report_path=str(report_path),
            conda_env=conda_env or None,
            conda_executable=conda_executable,
            run_label="slide_media_deepresearch",
            env_overrides={
                "PRESENTAGENT_DEEPRESEARCH_PROMPT_MODULE": "prompt_media",
                "PRESENTAGENT_DEEPRESEARCH_AGENT_MODULE": "react_agent_media",
                "MEDIA_SEARCH_MODE": "1",
            },
        )
        deadline = time.monotonic() + max(max_wait_seconds, 0.0)
        file_offset = 0
        partial_line = ""
        seen_urls: set[str] = set()
        accumulated_assets: list[SlideMediaAsset] = []
        seen_paths: set[str] = set()
        try:
            while True:
                raw_text, file_offset, partial_line = self.adapter._read_text_since(
                    report_path,
                    file_offset=file_offset,
                    partial_line=partial_line,
                )
                for entry in self.adapter._extract_visit_entries_from_text(raw_text):
                    if entry.url in seen_urls:
                        continue
                    seen_urls.add(entry.url)
                    candidate_dir = workspace / f"candidate_{len(seen_urls):02d}"
                    candidate_dir.mkdir(parents=True, exist_ok=True)
                    logger.info(
                        "[slide_media:deepresearch:resolve:start] url=%s",
                        entry.url,
                    )
                    result = self.adapter.resolve_source_content(
                        url=entry.url,
                        output_dir=str(candidate_dir),
                        topic=slide_description,
                        goal=entry.goal or f"Retrieve {media_type} media to support slide: {slide_description}",
                        summary_hint=entry.summary_hint,
                    )
                    candidate_assets = self._collect_assets_from_result(result, media_type)
                    llm_is_good_media, llm_rationale = self._judge_media_link(
                        presentation_outline=presentation_outline,
                        slide_content=slide_content,
                        slide_description=slide_description,
                        language_model=language_model,
                        media_type=media_type,
                        search_query=search_query,
                        page_url=result.final_url or entry.url,
                        candidate_assets=candidate_assets,
                    )
                    logger.info(
                        "[slide_media:deepresearch:resolve:done] resolved=%s raw_assets=%s motion=%s figures=%s url=%s llm_is_good_media=%s llm_rationale=%r resolver_reason=%r",
                        result.success,
                        len(candidate_assets),
                        result.media_stats.motion_count,
                        result.media_stats.figure_count,
                        entry.url,
                        llm_is_good_media,
                        llm_rationale,
                        self._resolve_result_reason(result),
                    )
                    if candidate_assets:
                        logger.info(
                            "[slide_media:deepresearch:assets] url=%s assets=%s",
                            entry.url,
                            json.dumps(
                                [
                                    {
                                        "path": asset.local_path,
                                        "source_url": asset.source_url,
                                        "caption": asset.caption,
                                        "duration_seconds": asset.duration_seconds,
                                    }
                                    for asset in candidate_assets
                                ],
                                ensure_ascii=False,
                            ),
                        )
                    logger.info(
                        "[slide_media:deepresearch:link_judge] url=%s is_good_media=%s rationale=%r",
                        result.final_url or entry.url,
                        llm_is_good_media,
                        llm_rationale,
                    )
                    if not llm_is_good_media:
                        continue
                    if not result.success:
                        continue
                    if candidate_assets:
                        logger.info(
                            "[slide_media:deepresearch:assets:kept] url=%s assets=%s",
                            entry.url,
                            json.dumps(
                                [
                                    {
                                        "path": asset.local_path,
                                        "source_url": asset.source_url,
                                        "caption": asset.caption,
                                        "duration_seconds": asset.duration_seconds,
                                    }
                                    for asset in candidate_assets
                                ],
                                ensure_ascii=False,
                            ),
                        )
                    if not candidate_assets:
                        continue
                    fresh_assets: list[SlideMediaAsset] = []
                    for asset in candidate_assets:
                        if asset.local_path in seen_paths:
                            continue
                        seen_paths.add(asset.local_path)
                        accumulated_assets.append(asset)
                        fresh_assets.append(asset)
                    if not fresh_assets:
                        continue
                    selected_asset = self._select_best_asset(
                        presentation_outline=presentation_outline,
                        slide_content=slide_content,
                        slide_description=slide_description,
                        language_model=language_model,
                        vision_model=vision_model,
                        media_type=media_type,
                        search_query=search_query,
                        candidate_assets=fresh_assets,
                    )
                    if selected_asset is not None:
                        logger.info(
                            "[slide_media:deepresearch:selected] path=%s source=%s duration_seconds=%s",
                            selected_asset.local_path,
                            selected_asset.source_url,
                            selected_asset.duration_seconds,
                        )
                        return selected_asset
                if time.monotonic() >= deadline:
                    logger.info(
                        "[slide_media:deepresearch:timeout] media_type=%s query=%r seen_urls=%s accumulated_assets=%s max_wait_seconds=%s",
                        media_type,
                        search_query,
                        len(seen_urls),
                        len(accumulated_assets),
                        max_wait_seconds,
                    )
                    break
                if process.poll() is not None:
                    break
                time.sleep(2.0)
            if accumulated_assets:
                selected_asset = self._select_best_asset(
                    presentation_outline=presentation_outline,
                    slide_content=slide_content,
                    slide_description=slide_description,
                    language_model=language_model,
                    vision_model=vision_model,
                    media_type=media_type,
                    search_query=search_query,
                    candidate_assets=accumulated_assets,
                )
                if selected_asset is not None:
                    logger.info(
                        "[slide_media:deepresearch:selected] path=%s source=%s duration_seconds=%s",
                        selected_asset.local_path,
                        selected_asset.source_url,
                        selected_asset.duration_seconds,
                    )
                return selected_asset
            return None
        finally:
            try:
                if process.poll() is None:
                    logger.info("[slide_media:deepresearch:stop] action=terminate")
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        logger.info("[slide_media:deepresearch:stop] action=kill")
                        process.kill()
                        process.wait(timeout=10)
                    logger.info("[slide_media:deepresearch:stop] action=stopped")
            finally:
                if report_handle is not None:
                    report_handle.close()

    def _collect_assets_from_result(
        self,
        result: ResolvedContent,
        media_type: str,
    ) -> list[SlideMediaAsset]:
        assets: list[SlideMediaAsset] = []
        for candidate in result.media_candidates:
            local_path = getattr(candidate, "local_path", "") or ""
            if not local_path:
                continue
            normalized_path = (
                self._normalize_motion_asset(local_path)
                if media_type == "video"
                else local_path
            )
            if not normalized_path or not os.path.exists(normalized_path):
                continue
            lowered = normalized_path.lower()
            if media_type == "video":
                if not lowered.endswith((".mp4", ".webm", ".mov", ".m4v")):
                    continue
            else:
                if lowered.endswith((".mp4", ".webm", ".mov", ".m4v", ".gif")):
                    continue
            caption = (
                getattr(candidate, "figure_caption", "") or getattr(candidate, "alt_text", "") or getattr(candidate, "title_text", "") or pbasename(normalized_path)
            )
            assets.append(
                SlideMediaAsset(
                    local_path=normalized_path,
                    media_type=media_type,
                    caption=caption.strip(),
                    source_url=result.final_url or result.source_url,
                    duration_seconds=(
                        self._measure_media_duration_seconds(normalized_path)
                        if media_type == "video"
                        else None
                    ),
                )
            )
        return assets

    def _resolve_result_reason(self, result: ResolvedContent) -> str:
        if not result.success:
            return result.error or "resolve_failed"
        reasons: list[str] = []
        if result.has_explanatory_motion_media:
            reasons.append("has_explanatory_motion_media")
        if result.has_static_visual_media:
            reasons.append("has_static_visual_media")
        if result.has_direct_media_links:
            reasons.append("has_direct_media_links")
        if result.has_complete_content:
            reasons.append("has_complete_content")
        if result.media_stats.motion_count > 0:
            reasons.append(f"motion_count={result.media_stats.motion_count}")
        if result.media_stats.figure_count > 0:
            reasons.append(f"figure_count={result.media_stats.figure_count}")
        if result.media_stats.direct_media_url_count > 0:
            reasons.append(f"direct_media_url_count={result.media_stats.direct_media_url_count}")
        if not reasons:
            reasons.append("resolved_without_media_signal")
        return ", ".join(reasons)

    def _judge_media_link(
        self,
        *,
        presentation_outline: str,
        slide_content: str,
        slide_description: str,
        language_model: LLM,
        media_type: str,
        search_query: str,
        page_url: str,
        candidate_assets: list[SlideMediaAsset],
    ) -> tuple[bool, str]:
        prompt = SLIDE_MEDIA_LINK_JUDGE_PROMPT.format(
            presentation_outline=presentation_outline,
            slide_content=slide_content,
            slide_description=slide_description,
            media_type=media_type,
            search_query=search_query,
            page_url=page_url,
            candidate_media=json.dumps(
                [
                    {
                        "path": asset.local_path,
                        "caption": asset.caption,
                        "source_url": asset.source_url,
                        "media_type": asset.media_type,
                        "duration_seconds": asset.duration_seconds,
                    }
                    for asset in candidate_assets
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        try:
            payload = language_model(prompt, return_json=True, temperature=0)
        except Exception:
            return False, "link_judge_failed"
        return bool(payload.get("is_good_media", False)), str(payload.get("rationale", "")).strip()

    def _looks_like_strong_technical_asset(
        self,
        asset: SlideMediaAsset,
        slide_text: str,
    ) -> bool:
        combined = f"{asset.caption}\n{asset.source_url}\n{asset.local_path}".lower()
        technical_terms = [
            "flow",
            "matching",
            "trajectory",
            "sampling",
            "transport",
            "diffusion",
            "algorithm",
            "pipeline",
            "mechanism",
            "architecture",
            "diagram",
            "figure",
            "visualization",
            "demo",
            "animation",
            "process",
        ]
        overlap = 0
        for term in technical_terms:
            if term in slide_text and term in combined:
                overlap += 1
        if overlap >= 1:
            return True
        return any(token in combined for token in ("diagram", "figure", "visualization", "animation", "demo"))

    def _measure_media_duration_seconds(self, local_path: str) -> float | None:
        if not os.path.exists(local_path):
            return None
        lowered = local_path.lower()
        if lowered.endswith(".gif"):
            try:
                total_ms = 0
                with Image.open(local_path) as gif:
                    frame_count = getattr(gif, "n_frames", 1)
                    for frame_index in range(frame_count):
                        gif.seek(frame_index)
                        total_ms += int(gif.info.get("duration", 0))
                if total_ms > 0:
                    return total_ms / 1000.0
            except Exception:
                return None
            return None
        ffprobe_path = shutil.which("ffprobe")
        if ffprobe_path is None:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe_path,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    local_path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except Exception:
            return None
        try:
            duration = float((result.stdout or "").strip())
        except Exception:
            return None
        return duration if duration > 0 else None

    def _select_best_asset(
        self,
        *,
        presentation_outline: str,
        slide_content: str,
        slide_description: str,
        language_model: LLM,
        vision_model: LLM | None,
        media_type: str,
        search_query: str,
        candidate_assets: list[SlideMediaAsset],
    ) -> SlideMediaAsset | None:
        candidate_blocks = []
        for index, asset in enumerate(candidate_assets):
            candidate_blocks.append(
                {
                    "index": index,
                    "path": asset.local_path,
                    "caption": asset.caption,
                    "source_url": asset.source_url,
                    "media_type": asset.media_type,
                    "duration_seconds": asset.duration_seconds,
                }
            )
        prompt = SLIDE_MEDIA_SELECTOR_PROMPT.format(
            presentation_outline=presentation_outline,
            slide_content=slide_content,
            slide_description=slide_description,
            media_type=media_type,
            search_query=search_query,
            candidate_media=json.dumps(candidate_blocks, ensure_ascii=False, indent=2),
        )
        try:
            payload = language_model(prompt, return_json=True, temperature=0)
            selected_index = int(payload.get("selected_index", -1))
        except Exception:
            selected_index = 0
            payload = {}
        if selected_index < 0 or selected_index >= len(candidate_assets):
            return None
        selected = candidate_assets[selected_index]
        caption = str(payload.get("caption", "")).strip()
        if caption:
            selected.caption = caption
        selected_path = str(payload.get("selected_path", "")).strip()
        if selected_path and os.path.exists(selected_path):
            selected.local_path = selected_path
        self._refresh_asset_caption(
            asset=selected,
            vision_model=vision_model,
            slide_content=slide_content,
            slide_description=slide_description,
        )
        return selected

    def _refresh_asset_caption(
        self,
        *,
        asset: SlideMediaAsset,
        vision_model: LLM | None,
        slide_content: str,
        slide_description: str,
    ) -> None:
        if vision_model is None or not asset.local_path or not os.path.exists(asset.local_path):
            return
        near_chunks = (
            slide_description.strip()[:512],
            slide_content.strip()[:2048],
        )
        try:
            if asset.media_type == "video" and asset.local_path.lower().endswith(".gif"):
                frame_paths = _extract_gif_summary_frames(asset.local_path)
                try:
                    asset.caption = vision_model(
                        _build_gif_caption_prompt(near_chunks),
                        frame_paths,
                    ).strip()
                finally:
                    _cleanup_temp_files(frame_paths)
                return
            if asset.media_type == "image":
                caption_path, temp_path = _prepare_caption_image(asset.local_path)
                try:
                    asset.caption = vision_model(
                        IMAGE_CAPTION_PROMPT.render(markdown_caption=near_chunks),
                        caption_path,
                    ).strip()
                finally:
                    if temp_path is not None:
                        _cleanup_temp_files([temp_path])
                return
        except Exception:
            return

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
