from __future__ import annotations

import argparse
import base64
import csv
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _parse_json_block(raw: str) -> Any:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        left = raw.find("{")
        right = raw.rfind("}")
        if left != -1 and right != -1 and left < right:
            return json.loads(raw[left : right + 1])
        left = raw.find("[")
        right = raw.rfind("]")
        if left != -1 and right != -1 and left < right:
            return json.loads(raw[left : right + 1])
        raise


def _b64_image(path: Path) -> str:
    mime = "image/jpeg"
    suffix = path.suffix.lower()
    if suffix == ".png":
        mime = "image/png"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def _sample_slide_frames(notes_assets_dir: Path, max_images: int = 6) -> list[Path]:
    frames = sorted(notes_assets_dir.glob("frame_*.jpg"))
    if not frames:
        frames = sorted(notes_assets_dir.glob("frame_*.png"))
    if len(frames) <= max_images:
        return frames
    chosen: list[Path] = []
    for i in range(max_images):
        idx = round(i * (len(frames) - 1) / max(1, max_images - 1))
        chosen.append(frames[idx])
    deduped: list[Path] = []
    seen: set[Path] = set()
    for item in chosen:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _flatten_notes(slide_notes: list[dict[str, Any]]) -> str:
    lines = []
    for item in slide_notes:
        idx = item.get("slide_idx")
        notes = (item.get("notes") or "").strip()
        lines.append(f"Slide {idx}: {notes}")
    return "\n".join(lines)


def _clip_score(value: Any, default: float = 0.0) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = default
    return max(0.0, min(5.0, score))


def _extract_source_context(refined_doc: dict[str, Any], max_chars: int = 7000) -> str:
    lines: list[str] = []
    for section in refined_doc.get("sections", []):
        title = section.get("title", "")
        summary = section.get("summary", "")
        if title:
            lines.append(f"Section: {title}")
        if summary:
            lines.append(f"Summary: {summary}")
        for subsection in section.get("subsections", [])[:3]:
            st = subsection.get("title", "")
            if st:
                lines.append(f"Subsection: {st}")
            content = (subsection.get("content") or "").strip()
            if content:
                lines.append(content[:800])
        text = "\n".join(lines)
        if len(text) >= max_chars:
            return text[:max_chars]
    return "\n".join(lines)[:max_chars]


def _collect_media_stats(refined_doc: dict[str, Any]) -> dict[str, int]:
    counts = {"image": 0, "video": 0, "gif": 0, "other": 0}
    for section in refined_doc.get("sections", []):
        for subsection in section.get("subsections", []):
            for media in subsection.get("medias", []):
                media_type = (media.get("media_type") or "").lower()
                path = (media.get("path") or "").lower()
                if path.endswith(".gif"):
                    counts["gif"] += 1
                elif media_type == "image":
                    counts["image"] += 1
                elif media_type == "video":
                    counts["video"] += 1
                else:
                    counts["other"] += 1
    return counts


@dataclass
class Sample:
    paper_dir: str
    ppt_path: Path
    video_path: Path | None
    notes_path: Path | None
    notes_assets_dir: Path | None
    source_md_path: Path
    refined_doc_path: Path


class JudgeRunner:
    def __init__(self, api_key: str, api_base: str, text_model: str, vl_model: str) -> None:
        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.text_model = text_model
        self.vl_model = vl_model

    def chat_text(self, prompt: str, model: str | None = None) -> str:
        response = self.client.chat.completions.create(
            model=model or self.text_model,
            temperature=0,
            messages=[
                {"role": "system", "content": "Return concise, valid JSON when requested."},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content or ""

    def chat_vl(self, text_prompt: str, image_paths: list[Path]) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": text_prompt}]
        for image_path in image_paths:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": _b64_image(image_path)},
                }
            )
        response = self.client.chat.completions.create(
            model=self.vl_model,
            temperature=0,
            messages=[
                {"role": "system", "content": "Judge the presentation carefully and return concise valid JSON."},
                {"role": "user", "content": content},
            ],
        )
        return response.choices[0].message.content or ""


def discover_samples(ppt_root: Path, bundle_root: Path) -> list[Sample]:
    samples: list[Sample] = []
    for paper_dir in sorted([p for p in ppt_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        ppt_path = paper_dir / "single_presentation" / "final_single_presentation.pptx"
        video_path = paper_dir / "single_presentation_video" / "output.mp4"
        notes_path = paper_dir / "single_presentation_video" / "slide_notes.json"
        notes_assets_dir = paper_dir / "single_presentation_video" / "notes_assets"
        bundle_dir = bundle_root / paper_dir.name
        source_md_path = bundle_dir / "url_to_source" / "source.md"
        refined_doc_path = bundle_dir / "source_to_document" / "refined_doc.json"
        if not (ppt_path.exists() and source_md_path.exists() and refined_doc_path.exists()):
            continue
        samples.append(
            Sample(
                paper_dir=paper_dir.name,
                ppt_path=ppt_path,
                video_path=video_path if video_path.exists() else None,
                notes_path=notes_path if notes_path.exists() else None,
                notes_assets_dir=notes_assets_dir if notes_assets_dir.exists() else None,
                source_md_path=source_md_path,
                refined_doc_path=refined_doc_path,
            )
        )
    return samples


def generate_quiz(runner: JudgeRunner, sample: Sample, refined_doc: dict[str, Any], notes_text: str) -> list[dict[str, Any]]:
    source_context = _extract_source_context(refined_doc, max_chars=6000)
    prompt = f"""
You are designing PresentEval-style evaluation questions for a single-speaker academic presentation video.

Create exactly 5 multiple-choice questions based on the source content and the generated presentation notes.
Each question must test one important idea:
- topic recognition
- structural understanding
- core mechanism, comparison, or limitation
- key result or capability
- takeaway

Requirements:
- Questions should be answerable from a good presentation of the source material.
- Avoid trivial wording-match questions.
- Include exactly 4 options per question.
- Only one option may be correct.

Return JSON as a list with this schema:
[
  {{
    "question": "...",
    "options": {{"A": "...", "B": "...", "C": "...", "D": "..."}},
    "answer": "A",
    "rationale": "..."
  }}
]

Paper/sample name: {sample.paper_dir}

Source context:
{source_context}

Presentation notes:
{notes_text[:6000]}
"""
    raw = runner.chat_text(prompt)
    data = _parse_json_block(raw)
    if not isinstance(data, list):
        raise RuntimeError(f"Quiz generation failed for {sample.paper_dir}: not a list")
    return data[:5]


def judge_presentation_quality(
    runner: JudgeRunner,
    sample: Sample,
    refined_doc: dict[str, Any],
    notes_text: str,
    image_paths: list[Path],
) -> dict[str, Any]:
    source_context = _extract_source_context(refined_doc, max_chars=4500)
    prompt = f"""
Evaluate this generated presentation using a PresentEval-style subjective scoring rubric.

Score each item from 1 to 5:
- content_quality_score
- visual_quality_score
- comprehension_clarity_score

Definitions:
- content_quality_score: whether the presentation faithfully covers the important source content, stays accurate, and highlights the key ideas rather than missing or distorting them.
- visual_quality_score: whether the slides are visually clear, well laid out, readable, and presentation-like rather than cluttered or confusing.
- comprehension_clarity_score: whether a viewer can follow the explanation easily, with coherent narration and a clear presentation flow.

Scoring rubric:
- 5: excellent, with only minor weaknesses if any.
- 4: strong overall, but with some noticeable issues.
- 3: adequate but clearly uneven or partially weak.
- 2: poor, with major problems that hurt the presentation.
- 1: very poor, confusing, or largely ineffective.

Return JSON:
{{
  "content_quality_score": 0,
  "visual_quality_score": 0,
  "comprehension_clarity_score": 0,
  "subjective_score": 0,
  "presentation_quality_score": 0,
  "justification": "short justification"
}}

Sample: {sample.paper_dir}

Source context:
{source_context}

Presentation notes:
{notes_text[:5000]}

The attached images are sampled slide frames from the generated presentation.
"""
    raw = runner.chat_vl(prompt, image_paths)
    return _parse_json_block(raw)


def judge_multimodal_search_quality(
    runner: JudgeRunner,
    sample: Sample,
    refined_doc: dict[str, Any],
    notes_text: str,
    image_paths: list[Path],
) -> dict[str, Any]:
    source_context = _extract_source_context(refined_doc, max_chars=4500)
    media_stats = _collect_media_stats(refined_doc)
    prompt = f"""
Evaluate the multimodal search quality of this generated presentation.

The system used external multimodal resources from the source document.
Score each item from 1 to 5:
- media_relevance_score
- media_helpfulness_score
- media_diversity_score
- media_integration_score

Definitions:
- media_relevance_score: how relevant the selected media are to the actual topic.
- media_helpfulness_score: whether the media genuinely help explain the key ideas.
- media_diversity_score: whether the media provide complementary evidence instead of repetitive visuals.
- media_integration_score: whether the media are well integrated into the presentation storyline and slides.

Scoring rubric:
- 5: excellent and consistently helpful.
- 4: strong overall with some weakness.
- 3: mixed quality, partially helpful.
- 2: weak, often unhelpful or repetitive.
- 1: poor, irrelevant, or badly integrated.

Return JSON:
{{
  "media_relevance_score": 0,
  "media_helpfulness_score": 0,
  "media_diversity_score": 0,
  "media_integration_score": 0,
  "multimodal_search_quality_score": 0,
  "justification": "short justification"
}}

Sample: {sample.paper_dir}
Media stats from refined_doc: {json.dumps(media_stats)}

Source context:
{source_context}

Presentation notes:
{notes_text[:5000]}

The attached images are sampled slide frames from the generated presentation.
"""
    raw = runner.chat_vl(prompt, image_paths)
    return _parse_json_block(raw)


def judge_objective_comprehension(
    runner: JudgeRunner,
    sample: Sample,
    quiz: list[dict[str, Any]],
    notes_text: str,
    image_paths: list[Path],
) -> dict[str, Any]:
    quiz_block = json.dumps(quiz, ensure_ascii=False, indent=2)
    prompt = f"""
Run objective quiz evaluation for this presentation.
Answer the following 5 multiple-choice questions using only the presentation content.
Use the provided speaking notes and sampled slide frames as the presentation evidence.

Return JSON:
{{
  "answers": [
    {{"index": 1, "predicted": "A", "correct": "B", "is_correct": false}},
    {{"index": 2, "predicted": "A", "correct": "A", "is_correct": true}}
  ],
  "quiz_score_raw": 0,
  "quiz_score_norm": 0.0,
  "justification": "short justification"
}}

Sample: {sample.paper_dir}

Questions:
{quiz_block}

Presentation notes:
{notes_text[:5000]}
"""
    raw = runner.chat_vl(prompt, image_paths)
    return _parse_json_block(raw)


def compute_overall(result: dict[str, Any]) -> float:
    quiz = float(result.get("quiz_score_norm", 0))
    subjective = _clip_score(result.get("subjective_score", result.get("presentation_quality_score", 0))) / 5.0
    mm = _clip_score(result.get("multimodal_search_quality_score", 0)) / 5.0
    return round(0.4 * quiz + 0.4 * subjective + 0.2 * mm, 4)


def finalize_subjective_scores(result: dict[str, Any]) -> dict[str, Any]:
    content = _clip_score(result.get("content_quality_score"))
    visual = _clip_score(result.get("visual_quality_score"))
    comp = _clip_score(result.get("comprehension_clarity_score"))
    subjective = round((content + visual + comp) / 3.0, 4)
    result["content_quality_score"] = content
    result["visual_quality_score"] = visual
    result["comprehension_clarity_score"] = comp
    result["subjective_score"] = subjective
    result["presentation_quality_score"] = subjective
    return result


def finalize_multimodal_scores(result: dict[str, Any]) -> dict[str, Any]:
    relevance = _clip_score(result.get("media_relevance_score"))
    helpfulness = _clip_score(result.get("media_helpfulness_score"))
    diversity = _clip_score(result.get("media_diversity_score"))
    integration = _clip_score(result.get("media_integration_score"))
    mm_score = round((relevance + helpfulness + diversity + integration) / 4.0, 4)
    result["media_relevance_score"] = relevance
    result["media_helpfulness_score"] = helpfulness
    result["media_diversity_score"] = diversity
    result["media_integration_score"] = integration
    result["multimodal_search_quality_score"] = mm_score
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run single presentation evaluation on generated presentation videos.")
    parser.add_argument("--ppt-root", required=True)
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--api-base", default=os.getenv("API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY"))
    parser.add_argument("--text-model", default=os.getenv("LANGUAGE_MODEL", "qwen3.5-plus"))
    parser.add_argument("--vl-model", default=os.getenv("VISION_MODEL", "qwen3-vl-plus"))
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    if not args.api_key:
        raise SystemExit("Missing API key. Pass --api-key or set OPENAI_API_KEY/API_KEY.")

    ppt_root = Path(args.ppt_root).resolve()
    bundle_root = Path(args.bundle_root).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    samples = discover_samples(ppt_root, bundle_root)
    if args.limit > 0:
        samples = samples[: args.limit]

    runner = JudgeRunner(
        api_key=args.api_key,
        api_base=args.api_base,
        text_model=args.text_model,
        vl_model=args.vl_model,
    )

    manifest: list[dict[str, Any]] = []
    quizzes: dict[str, Any] = {}
    results: list[dict[str, Any]] = []

    for sample in samples:
        refined_doc = _read_json(sample.refined_doc_path)
        notes_data = _read_json(sample.notes_path) if sample.notes_path else []
        notes_text = _flatten_notes(notes_data) if notes_data else ""
        image_paths = _sample_slide_frames(sample.notes_assets_dir) if sample.notes_assets_dir else []

        manifest.append(
            {
                "paper_dir": sample.paper_dir,
                "ppt_path": str(sample.ppt_path),
                "video_path": str(sample.video_path) if sample.video_path else "",
                "notes_path": str(sample.notes_path) if sample.notes_path else "",
                "source_md_path": str(sample.source_md_path),
                "refined_doc_path": str(sample.refined_doc_path),
                "sampled_slide_images": [str(p) for p in image_paths],
            }
        )

        if not (sample.video_path and sample.notes_path and sample.notes_assets_dir):
            results.append(
                {
                    "paper_dir": sample.paper_dir,
                    "status": "video_missing",
                }
            )
            continue

        quiz = generate_quiz(runner, sample, refined_doc, notes_text)
        quizzes[sample.paper_dir] = quiz

        presentation = finalize_subjective_scores(
            judge_presentation_quality(runner, sample, refined_doc, notes_text, image_paths)
        )
        multimodal = finalize_multimodal_scores(
            judge_multimodal_search_quality(runner, sample, refined_doc, notes_text, image_paths)
        )
        objective = judge_objective_comprehension(runner, sample, quiz, notes_text, image_paths)

        merged = {
            "paper_dir": sample.paper_dir,
            "status": "success",
            "evaluation_protocol": "present_eval_plus_multimodal_search",
            **presentation,
            **multimodal,
            **objective,
        }
        merged["overall_score"] = compute_overall(merged)
        results.append(merged)

        _write_json(output_dir / f"{sample.paper_dir}__eval.json", merged)

    _write_json(output_dir / "single_presentation_eval_manifest.json", manifest)
    _write_json(output_dir / "single_presentation_quiz.json", quizzes)
    _write_json(output_dir / "single_presentation_results.json", results)

    if results:
        fieldnames: list[str] = sorted({key for item in results for key in item.keys()})
        with (output_dir / "single_presentation_results.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in results:
                writer.writerow(row)


if __name__ == "__main__":
    main()
