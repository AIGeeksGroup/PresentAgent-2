import asyncio
import json
import os
import re
import traceback
from abc import ABC, abstractmethod
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from pptagent.agent import Agent
from pptagent.apis import API_TYPES, CodeExecutor
from pptagent.document import Document, OutlineItem
from pptagent.llms import LLM, AsyncLLM
from pptagent.presentation import (
    Layout,
    Picture,
    Presentation,
    ShapeElement,
    SlidePage,
    StyleArg,
)
from pptagent.research import SlideMediaAsset, SlideMediaResearcher
from pptagent.utils import Config, edit_distance, get_logger, tenacity_decorator

logger = get_logger(__name__)

style = StyleArg.all_true()
style.area = False

class FunctionalLayouts(Enum):
    OPENING = "opening"
    TOC = "table of contents"
    SECTION_OUTLINE = "section outline"
    ENDING = "ending"


FunctionalContent = {
    FunctionalLayouts.OPENING.value: "This slide is a presentation opening, presenting available meta information, like title, author, date, etc.",
    FunctionalLayouts.TOC.value: "This slide is the Table of Contents, outlining the presentation's sections. Please use the given Table of Contents, and remove numbering to generate the slide content.",
    FunctionalLayouts.SECTION_OUTLINE.value: "This slide is a section start , briefly presenting the section title, and optionally the section summary.",
    FunctionalLayouts.ENDING.value: "This slide is an *ending slide*, simply express your gratitude like 'Thank you!' or '谢谢' as the main title and *do not* include other meta information if not specified.",
}

TEMPLATE_RESIDUE_PATTERNS = [
    re.compile(r"^\d{4}[/-]\d{1,2}[/-]\d{1,2}$"),
    re.compile(r"^\d{1,3}$"),
    re.compile(r"^(?:GenAI Lab|Zhaolab|AI Research|AI Lab)$", re.IGNORECASE),
]
PRESENTER_PLACEHOLDER_PATTERN = re.compile(r"^Presenter Name$", re.IGNORECASE)
MEDIA_LINE_RE = re.compile(r"^(Image|Video):\s*(.+)$", re.IGNORECASE | re.MULTILINE)
CAPTION_LINE_RE = re.compile(r"^Caption:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass
class PPTGen(ABC):
    """
    Stage II: Presentation Generation
    An abstract base class for generating PowerPoint presentations.
    It accepts a reference presentation as input, then generates a presentation outline and slides.
    """

    roles = []
    text_embedder: LLM | AsyncLLM
    language_model: LLM | AsyncLLM
    vision_model: LLM | AsyncLLM
    retry_times: int = 3
    sim_bound: float = 0.5
    force_pages: bool = False
    error_exit: bool = False
    record_cost: bool = False
    length_factor: float | None = None
    add_section_outline_slides: bool = False
    _initialized: bool = False

    def __post_init__(self):
        self._initialized = False
        self._hire_staffs(self.record_cost, self.language_model, self.vision_model)
        assert (
            self.length_factor is None or self.length_factor > 0
        ), "length_factor must be positive or None"

    def set_reference(
        self,
        config: Config,
        slide_induction: dict,
        presentation: Presentation,
        hide_small_pic_ratio: Optional[float] = 0.2,
        keep_in_background: bool = True,
    ):
        """
        Set the reference presentation and extracted presentation information.

        Args:
            presentation (Presentation): The presentation object.
            slide_induction (dict): The slide induction data.

        Returns:
            PPTGen: The updated PPTGen object.
        """
        self.config = config
        self.presentation = presentation

        self.functional_layouts = slide_induction.pop("functional_keys")
        self.text_layouts = [
            k
            for k in slide_induction
            if k.endswith("text") and k not in self.functional_layouts
        ]
        self.multimodal_layouts = [
            k
            for k in slide_induction
            if not k.endswith("text") and k not in self.functional_layouts
        ]
        if len(self.text_layouts) == 0:
            self.text_layouts = self.multimodal_layouts
        if len(self.multimodal_layouts) == 0:
            self.multimodal_layouts = self.text_layouts

        self.layouts = {k: Layout.from_dict(k, v) for k, v in slide_induction.items()}
        self.empty_prs = deepcopy(self.presentation)
        assert (
            hide_small_pic_ratio is None or hide_small_pic_ratio > 0
        ), "hide_small_pic_ratio must be positive or None"
        if hide_small_pic_ratio is not None:
            self._hide_small_pics(hide_small_pic_ratio, keep_in_background)
        self._initialized = True
        return self

    def generate_pres(
        self,
        source_doc: Document,
        num_slides: Optional[int] = None,
        outline: Optional[list[OutlineItem]] = None,
    ):
        """
        Generate a PowerPoint presentation.

        Args:
            source_doc (Document): The source document.
            num_slides (Optional[int]): The number of slides to generate.
            outline (Optional[List[OutlineItem]]): The outline of the presentation.

        Returns:
            dict: A dictionary containing the presentation data and history.

        Raise:
            ValueError: if failed to generate presentation outline.
        """
        assert self._initialized, "PPTGen not initialized, call `set_reference` first"
        self.source_doc = source_doc
        succ_flag = True
        if outline is None:
            self.outline = self.generate_outline(num_slides, source_doc)
        else:
            self.outline = outline
        self.simple_outline = "\n".join(
            [
                f"Slide {slide_idx+1}: {item.purpose}"
                for slide_idx, item in enumerate(self.outline)
            ]
        )
        generated_slides = []
        code_executors = []
        for slide_idx, outline_item in enumerate(self.outline):
            if self.force_pages and slide_idx == num_slides:
                break
            try:
                slide, code_executor = self.generate_slide(slide_idx, outline_item)
                generated_slides.append(slide)
                code_executors.append(code_executor)
            except Exception as e:
                logger.warning(
                    "Failed to generate slide, error_exit=%s, error: %s",
                    self.error_exit,
                    str(e),
                )
                traceback.print_exc()
                if self.error_exit:
                    succ_flag = False
                    break

        # Collect history data
        history = self._collect_history(
            sum(code_executors, start=CodeExecutor(self.retry_times))
        )

        if succ_flag:
            self.empty_prs.slides = generated_slides
            prs = self.empty_prs
        else:
            prs = None

        self.empty_prs = deepcopy(self.presentation)
        return prs, history

    def generate_outline(
        self,
        num_slides: int,
        source_doc: Document,
    ):
        """
        Generate an outline for the presentation.

        Args:
            num_slides (int): The number of slides to generate.

        Returns:
            dict: The generated outline.
        """
        assert self._initialized, "PPTGen not initialized, call `set_reference` first"
        content_slide_budget = self._content_slide_budget(num_slides)
        turn_id, outline = self.staffs["planner"](
            num_slides=content_slide_budget,
            document_overview=source_doc.get_overview(),
        )
        if num_slides == 1 and isinstance(outline, dict):
            outline = [outline]
        outline = self._fix_outline(outline, source_doc, turn_id)
        outline = self._enforce_outline_budget(outline, content_slide_budget)
        return self._add_functional_layouts(outline)

    @abstractmethod
    def generate_slide(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[SlidePage, CodeExecutor]:
        """
        Generate a slide from the outline item.
        """
        raise NotImplementedError("Subclass must implement this method")

    def _add_functional_layouts(self, outline: list[OutlineItem]):
        """
        Add functional layouts to the outline.
        """
        toc = []
        for item in outline:
            if item.section not in toc and item.section != "Functional":
                toc.append(item.section)
        self.toc = "\n".join(toc)

        fixed_functional_slides = [
            (FunctionalLayouts.TOC.value, 0),  # toc should be inserted before opening
            (FunctionalLayouts.OPENING.value, 0),
            (FunctionalLayouts.ENDING.value, 999999),  # append to the end
        ]
        for title, pos in fixed_functional_slides:
            layout = max(
                self.functional_layouts,
                key=lambda x: edit_distance(x.lower(), title),
            )
            if edit_distance(layout, title) > 0.7:
                outline.insert(pos, OutlineItem(title, "Functional", {}, []))

        if not self.add_section_outline_slides:
            return outline

        section_outline = max(
            self.functional_layouts,
            key=lambda x: edit_distance(x, FunctionalLayouts.SECTION_OUTLINE.value),
        )
        if (
            not edit_distance(section_outline, FunctionalLayouts.SECTION_OUTLINE.value)
            > 0.7
        ):
            return outline
        full_outline = []
        pre_section = None
        for item in outline:
            if item.section == "Functional":
                full_outline.append(item)
                continue
            if item.section != pre_section:
                new_item = OutlineItem(
                    FunctionalLayouts.SECTION_OUTLINE.value,
                    "Functional",
                    item.section,
                    [],
                )
                full_outline.append(new_item)
            full_outline.append(item)
            pre_section = item.section
        return full_outline

    def _fixed_functional_slide_count(self) -> int:
        count = 0
        for title in [
            FunctionalLayouts.OPENING.value,
            FunctionalLayouts.TOC.value,
            FunctionalLayouts.ENDING.value,
        ]:
            layout = max(
                self.functional_layouts,
                key=lambda x: edit_distance(x.lower(), title),
            )
            if edit_distance(layout, title) > 0.7:
                count += 1
        return count

    def _content_slide_budget(self, num_slides: int) -> int:
        if num_slides <= 1:
            return 1
        return max(1, num_slides - self._fixed_functional_slide_count())

    def _enforce_outline_budget(
        self, outline: list[OutlineItem], content_slide_budget: int
    ) -> list[OutlineItem]:
        if len(outline) <= content_slide_budget:
            return outline
        logger.warning(
            "Planner returned %d content slides for a budget of %d; trimming extras.",
            len(outline),
            content_slide_budget,
        )
        return outline[:content_slide_budget]

    def _remove_template_residue(self, slide: SlidePage):
        for father, shape in list(slide.shape_filter(ShapeElement, return_father=True)):
            if not shape.text_frame.is_textframe:
                continue
            kept_paragraphs = []
            for para in shape.text_frame.paragraphs:
                text = getattr(para, "text", "").strip()
                if text and PRESENTER_PLACEHOLDER_PATTERN.match(text):
                    para.text = "PresentAgent"
                    kept_paragraphs.append(para)
                    continue
                if text and any(pattern.match(text) for pattern in TEMPLATE_RESIDUE_PATTERNS):
                    continue
                kept_paragraphs.append(para)
            if kept_paragraphs:
                shape.text_frame.paragraphs = kept_paragraphs
            else:
                father.shapes.remove(shape)

    def _hide_small_pics(self, area_ratio: float, keep_in_background: bool):
        for layout in self.layouts.values():
            template_slide = self.presentation.slides[layout.template_id - 1]
            pictures = list(template_slide.shape_filter(Picture, return_father=True))
            if len(pictures) == 0:
                continue
            for father, pic in pictures:
                if pic.area / pic.slide_area < area_ratio:
                    if keep_in_background:
                        father.shapes.remove(pic)
                    else:
                        father.shapes.remove(pic)
                        father.backgrounds.append(pic)
                    layout.remove_item(pic.caption.strip())

            if len(list(template_slide.shape_filter(Picture))) == 0:
                logger.debug(
                    "All pictures in layout %s are too small, set to pure text layout",
                    layout.title,
                )
                layout.title = layout.title.replace(":image", ":text")

    def _build_slide_content(
        self,
        key_points: list,
        images: list[str],
        videos: list[str],
    ) -> str:
        slide_content = "Key Points:\n" + json.dumps(
            key_points, indent=2, ensure_ascii=False
        )
        if images:
            slide_content += "\nImages:\n" + "\n".join(images)
        if videos:
            slide_content += "\nVideos:\n" + "\n".join(videos)
        if images or videos:
            slide_content += (
                "\nVisual Guidance:\n"
                "- Prefer a single primary visual with strong explanatory value.\n"
                "- Use slide text to explain what the audience should notice in the visual and why it matters.\n"
                "- Avoid decorative visuals when a mechanism, comparison, result, or animation is available.\n"
            )
        slide_content += (
            "\nContent Density Guidance:\n"
            "- For technical body slides, avoid one-sentence filler.\n"
            "- Use available text slots for mechanism, interpretation, comparison, and takeaway.\n"
            "- If a visual is present, explain how to read it and connect it to the slide's claim.\n"
        )
        return slide_content

    def _parse_media_candidates(
        self,
        media_blocks: list[str],
        media_type: str,
    ) -> list[SlideMediaAsset]:
        candidates: list[SlideMediaAsset] = []
        for block in media_blocks:
            media_match = MEDIA_LINE_RE.search(block)
            if media_match is None:
                continue
            path = media_match.group(2).strip()
            caption_match = CAPTION_LINE_RE.search(block)
            caption = caption_match.group(1).strip() if caption_match else path
            candidates.append(
                SlideMediaAsset(
                    local_path=path,
                    media_type=media_type,
                    caption=caption,
                    source_url="",
                )
            )
        return candidates

    def _format_media_candidates(
        self,
        candidates: list[SlideMediaAsset],
    ) -> tuple[list[str], list[str]]:
        images: list[str] = []
        videos: list[str] = []
        for asset in candidates:
            media_line = (
                f"Image: {asset.local_path}\nCaption: {asset.caption}"
                if asset.media_type == "image"
                else f"Video: {asset.local_path}\nCaption: {asset.caption}"
            )
            if asset.media_type == "image":
                images.append(media_line)
            elif asset.media_type == "video":
                videos.append(media_line)
        return images, videos

    def _slide_media_workspace(self, slide_idx: int) -> Path:
        override_root = os.getenv("PRESENTAGENT_SLIDE_MEDIA_OUTPUT_DIR", "").strip()
        base_dir = Path(override_root) if override_root else Path(self.config.RUN_DIR)
        return base_dir / "slide_media" / f"slide_{slide_idx + 1:02d}"

    def _extract_media_captions(self, media_blocks: list[str]) -> list[str]:
        captions: list[str] = []
        for block in media_blocks:
            match = CAPTION_LINE_RE.search(block)
            if match is not None:
                captions.append(match.group(1).strip())
        return captions

    def _augment_content_source_with_media_context(
        self,
        content_source: str,
        images: list[str],
        videos: list[str],
    ) -> str:
        captions = self._extract_media_captions(images) + self._extract_media_captions(videos)
        if not captions:
            return content_source
        visual_context = "Visual References:\n" + "\n".join(
            [f"- {caption}" for caption in captions]
        )
        return f"{content_source}\n{visual_context}"

    def _write_slide_media_plan(
        self,
        slide_idx: int,
        payload: dict,
    ) -> None:
        workspace = self._slide_media_workspace(slide_idx)
        workspace.mkdir(parents=True, exist_ok=True)
        plan_path = workspace / "plan.json"
        plan_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _refine_slide_media_sync(
        self,
        slide_idx: int,
        header: str,
        slide_content: str,
        images: list[str],
        videos: list[str],
        language_model: LLM,
        vision_model: Optional[LLM] = None,
    ) -> tuple[list[str], list[str], bool]:
        # Usually slide-level deep research is reserved for pure text pages.
        # However, for mode-specific demo/case slides we still need to review
        # existing visuals, because a generic overview image can be worse than
        # having no visual at all.
        if (images or videos) and not self._should_force_media_review(
            header, images, videos
        ):
            self._write_slide_media_plan(
                slide_idx,
                {
                    "slide_idx": slide_idx + 1,
                    "slide_description": header,
                    "mode": "skip_non_text_slide",
                    "reason": "slide already has visual media; deep research is reserved for pure text slides",
                    "original_images": images,
                    "original_videos": videos,
                },
            )
            return images, videos, False

        researcher = SlideMediaResearcher()
        original_candidates = self._parse_media_candidates(images, "image") + self._parse_media_candidates(videos, "video")
        review = researcher.review_existing_media(
            presentation_outline=self.simple_outline,
            slide_content=slide_content,
            slide_description=header,
            candidate_assets=original_candidates,
            language_model=language_model,
        )
        kept_candidates = [
            original_candidates[index] for index in review.keep_indexes
        ]

        deepresearch_root = os.getenv("PRESENTAGENT_DEEPRESEARCH_ROOT", "").strip()
        external_asset: Optional[SlideMediaAsset] = None
        if review.need_external_media and deepresearch_root:
            workspace = self._slide_media_workspace(slide_idx)
            conda_env = os.getenv("PRESENTAGENT_DEEPRESEARCH_CONDA_ENV", "react_infer_env").strip()
            conda_executable = os.getenv("PRESENTAGENT_DEEPRESEARCH_CONDA_EXE", "conda").strip()
            max_wait_seconds = float(
                os.getenv("PRESENTAGENT_SLIDE_MEDIA_MAX_WAIT_SECONDS", "420")
            )
            external_asset = researcher.retrieve_for_slide(
                presentation_outline=self.simple_outline,
                slide_content=slide_content,
                slide_description=header,
                language_model=language_model,
                vision_model=vision_model,
                workspace_dir=str(workspace),
                deepresearch_root=deepresearch_root,
                conda_env=conda_env,
                conda_executable=conda_executable,
                max_wait_seconds=max_wait_seconds,
                preferred_media_type=review.external_media_type,
                preferred_search_query=review.search_query,
            )
            if external_asset is not None:
                kept_candidates.append(external_asset)

        refined_images, refined_videos = self._format_media_candidates(kept_candidates)
        media_changed = (
            refined_images != images
            or refined_videos != videos
            or external_asset is not None
        )
        self._write_slide_media_plan(
            slide_idx,
            {
                "slide_idx": slide_idx + 1,
                "slide_description": header,
                "original_candidates": [
                    {
                        "path": item.local_path,
                        "caption": item.caption,
                        "media_type": item.media_type,
                        "source_url": item.source_url,
                    }
                    for item in original_candidates
                ],
                "kept_indexes": review.keep_indexes,
                "kept_candidates": [
                    {
                        "path": item.local_path,
                        "caption": item.caption,
                        "media_type": item.media_type,
                        "source_url": item.source_url,
                    }
                    for item in kept_candidates
                ],
                "need_external_media": review.need_external_media,
                "external_media_type": review.external_media_type,
                "search_query": review.search_query,
                "review_rationale": review.rationale,
                "external_media_added": None
                if external_asset is None
                else {
                    "path": external_asset.local_path,
                    "caption": external_asset.caption,
                    "media_type": external_asset.media_type,
                    "source_url": external_asset.source_url,
                },
            },
        )
        return refined_images, refined_videos, media_changed

    def _should_force_media_review(
        self,
        slide_description: str,
        images: list[str],
        videos: list[str],
    ) -> bool:
        description = slide_description.lower()
        is_mode_demo_slide = (
            any(token in description for token in ("demo", "showcase", "output example"))
            and any(
                token in description
                for token in ("single", "discussion", "interaction", "q&a")
            )
        )
        is_case_slide = any(
            token in description
            for token in (
                "single-presentation case",
                "discussion-style case",
                "grounded audience question answering",
            )
        )
        if not (is_mode_demo_slide or is_case_slide):
            return False

        media_blob = "\n".join(images + videos).lower()
        generic_tokens = (
            "overview of the presentagent-2 framework",
            "flowchart illustrating the presentagent-2 framework",
            "agent framework",
            "framework",
            "benchmark",
            "evaluation pipeline",
            "three modes",
        )
        if any(token in media_blob for token in generic_tokens):
            return True

        target_tokens: list[str] = []
        if "single" in description:
            target_tokens.append("single")
        if "discussion" in description:
            target_tokens.append("discussion")
        if "interaction" in description or "q&a" in description:
            target_tokens.append("interaction")
        if "fastforward" in description:
            target_tokens.append("fastforward")
        if "chain of world" in description:
            target_tokens.append("chain of world")

        if target_tokens and not any(token in media_blob for token in target_tokens):
            return True
        return False

    async def _refine_slide_media_async(
        self,
        slide_idx: int,
        header: str,
        slide_content: str,
        images: list[str],
        videos: list[str],
    ) -> tuple[list[str], list[str], bool]:
        sync_language_model = (
            self.language_model.to_sync()
            if hasattr(self.language_model, "to_sync")
            else self.language_model
        )
        sync_vision_model = (
            self.vision_model.to_sync()
            if hasattr(self.vision_model, "to_sync")
            else self.vision_model
        )
        return await asyncio.to_thread(
            self._refine_slide_media_sync,
            slide_idx,
            header,
            slide_content,
            images,
            videos,
            sync_language_model,
            sync_vision_model,
        )

    def _select_best_layout_key(
        self,
        requested_layout: str,
        candidate_layouts: list[str],
    ) -> str:
        return max(
            candidate_layouts,
            key=lambda x: edit_distance(x, requested_layout),
        )

    def _should_use_video(
        self,
        slide_description: str,
        slide_content: str,
        videos: list[str],
    ) -> bool:
        if not videos:
            return False
        if any(".gif" in video.lower() for video in videos):
            return True
        _, result = self.staffs["video_usage_judge"](
            outline=self.simple_outline,
            slide_description=slide_description,
            slide_content=slide_content,
            videos="\n".join(videos),
        )
        if bool(result.get("use_video", False)):
            return True
        return False

    def _generate_discussion(
        self,
        slide_content: str,
        slide_description: str,
    ) -> str:
        _, decision = self.staffs["discussion_judge"](
            slide_content=slide_content,
            slide_description=slide_description,
        )
        if not decision.get("need_discussion", False):
            return ""
        _, discussion = self.staffs["discussion_writer"](
            slide_content=slide_content,
            slide_description=slide_description,
        )
        question = str(discussion.get("question", "")).strip()
        draft_answer = str(discussion.get("answer", "")).strip()
        if not question:
            return ""
        _, answer_payload = self.staffs["discussion_answerer"](
            document_overview=self.source_doc.get_overview(include_summary=True),
            slide_content=slide_content,
            slide_description=slide_description,
            question=question,
            draft_answer=draft_answer,
        )
        answer = str(answer_payload.get("answer", "")).strip()
        if not answer:
            return ""
        return f"Speaker A: {question}\nSpeaker B: {answer}"

    def _notes_mode(self) -> str:
        raw_mode = os.getenv(
            "PRESENTAGENT_NOTES_MODE",
            "single_presentation",
        ).strip().lower()
        if raw_mode in {"discussion", "two_speaker", "two-speaker"}:
            return "discussion"
        return "single_presentation"

    def _merge_notes_with_discussion(
        self,
        notes: str,
        discussion: str,
    ) -> str:
        notes = str(notes).strip()
        discussion = str(discussion).strip()
        if not notes or not discussion:
            return notes
        return f"{notes}\n{discussion}"

    def _fix_outline(
        self, outline: list[dict], source_doc: Document, turn_id: int, retry: int = 0
    ) -> list[OutlineItem]:
        """
        Validate the generated outline.

        Raises:
            ValueError: If the outline is invalid.
        """
        try:
            outline_items = [
                OutlineItem.from_dict(outline_item) for outline_item in outline
            ]
            for outline_item in outline_items:
                outline_item.check_retrieve(source_doc, self.sim_bound)
                outline_item.check_images(
                    source_doc, self.text_embedder, self.sim_bound
                )
            return outline_items
        except Exception as e:
            retry += 1
            logger.info(
                "Failed to generate outline, tried %d/%d times, error: %s",
                retry,
                self.retry_times,
                str(e),
            )
            logger.debug(traceback.format_exc())
            if retry < self.retry_times:
                new_outline = self.staffs["planner"].retry(
                    str(e), traceback.format_exc(), turn_id, retry
                )
                return self._fix_outline(new_outline, source_doc, turn_id, retry)
            else:
                raise ValueError("Failed to generate outline, tried too many times")

    def _collect_history(self, code_executor: CodeExecutor):
        """
        Collect the history of code execution, API calls and agent steps.

        Returns:
            dict: The collected history data.
        """
        history = {
            "agents": {},
            "code_history": code_executor.code_history,
            "api_history": code_executor.api_history,
        }

        for role_name, role in self.staffs.items():
            history["agents"][role_name] = role.history
            role._history = []

        return history

    def _hire_staffs(
        self,
        record_cost: bool,
        language_model: LLM | AsyncLLM,
        vision_model: LLM | AsyncLLM,
    ) -> dict[str, Agent]:
        """
        Initialize agent roles and their models
        """
        llm_mapping = {
            "language": language_model,
            "vision": vision_model,
        }
        self.staffs = {
            role: Agent(
                role,
                record_cost=record_cost,
                text_model=self.text_embedder,
                llm_mapping=llm_mapping,
            )
            for role in ["planner"] + self.roles
        }

@dataclass
class PPTGenAsync(PPTGen):
    """
    Asynchronous base class for generating PowerPoint presentations.
    Extends PPTGen with async functionality.
    """

    def __post_init__(self):
        super().__post_init__()
        for k in list(self.staffs.keys()):
            self.staffs[k] = self.staffs[k].to_async()

    async def generate_pres(
        self,
        source_doc: Document,
        num_slides: Optional[int] = None,
        outline: Optional[list[OutlineItem]] = None,
    ):
        """
        Asynchronously generate a PowerPoint presentation.
        """
        assert (
            self._initialized
        ), "AsyncPPTAgent not initialized, call `set_reference` first"
        self.source_doc = source_doc
        succ_flag = True
        if outline is None:
            self.outline = await self.generate_outline(num_slides, source_doc)
        else:
            self.outline = outline
        self.simple_outline = "\n".join(
            [
                f"Slide {slide_idx+1}: {item.purpose}"
                for slide_idx, item in enumerate(self.outline)
            ]
        )

        slide_tasks = []
        for slide_idx, outline_item in enumerate(self.outline):
            if self.force_pages and slide_idx == num_slides:
                break
            slide_tasks.append(self.generate_slide(slide_idx, outline_item))

        slide_results = await asyncio.gather(*slide_tasks, return_exceptions=True)

        generated_slides = []
        code_executors = []
        for result in slide_results:
            if isinstance(result, Exception):
                if self.error_exit:
                    succ_flag = False
                    break
                continue
            if result is not None:
                slide, code_executor = result
                generated_slides.append(slide)
                code_executors.append(code_executor)

        history = self._collect_history(
            sum(code_executors, start=CodeExecutor(self.retry_times))
        )

        if succ_flag:
            self.empty_prs.slides = generated_slides
            prs = self.empty_prs
        else:
            prs = None

        self.empty_prs = deepcopy(self.presentation)
        return prs, history

    async def generate_outline(
        self,
        num_slides: int,
        source_doc: Document,
    ):
        """
        Asynchronously generate an outline for the presentation.
        """
        assert (
            self._initialized
        ), "AsyncPPTAgent not initialized, call `set_reference` first"

        logger.info("[pptgen:outline:start] num_slides=%s", num_slides)
        content_slide_budget = self._content_slide_budget(num_slides)
        turn_id, outline = await self.staffs["planner"](
            num_slides=content_slide_budget,
            document_overview=source_doc.get_overview(),
        )
        if num_slides == 1 and isinstance(outline, dict):
            outline = [outline]
        outline = await self._fix_outline(outline, source_doc, turn_id)
        outline = self._enforce_outline_budget(outline, content_slide_budget)
        outline = self._add_functional_layouts(outline)
        logger.info("[pptgen:outline:done] outline_items=%s", len(outline))
        return outline

    @abstractmethod
    async def generate_slide(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[SlidePage, CodeExecutor]:
        """
        Asynchronously generate a slide from the outline item.
        """
        raise NotImplementedError("Subclass must implement this method")

    async def _fix_outline(
        self, outline: list[dict], source_doc: Document, turn_id: int, retry: int = 0
    ) -> list[OutlineItem]:
        """
        Asynchronously validate the generated outline.
        """
        try:
            outline_items = [
                OutlineItem.from_dict(outline_item) for outline_item in outline
            ]
            async with asyncio.TaskGroup() as tg:
                for outline_item in outline_items:
                    outline_item.check_retrieve(source_doc, self.sim_bound)
                    tg.create_task(
                        outline_item.check_images_async(
                            source_doc, self.text_embedder, self.sim_bound
                        )
                    )
            return outline_items
        except Exception as e:
            retry += 1
            logger.info(
                "Failed to generate outline, tried %d/%d times, error: %s",
                retry,
                self.retry_times,
                str(e),
            )
            logger.debug(traceback.format_exc())
            if retry < self.retry_times:
                new_outline = await self.staffs["planner"].retry(
                    str(e), traceback.format_exc(), turn_id, retry
                )
                return await self._fix_outline(new_outline, source_doc, turn_id, retry)
            else:
                raise ValueError("Failed to generate outline, tried too many times")


class PPTAgent(PPTGen):
    """
    A class to generate PowerPoint presentations with a crew of agents.
    """

    roles: list[str] = [
        "editor",
        "coder",
        "content_organizer",
        "layout_selector",
        "notes_generator",
        "discussion_notes_generator",
        "discussion_judge",
        "discussion_writer",
        "discussion_answerer",
        "video_usage_judge",
    ]

    def generate_slide(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[SlidePage, CodeExecutor]:
        """
        Generate a slide from the outline item.
        """
        if outline_item.section == "Functional":
            layout = self.layouts[
                max(
                    self.functional_layouts,
                    key=lambda x: edit_distance(x, outline_item.purpose),
                )
            ]
            slide_desc = FunctionalContent[outline_item.purpose]
            if outline_item.purpose == FunctionalLayouts.SECTION_OUTLINE.value:
                outline_item.purpose = f"Section Outline of {outline_item.indexs}"
                outline_item.indexs = {}
                slide_content = (
                    "Overview of the Document:\n"
                    + self.source_doc.get_overview(include_summary=True)
                )
            elif outline_item.purpose == FunctionalLayouts.TOC.value:
                slide_content = "Table of Contents:\n" + self.toc
            else:
                slide_content = "This slide is a functional layout, please follow the slide description and content schema to generate the slide content."
            header, _, _, _ = outline_item.retrieve(slide_idx, self.source_doc)
            header += slide_desc
        else:
            layout, header, slide_content = self._select_layout(slide_idx, outline_item)
        command_list, template_id = self._generate_content(
            layout, slide_content, header
        )
        notes = self._generate_notes(slide_content, header)
        slide, code_executor = self._edit_slide(command_list, template_id, notes)
        slide.slide_notes = notes
        return slide, code_executor

    @tenacity_decorator
    def _select_layout(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[Layout, str, str]:
        """
        Select a layout for the slide.
        """
        header, content_source, images, videos = outline_item.retrieve(
            slide_idx, self.source_doc
        )
        media_review_content = content_source or header
        images, videos, _ = self._refine_slide_media_sync(
            slide_idx,
            header,
            media_review_content,
            images,
            videos,
            self.language_model,
            self.vision_model,
        )
        organized_content_source = self._augment_content_source_with_media_context(
            content_source,
            images,
            videos,
        )
        if len(organized_content_source) == 0:
            key_points = []
        else:
            _, key_points = self.staffs["content_organizer"](
                content_source=organized_content_source
            )
        slide_content_without_video = self._build_slide_content(key_points, images, [])
        use_video = self._should_use_video(header, slide_content_without_video, videos)
        active_videos = videos if use_video else []
        slide_content = self._build_slide_content(key_points, images, active_videos)
        layouts = self.text_layouts
        has_visuals = len(images) > 0 or len(active_videos) > 0
        if has_visuals:
            layouts = self.multimodal_layouts

        _, layout_selection = self.staffs["layout_selector"](
            outline=self.simple_outline,
            slide_description=header,
            slide_content=slide_content,
            available_layouts=layouts,
        )
        layout = self._select_best_layout_key(layout_selection["layout"], layouts)
        if "image" in layout and not has_visuals:
            logger.debug(
                f"An image layout: {layout} is selected, but no visual media are provided, please check the parsed document and outline item:\n {outline_item}"
            )
        elif "image" not in layout and has_visuals:
            logger.debug(
                f"A pure text layout: {layout} is selected, but visual media are provided, please check the parsed document and outline item:\n {outline_item}\n Set visual media to empty list."
            )
            media_start = slide_content.find("\nImages:\n")
            if media_start == -1:
                media_start = slide_content.find("\nVideos:\n")
            if media_start != -1:
                slide_content = slide_content[:media_start]
        return self.layouts[layout], header, slide_content

    def _generate_content(
        self,
        layout: Layout,
        slide_content: str,
        slide_description: str,
    ) -> tuple[list, int]:
        """
        Synergize Agents to generate a slide.

        Args:
            layout (Layout): The layout data.
            slide_content (str): The slide content.
            slide_description (str): The description of the slide.

        Returns:
            tuple[list, int]: The generated command list and template id.
        """
        turn_id, editor_output = self.staffs["editor"](
            outline=self.simple_outline,
            metadata=self.source_doc.metainfo,
            slide_description=slide_description,
            slide_content=slide_content,
            schema=layout.content_schema,
        )
        command_list, template_id = self._generate_commands(
            editor_output, layout, turn_id
        )
        return command_list, template_id

    def _generate_notes(
        self,
        slide_content: str,
        slide_description: str,
    ) -> str:
        """
        Generate speaker notes for a slide.
        """
        mode = self._notes_mode()
        if mode == "discussion":
            _, notes = self.staffs["discussion_notes_generator"](
                slide_content=slide_content,
                slide_description=slide_description,
            )
            return str(notes).strip()
        _, notes = self.staffs["notes_generator"](
            slide_content=slide_content,
            slide_description=slide_description,
        )
        return str(notes).strip()
    def _edit_slide(
        self, command_list: list, template_id: int, notes: str
    ) -> tuple[SlidePage, CodeExecutor]:
        code_executor = CodeExecutor(self.retry_times)
        turn_id, edit_actions = self.staffs["coder"](
            api_docs=code_executor.get_apis_docs(API_TYPES.Agent.value),
            edit_target=self.presentation.slides[template_id - 1].to_html(),
            command_list="\n".join([str(i) for i in command_list]),
        )
        for error_idx in range(self.retry_times):
            edit_slide: SlidePage = deepcopy(self.presentation.slides[template_id - 1])
            feedback = code_executor.execute_actions(
                edit_actions, edit_slide, self.source_doc
            )
            if feedback is None:
                break
            logger.info(
                "Failed to generate slide, tried %d/%d times, error: %s",
                error_idx + 1,
                self.retry_times,
                str(feedback[1]),
            )
            logger.debug(traceback.format_exc())
            if error_idx == self.retry_times:
                raise Exception(
                    f"Failed to generate slide, tried too many times at editing\ntraceback: {feedback[1]}"
                )
            edit_actions = self.staffs["coder"].retry(
                feedback[0], feedback[1], turn_id, error_idx + 1
            )
        self._remove_template_residue(edit_slide)
        self.empty_prs.build_slide(edit_slide)
        return edit_slide, code_executor

    def _generate_commands(
        self, editor_output: dict, layout: Layout, turn_id: int, retry: int = 0
    ):
        """
        Generate commands for editing the slide content.
        """
        command_list = []
        try:
            layout.validate(editor_output, self.source_doc.image_dir)
            if self.length_factor is not None:
                layout.validate_length(
                    editor_output, self.length_factor, self.language_model
                )
            old_data = layout.get_old_data(editor_output)
            template_id = layout.get_slide_id(editor_output)
        except Exception as e:
            if retry < self.retry_times:
                new_output = self.staffs["editor"].retry(
                    e,
                    traceback.format_exc(),
                    turn_id,
                    retry + 1,
                )
                return self._generate_commands(new_output, layout, turn_id, retry + 1)
            else:
                raise Exception(
                    f"Failed to generate commands, tried too many times at editing\ntraceback: {e}"
                )

        for el_name, old_content in old_data.items():
            if not isinstance(old_content, list):
                old_content = [old_content]

            new_content = editor_output.get(el_name, {"data": []})["data"]
            if not isinstance(new_content, list):
                new_content = [new_content]
            new_content = [i for i in new_content if i]
            quantity_change = len(new_content) - len(old_content)
            command_list.append(
                (
                    el_name,
                    layout[el_name].el_type,
                    f"quantity_change: {quantity_change}",
                    old_content,
                    new_content,
                )
            )

        assert len(command_list) > 0, "No commands generated"
        return command_list, template_id


class PPTAgentAsync(PPTGenAsync):
    """
    Asynchronous version of PPTAgent that uses AsyncAgent for concurrent processing.
    """

    roles: list[str] = [
        "editor",
        "coder",
        "content_organizer",
        "layout_selector",
        "notes_generator",
        "discussion_notes_generator",
        "discussion_judge",
        "discussion_writer",
        "discussion_answerer",
        "video_usage_judge",
    ]

    async def generate_slide(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[SlidePage, CodeExecutor]:
        """
        Asynchronously generate a slide from the outline item.
        """
        logger.info(
            "[pptgen:slide:start] slide=%s section=%s purpose=%s",
            slide_idx,
            outline_item.section,
            outline_item.purpose,
        )
        if outline_item.section == "Functional":
            layout = self.layouts[
                max(
                    self.functional_layouts,
                    key=lambda x: edit_distance(x.lower(), outline_item.purpose),
                )
            ]
            slide_desc = FunctionalContent[outline_item.purpose]
            if outline_item.purpose == FunctionalLayouts.SECTION_OUTLINE.value:
                outline_item.purpose = f"Section Outline of {outline_item.indexs}"
                outline_item.indexs = {}
                slide_content = (
                    "Overview of the Document:\n"
                    + self.source_doc.get_overview(include_summary=True)
                )
            elif outline_item.purpose == FunctionalLayouts.TOC.value:
                slide_content = "Table of Contents:\n" + self.toc
            else:
                slide_content = "This slide is a functional layout, please follow the slide description and content schema to generate the slide content."
            header, _, _, _ = outline_item.retrieve(slide_idx, self.source_doc)
            header += slide_desc
        else:
            logger.info("[pptgen:slide:select_layout:start] slide=%s", slide_idx)
            layout, header, slide_content = await self._select_layout(
                slide_idx, outline_item
            )
            logger.info(
                "[pptgen:slide:select_layout:done] slide=%s layout=%s header_chars=%s content_chars=%s",
                slide_idx,
                getattr(layout, "name", ""),
                len(header),
                len(slide_content),
            )
        try:
            logger.info("[pptgen:slide:content:start] slide=%s", slide_idx)
            command_list, template_id = await self._generate_content(
                layout, slide_content, header
            )
            logger.info(
                "[pptgen:slide:content:done] slide=%s template_id=%s commands=%s",
                slide_idx,
                template_id,
                len(command_list),
            )
            logger.info("[pptgen:slide:notes:start] slide=%s", slide_idx)
            notes = await self._generate_notes(slide_content, header)
            logger.info(
                "[pptgen:slide:notes:done] slide=%s notes_chars=%s",
                slide_idx,
                len(notes),
            )
            logger.info("[pptgen:slide:edit:start] slide=%s", slide_idx)
            slide, code_executor = await self._edit_slide(command_list, template_id, notes)
            logger.info("[pptgen:slide:edit:done] slide=%s", slide_idx)
            slide.slide_notes = notes
        except Exception as e:
            logger.error(f"Failed to generate slide {slide_idx}, error: {e}")
            traceback.print_exc()
            raise e
        logger.info("[pptgen:slide:done] slide=%s", slide_idx)
        return slide, code_executor

    @tenacity_decorator
    async def _select_layout(
        self, slide_idx: int, outline_item: OutlineItem
    ) -> tuple[Layout, str, str]:
        """
        Asynchronously select a layout for the slide.
        """
        header, content_source, images, videos = outline_item.retrieve(
            slide_idx, self.source_doc
        )
        logger.info(
            "[pptgen:layout:retrieve] slide=%s content_blocks=%s images=%s videos=%s",
            slide_idx,
            len(content_source),
            len(images),
            len(videos),
        )
        media_review_content = content_source or header
        logger.info("[pptgen:layout:media_review:start] slide=%s", slide_idx)
        images, videos, _ = await self._refine_slide_media_async(
            slide_idx,
            header,
            media_review_content,
            images,
            videos,
        )
        logger.info(
            "[pptgen:layout:media_review:done] slide=%s images=%s videos=%s",
            slide_idx,
            len(images),
            len(videos),
        )
        organized_content_source = self._augment_content_source_with_media_context(
            content_source,
            images,
            videos,
        )
        if len(organized_content_source) == 0:
            key_points = []
        else:
            logger.info("[pptgen:layout:content_organizer:start] slide=%s", slide_idx)
            _, key_points = await self.staffs["content_organizer"](
                content_source=organized_content_source
            )
            logger.info(
                "[pptgen:layout:content_organizer:done] slide=%s key_points=%s",
                slide_idx,
                len(key_points) if hasattr(key_points, "__len__") else "?",
            )
        slide_content_without_video = self._build_slide_content(key_points, images, [])
        logger.info("[pptgen:layout:video_usage_judge:start] slide=%s", slide_idx)
        use_video = bool(videos) and any(".gif" in video.lower() for video in videos)
        if videos and not use_video:
            use_video = bool(
                (
                    await self.staffs["video_usage_judge"](
                        outline=self.simple_outline,
                        slide_description=header,
                        slide_content=slide_content_without_video,
                        videos="\n".join(videos),
                    )
                )[1].get("use_video", False)
            )
        logger.info(
            "[pptgen:layout:video_usage_judge:done] slide=%s use_video=%s",
            slide_idx,
            use_video,
        )
        active_videos = videos if use_video else []
        slide_content = self._build_slide_content(key_points, images, active_videos)
        layouts = self.text_layouts
        has_visuals = len(images) > 0 or len(active_videos) > 0
        if has_visuals:
            layouts = self.multimodal_layouts

        logger.info("[pptgen:layout:layout_selector:start] slide=%s", slide_idx)
        _, layout_selection = await self.staffs["layout_selector"](
            outline=self.simple_outline,
            slide_description=header,
            slide_content=slide_content,
            available_layouts=layouts,
        )
        layout = self._select_best_layout_key(layout_selection["layout"], layouts)
        logger.info(
            "[pptgen:layout:layout_selector:done] slide=%s selected=%s",
            slide_idx,
            layout,
        )
        if "image" in layout and not has_visuals:
            logger.debug(
                f"An image layout: {layout} is selected, but no visual media are provided, please check the parsed document and outline item:\n {outline_item}"
            )
        elif "image" not in layout and has_visuals:
            logger.debug(
                f"A pure text layout: {layout} is selected, but visual media are provided, please check the parsed document and outline item:\n {outline_item}\n Set visual media to empty list."
            )
            media_start = slide_content.find("\nImages:\n")
            if media_start == -1:
                media_start = slide_content.find("\nVideos:\n")
            if media_start != -1:
                slide_content = slide_content[:media_start]
        return self.layouts[layout], header, slide_content

    async def _generate_content(
        self,
        layout: Layout,
        slide_content: str,
        slide_description: str,
    ) -> tuple[list, int]:
        """
        Synergize Agents to generate a slide.

        Args:
            layout (Layout): The layout data.
            slide_content (str): The slide content.
            slide_description (str): The description of the slide.

        Returns:
            tuple[list, int]: The generated command list and template id.
        """
        logger.info("[pptgen:editor:start] slide_description_chars=%s", len(slide_description))
        turn_id, editor_output = await self.staffs["editor"](
            outline=self.simple_outline,
            metadata=self.source_doc.metainfo,
            slide_description=slide_description,
            slide_content=slide_content,
            schema=layout.content_schema,
        )
        logger.info("[pptgen:editor:done] turn=%s", turn_id)
        command_list, template_id = await self._generate_commands(
            editor_output, layout, turn_id
        )
        return command_list, template_id

    async def _generate_notes(
        self,
        slide_content: str,
        slide_description: str,
    ) -> str:
        """
        Generate speaker notes for a slide.
        """
        mode = self._notes_mode()
        logger.info(
            "[pptgen:notes_generator:start] mode=%s slide_description_chars=%s",
            mode,
            len(slide_description),
        )
        if mode == "discussion":
            _, notes = await self.staffs["discussion_notes_generator"](
                slide_content=slide_content,
                slide_description=slide_description,
            )
            return str(notes).strip()
        _, notes = await self.staffs["notes_generator"](
            slide_content=slide_content,
            slide_description=slide_description,
        )
        return str(notes).strip()

    async def _edit_slide(
        self, command_list: list, template_id: int, notes: str
    ) -> tuple[SlidePage, CodeExecutor]:
        """
        Asynchronously edit the slide.
        """
        code_executor = CodeExecutor(self.retry_times)
        logger.info("[pptgen:coder:start] template_id=%s commands=%s", template_id, len(command_list))
        turn_id, edit_actions = await self.staffs["coder"](
            api_docs=code_executor.get_apis_docs(API_TYPES.Agent.value),
            edit_target=self.presentation.slides[template_id - 1].to_html(),
            command_list="\n".join([str(i) for i in command_list]),
        )
        logger.info("[pptgen:coder:done] turn=%s", turn_id)

        for error_idx in range(self.retry_times):
            edit_slide: SlidePage = deepcopy(self.presentation.slides[template_id - 1])
            feedback = code_executor.execute_actions(
                edit_actions, edit_slide, self.source_doc
            )
            if feedback is None:
                break
            logger.info(
                "Failed to generate slide, tried %d/%d times, error: %s",
                error_idx + 1,
                self.retry_times,
                str(feedback[1]),
            )
            if error_idx == self.retry_times:
                raise Exception(
                    f"Failed to generate slide, tried too many times at editing\ntraceback: {feedback[1]}"
                )
            edit_actions = await self.staffs["coder"].retry(
                feedback[0], feedback[1], turn_id, error_idx + 1
            )
        self._remove_template_residue(edit_slide)
        self.empty_prs.build_slide(edit_slide)
        return edit_slide, code_executor

    async def _generate_commands(
        self, editor_output: dict, layout: Layout, turn_id: int, retry: int = 0
    ):
        """
        Asynchronously generate commands for editing the slide content.

        Args:
            editor_output (dict): The editor output.
            layout (Layout): The layout object containing content schema.
            turn_id (int): The turn ID for retrying.
            retry (int, optional): The number of retries. Defaults to 0.

        Returns:
            list: A list of commands.

        Raises:
            Exception: If command generation fails.
        """
        command_list = []
        try:
            layout.validate(editor_output, self.source_doc.image_dir)
            if self.length_factor is not None:
                await layout.validate_length_async(
                    editor_output, self.length_factor, self.language_model
                )
            old_data = layout.get_old_data(editor_output)
            template_id = layout.get_slide_id(editor_output)
        except Exception as e:
            if retry < self.retry_times:
                new_output = await self.staffs["editor"].retry(
                    e,
                    traceback.format_exc(),
                    turn_id,
                    retry + 1,
                )
                return await self._generate_commands(
                    new_output, layout, turn_id, retry + 1
                )
            else:
                raise Exception(
                    f"Failed to generate commands, tried too many times at editing\ntraceback: {e}"
                )

        for el_name, old_content in old_data.items():
            if not isinstance(old_content, list):
                old_content = [old_content]

            new_content = editor_output.get(el_name, {"data": []})["data"]
            if not isinstance(new_content, list):
                new_content = [new_content]
            new_content = [i for i in new_content if i]
            quantity_change = len(new_content) - len(old_content)
            command_list.append(
                (
                    el_name,
                    layout[el_name].el_type,
                    f"quantity_change: {quantity_change}",
                    old_content,
                    new_content,
                )
            )

        assert len(command_list) > 0, "No commands generated"
        return command_list, template_id
