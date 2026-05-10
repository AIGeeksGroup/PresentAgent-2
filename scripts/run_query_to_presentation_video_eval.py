from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


OBJECTIVE_PROMPT_VERSION = "2026-05-06-strict-v2"


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except UnicodeError:
        return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(_read_text(path))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_id(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def _parse_json_response(raw: str) -> Any:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for left, right in (("{", "}"), ("[", "]")):
            start = raw.find(left)
            end = raw.rfind(right)
            if start != -1 and end != -1 and start < end:
                return json.loads(raw[start : end + 1])
        raise


def _flatten_slide_notes(path: Path) -> str:
    data = _read_json(path)
    if isinstance(data, list):
        lines = []
        for item in data:
            idx = item.get("slide_idx")
            notes = (item.get("notes") or "").strip()
            lines.append(f"Slide {idx}: {notes}")
        return "\n".join(lines)
    if isinstance(data, dict):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


def _read_transcript(path: Path) -> str:
    if path.suffix.lower() == ".json":
        return _flatten_slide_notes(path)
    return _read_text(path)


def _b64_image(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as img:
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        max_dim = 960
        if max(img.size) > max_dim:
            scale = max_dim / max(img.size)
            new_size = (max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale)))
            img = img.resize(new_size)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            temp_path = Path(tmp.name)
        try:
            img.save(temp_path, format="JPEG", quality=72, optimize=True)
            data = base64.b64encode(temp_path.read_bytes()).decode("ascii")
        finally:
            temp_path.unlink(missing_ok=True)
    return f"data:image/jpeg;base64,{data}"


def _which_ffmpeg(repo_root: Path) -> Path:
    local = repo_root / "tools" / "ffmpeg" / "bin" / "ffmpeg.exe"
    if local.exists():
        return local
    return Path("ffmpeg")


def _which_ffprobe(repo_root: Path) -> Path:
    local = repo_root / "tools" / "ffmpeg" / "bin" / "ffprobe.exe"
    if local.exists():
        return local
    return Path("ffprobe")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def _get_video_duration_seconds(ffprobe: Path, video_path: Path) -> float:
    cmd = [
        str(ffprobe),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {video_path}: {proc.stderr}")
    return max(float(proc.stdout.strip()), 1.0)


def _sample_reference_video_frames(
    repo_root: Path,
    video_path: Path,
    cache_dir: Path,
    max_frames: int = 6,
) -> list[Path]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(cache_dir.glob("frame_*.jpg"))
    if len(existing) >= max_frames:
        return existing[:max_frames]

    ffmpeg = _which_ffmpeg(repo_root)
    ffprobe = _which_ffprobe(repo_root)
    duration = _get_video_duration_seconds(ffprobe, video_path)
    frame_paths: list[Path] = []
    for index in range(max_frames):
        timestamp = duration * (index + 1) / (max_frames + 1)
        out_path = cache_dir / f"frame_{index:02d}.jpg"
        cmd = [
            str(ffmpeg),
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(out_path),
        ]
        proc = _run(cmd)
        if proc.returncode == 0 and out_path.exists():
            frame_paths.append(out_path)
    return frame_paths


def _sample_generated_video_frames(video_path: Path, max_frames: int = 6) -> list[Path]:
    notes_assets = video_path.parent / "notes_assets"
    frames = sorted(notes_assets.glob("frame_*.jpg"))
    if not frames:
        frames = sorted(notes_assets.glob("frame_*.png"))
    if len(frames) <= max_frames:
        return frames
    selected: list[Path] = []
    for i in range(max_frames):
        idx = round(i * (len(frames) - 1) / max(1, max_frames - 1))
        selected.append(frames[idx])
    deduped: list[Path] = []
    seen: set[Path] = set()
    for item in selected:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def _sample_retrieved_resource_images(
    resources_root: Path,
    repo_root: Path,
    cache_dir: Path,
    max_images: int = 4,
) -> list[Path]:
    candidates: list[Path] = []
    if resources_root.is_file():
        return []

    asset_dirs = []
    if (resources_root / "assets").exists():
        asset_dirs.append(resources_root / "assets")
    asset_dirs.append(resources_root)

    for base in asset_dirs:
        for suffix in ("*.png", "*.jpg", "*.jpeg"):
            candidates.extend(sorted(base.glob(suffix)))
        if candidates:
            break

    if candidates:
        return candidates[:max_images]

    video_candidates: list[Path] = []
    for base in asset_dirs:
        for suffix in ("*.mp4", "*.webm", "*.mov", "*.gif"):
            video_candidates.extend(sorted(base.glob(suffix)))
        if video_candidates:
            break
    if not video_candidates:
        return []

    images: list[Path] = []
    for idx, video_path in enumerate(video_candidates[: max(1, max_images)]):
        sampled = _sample_reference_video_frames(
            repo_root=repo_root,
            video_path=video_path,
            cache_dir=cache_dir / f"resource_video_{idx}",
            max_frames=1,
        )
        images.extend(sampled)
        if len(images) >= max_images:
            break
    return images[:max_images]


def _summarize_retrieved_resources(resources_root: Path) -> str:
    if not resources_root.exists():
        return f"Retrieved resources path does not exist: {resources_root}"

    if resources_root.is_file():
        text = _read_text(resources_root)
        return text[:4000]

    file_counts = {"images": 0, "videos": 0, "other": 0}
    names: list[str] = []
    for path in sorted(resources_root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".svg"}:
            file_counts["images"] += 1
        elif suffix in {".mp4", ".webm", ".mov"}:
            file_counts["videos"] += 1
        else:
            file_counts["other"] += 1
        rel = path.relative_to(resources_root).as_posix()
        if len(names) < 30:
            names.append(rel)

    summary = [
        f"Resources root: {resources_root}",
        f"Image-like files: {file_counts['images']}",
        f"Video-like files: {file_counts['videos']}",
        f"Other files: {file_counts['other']}",
        "Example resource files:",
        *[f"- {name}" for name in names],
    ]

    doc_overview = resources_root.parent / "source_to_document" / "document_overview.txt"
    if doc_overview.exists():
        summary.append("")
        summary.append("Document overview excerpt:")
        summary.append(_read_text(doc_overview)[:2500])

    return "\n".join(summary)


def _extract_single_title_from_query(query: str, sample_name: str) -> str:
    match = re.search(r"explaining\s+(.+?)(?:, focusing on|, covering|$)", query, re.IGNORECASE)
    return match.group(1).strip() if match else sample_name.replace("_", " ")


def _fallback_discussion_query(sample_name: str) -> str:
    title = sample_name.replace("_", " ")
    return (
        f"Create a roughly 6-minute two-speaker slide-based discussion video explaining {title}, "
        "focusing on the main mechanism and why it works, and use visual slides to clarify the key visual intuition, figure, or example."
    )


@dataclass
class BenchmarkExample:
    example_id: str
    mode: str
    sample_name: str
    user_query: str
    reference_video_path: str
    generated_video_path: str
    generated_transcript_path: str
    retrieved_resources_path: str


class EvalPrompts:
    def __init__(self, prompt_root: Path) -> None:
        self.quiz_generation = _read_text(prompt_root / "quiz_generation_prompt_zh.txt")
        self.quiz_answering = _read_text(prompt_root / "quiz_answering_prompt_zh.txt")
        self.subjective_wrapper = _read_text(prompt_root / "subjective_scoring_wrapper_zh.txt")
        self.scoring_prompts = _read_json(prompt_root / "scoring_prompts_zh.json")


class VLMClient:
    def __init__(self, model: str, api_key: str, api_base: str) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=api_key, base_url=api_base)
        self.model = model

    def complete_json(
        self,
        *,
        system_prompt: str,
        user_text: str,
        image_paths: list[Path],
        raw_output_path: Path,
        prompt_dump_path: Path,
        max_retries: int = 2,
    ) -> Any:
        image_plans: list[list[Path]] = []
        if image_paths:
            image_plans.append(image_paths)
            if len(image_paths) > 4:
                image_plans.append(image_paths[:4])
            if len(image_paths) > 2:
                image_plans.append(image_paths[:2])
            image_plans.append(image_paths[:1])
        image_plans.append([])

        seen_plan_keys: set[tuple[str, ...]] = set()
        last_raw = ""
        last_error: Exception | None = None

        for plan_index, current_images in enumerate(image_plans):
            plan_key = tuple(str(p) for p in current_images)
            if plan_key in seen_plan_keys:
                continue
            seen_plan_keys.add(plan_key)

            content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
            for image_path in current_images:
                content.append({"type": "image_url", "image_url": {"url": _b64_image(image_path)}})

            payload = {
                "model": self.model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
            }
            _write_text(
                prompt_dump_path.with_name(f"{prompt_dump_path.stem}_images{len(current_images)}{prompt_dump_path.suffix}"),
                json.dumps(payload, ensure_ascii=False, indent=2),
            )

            for attempt in range(max_retries):
                try:
                    response = self.client.chat.completions.create(**payload)
                except Exception as exc:
                    last_error = exc
                    if any(token in str(exc).lower() for token in ["connection error", "server disconnected", "remoteprotocolerror", "timeout"]):
                        time.sleep(min(10, 2 * (attempt + 1)))
                        continue
                    if "data_inspection_failed" in str(exc).lower() and current_images:
                        break
                    raise
                raw = response.choices[0].message.content or ""
                last_raw = raw
                _write_text(
                    raw_output_path.with_name(
                        f"{raw_output_path.stem}_images{len(current_images)}_attempt{attempt + 1}{raw_output_path.suffix}"
                    ),
                    raw,
                )
                try:
                    parsed = _parse_json_response(raw)
                    _write_text(raw_output_path, raw)
                    return parsed
                except Exception:
                    payload["messages"] = [
                        {"role": "system", "content": system_prompt},
                        {
                            "role": "user",
                            "content": content
                            + [
                                {
                                    "type": "text",
                                    "text": "上一次输出不是合法 JSON。请只返回合法 JSON，不要包含解释、代码块或额外文本。",
                                }
                            ],
                        },
                    ]
        _write_text(raw_output_path, last_raw)
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Model did not return valid JSON after {max_retries} attempts.")


def _load_single_instruction_map(instruction_bundle_root: Path) -> dict[str, str]:
    manifest_path = instruction_bundle_root / "instruction_manifest.json"
    if not manifest_path.exists():
        return {}
    data = _read_json(manifest_path)
    result: dict[str, str] = {}
    for item in data:
        paper_dir = item.get("paper_dir")
        instruction = item.get("instruction")
        if paper_dir and instruction:
            result[paper_dir] = instruction
    return result


def _find_single_reference_video(reference_root: Path, sample_name: str) -> Path | None:
    folder = reference_root / sample_name
    if not folder.exists():
        return None
    preferred = sorted(folder.glob("*_aac.mp4"))
    if preferred:
        return preferred[0]
    fallback = sorted(folder.glob("*.mp4"))
    return fallback[0] if fallback else None


def _load_discussion_reference_map(manifest_path: Path) -> dict[str, Path]:
    data = _read_json(manifest_path)
    result: dict[str, Path] = {}
    for item in data:
        name = item.get("name")
        dest = item.get("dest")
        status = item.get("status")
        if name and dest and status == "linked":
            result[name] = Path(dest)
    return result


def discover_single_examples(
    *,
    single_root: Path,
    single_reference_root: Path,
    instruction_bundle_root: Path,
    single_pipeline_bundle_root: Path,
) -> list[BenchmarkExample]:
    instruction_map = _load_single_instruction_map(instruction_bundle_root)
    examples: list[BenchmarkExample] = []
    for sample_dir in sorted([p for p in single_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        generated_video = sample_dir / "single_presentation_video" / "output.mp4"
        generated_transcript = sample_dir / "single_presentation_video" / "slide_notes.json"
        reference_video = _find_single_reference_video(single_reference_root, sample_dir.name)
        resources_root = single_pipeline_bundle_root / sample_dir.name / "url_to_source"
        user_query = instruction_map.get(sample_dir.name)
        if not user_query:
            title = sample_dir.name.replace("_", " ")
            user_query = f"Create a presentation video explaining {title}, covering the main idea, the method, and the key results."
        if not (generated_video.exists() and generated_transcript.exists() and reference_video and reference_video.exists()):
            continue
        if not resources_root.exists():
            resources_root = sample_dir
        examples.append(
            BenchmarkExample(
                example_id=f"single__{sample_dir.name}",
                mode="single",
                sample_name=sample_dir.name,
                user_query=user_query,
                reference_video_path=str(reference_video),
                generated_video_path=str(generated_video),
                generated_transcript_path=str(generated_transcript),
                retrieved_resources_path=str(resources_root),
            )
        )
    return examples


def discover_discussion_examples(
    *,
    discussion_root: Path,
    reference_manifest_path: Path,
) -> list[BenchmarkExample]:
    reference_map = _load_discussion_reference_map(reference_manifest_path)
    examples: list[BenchmarkExample] = []
    for sample_dir in sorted([p for p in discussion_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        generated_video = sample_dir / "discussion_video" / "output.mp4"
        generated_transcript = sample_dir / "discussion_video" / "slide_notes.json"
        instruction_path = sample_dir / "instruction.txt"
        meta_path = sample_dir / "meta.json"
        resources_root = sample_dir / "url_to_source"
        reference_video = reference_map.get(sample_dir.name)
        user_query = ""
        if instruction_path.exists():
            user_query = _read_text(instruction_path).strip()
        elif meta_path.exists():
            meta = _read_json(meta_path)
            user_query = str(meta.get("instruction", "")).strip()
        if not user_query:
            user_query = _fallback_discussion_query(sample_dir.name)
        if not (generated_video.exists() and generated_transcript.exists() and user_query and reference_video and reference_video.exists()):
            continue
        examples.append(
            BenchmarkExample(
                example_id=f"discussion__{sample_dir.name}",
                mode="discussion",
                sample_name=sample_dir.name,
                user_query=user_query,
                reference_video_path=str(reference_video),
                generated_video_path=str(generated_video),
                generated_transcript_path=str(generated_transcript),
                retrieved_resources_path=str(resources_root),
            )
        )
    return examples


def _quiz_generation_text(
    prompt_template: str,
    *,
    user_query: str,
    reference_video: str,
    reference_transcript: str,
) -> str:
    return (
        prompt_template.replace("{user_query}", user_query)
        .replace("{reference_video}", reference_video)
        .replace("{reference_transcript}", reference_transcript or "无")
    )


def _quiz_answering_text(
    prompt_template: str,
    *,
    user_query: str,
    generated_video: str,
    generated_transcript: str,
    quiz_questions: str,
) -> str:
    return (
        prompt_template.replace("{user_query}", user_query)
        .replace("{generated_video}", generated_video)
        .replace("{generated_transcript}", generated_transcript)
        .replace("{quiz_questions}", quiz_questions)
    )


def _subjective_text(
    prompt_template: str,
    *,
    user_query: str,
    mode: str,
    generated_video: str,
    generated_transcript: str,
    reference_video: str,
    retrieved_resources: str,
    metric: str,
    scoring_prompt: str,
) -> str:
    return (
        prompt_template.replace("{user_query}", user_query)
        .replace("{mode}", mode)
        .replace("{generated_video}", generated_video)
        .replace("{generated_transcript}", generated_transcript)
        .replace("{reference_video}", reference_video)
        .replace("{retrieved_resources}", retrieved_resources)
        .replace("{metric}", metric)
        .replace("{scoring_prompt}", scoring_prompt)
    )


def _normalize_mode_key(mode: str) -> str:
    return "single" if mode == "single" else "discussion"


def _score_quiz(questions: list[dict[str, Any]], answers: list[dict[str, Any]]) -> dict[str, Any]:
    answer_map = {item.get("question_id"): item.get("predicted_answer") for item in answers}
    scored: list[dict[str, Any]] = []
    total = 0
    for q in questions:
        qid = q.get("question_id")
        predicted = answer_map.get(qid)
        correct = q.get("correct_answer")
        is_correct = predicted == correct
        total += int(bool(is_correct))
        scored.append(
            {
                "question_id": qid,
                "predicted_answer": predicted,
                "correct_answer": correct,
                "is_correct": is_correct,
            }
        )
    return {
        "quiz_answers": scored,
        "quiz_score": total,
        "quiz_accuracy": round(total / max(1, len(questions)), 4),
    }


def run_evaluation(
    *,
    repo_root: Path,
    examples: list[BenchmarkExample],
    output_root: Path,
    prompts: EvalPrompts,
    client: VLMClient,
    max_video_frames: int,
    max_resource_images: int,
    rerun_objective_only: bool,
) -> None:
    outputs_root = output_root / "outputs"
    raw_root = outputs_root / "raw_responses"
    results: list[dict[str, Any]] = []

    for example in examples:
        example_raw_root = raw_root / example.example_id
        example_raw_root.mkdir(parents=True, exist_ok=True)
        quiz_question_path = outputs_root / "quiz_questions" / f"{example.example_id}.json"
        quiz_answer_path = outputs_root / "quiz_answers" / f"{example.example_id}.json"
        subjective_path = outputs_root / "subjective_scores" / f"{example.example_id}.json"

        if (
            quiz_question_path.exists()
            and quiz_answer_path.exists()
            and subjective_path.exists()
            and not rerun_objective_only
        ):
            quiz_existing = _read_json(quiz_answer_path)
            subjective_existing = _read_json(subjective_path)
            results.append(
                {
                    **asdict(example),
                    "quiz_score": quiz_existing.get("quiz_score", 0),
                    "quiz_accuracy": quiz_existing.get("quiz_accuracy", 0.0),
                    "average_subjective_score": subjective_existing.get("average_subjective_score", 0.0),
                    "subjective_scores": subjective_existing.get("subjective_scores", []),
                }
            )
            continue

        generated_transcript = _read_transcript(Path(example.generated_transcript_path))
        reference_transcript = ""
        generated_frames = _sample_generated_video_frames(Path(example.generated_video_path), max_video_frames)
        reference_frames = _sample_reference_video_frames(
            repo_root=repo_root,
            video_path=Path(example.reference_video_path),
            cache_dir=example_raw_root / "reference_frames",
            max_frames=max_video_frames,
        )
        resource_summary = _summarize_retrieved_resources(Path(example.retrieved_resources_path))
        resource_images = _sample_retrieved_resource_images(
            resources_root=Path(example.retrieved_resources_path),
            repo_root=repo_root,
            cache_dir=example_raw_root / "resource_frames",
            max_images=max_resource_images,
        )

        if example.mode == "single":
            reference_descriptor = f"Single-presentation reference for {_extract_single_title_from_query(example.user_query, example.sample_name)}"
        else:
            reference_descriptor = f"Discussion reference for {example.sample_name}"

        if rerun_objective_only and quiz_question_path.exists() and quiz_answer_path.exists():
            existing_q = _read_json(quiz_question_path)
            existing_a = _read_json(quiz_answer_path)
            if (
                existing_q.get("objective_prompt_version") == OBJECTIVE_PROMPT_VERSION
                and existing_a.get("objective_prompt_version") == OBJECTIVE_PROMPT_VERSION
                and subjective_path.exists()
            ):
                subjective_existing = _read_json(subjective_path)
                results.append(
                    {
                        **asdict(example),
                        "quiz_score": existing_a.get("quiz_score", 0),
                        "quiz_accuracy": existing_a.get("quiz_accuracy", 0.0),
                        "average_subjective_score": subjective_existing.get("average_subjective_score", 0.0),
                        "subjective_scores": subjective_existing.get("subjective_scores", []),
                    }
                )
                continue

        quiz_generation_text = _quiz_generation_text(
            prompts.quiz_generation,
            user_query=example.user_query,
            reference_video=reference_descriptor,
            reference_transcript=reference_transcript,
        )
        quiz_payload = client.complete_json(
            system_prompt="你是一个严谨的评测助手。按要求生成高质量测验题，并且只返回合法 JSON。",
            user_text=quiz_generation_text,
            image_paths=reference_frames,
            raw_output_path=example_raw_root / "quiz_generation_raw.txt",
            prompt_dump_path=example_raw_root / "quiz_generation_prompt.json",
        )
        questions = quiz_payload["questions"] if isinstance(quiz_payload, dict) else quiz_payload
        _write_json(
            quiz_question_path,
            {
                "example_id": example.example_id,
                "mode": example.mode,
                "objective_prompt_version": OBJECTIVE_PROMPT_VERSION,
                "questions": questions,
            },
        )

        quiz_answering_text = _quiz_answering_text(
            prompts.quiz_answering,
            user_query=example.user_query,
            generated_video=f"Generated {example.mode} presentation",
            generated_transcript=generated_transcript,
            quiz_questions=json.dumps(questions, ensure_ascii=False, indent=2),
        )
        answer_payload = client.complete_json(
            system_prompt="你是观看生成视频的观众。只能根据提供的生成视频证据和 transcript 作答，并且只返回合法 JSON。",
            user_text=quiz_answering_text,
            image_paths=generated_frames,
            raw_output_path=example_raw_root / "quiz_answering_raw.txt",
            prompt_dump_path=example_raw_root / "quiz_answering_prompt.json",
        )
        answers = answer_payload["answers"] if isinstance(answer_payload, dict) else answer_payload
        quiz_result = _score_quiz(questions, answers)
        _write_json(
            quiz_answer_path,
            {
                "example_id": example.example_id,
                "objective_prompt_version": OBJECTIVE_PROMPT_VERSION,
                **quiz_result,
            },
        )

        mode_key = _normalize_mode_key(example.mode)
        if rerun_objective_only and subjective_path.exists():
            subjective_existing = _read_json(subjective_path)
            subjective_scores = subjective_existing.get("subjective_scores", [])
            average_subjective_score = subjective_existing.get("average_subjective_score", 0.0)
        else:
            subjective_scores = []
            subjective_raw: list[dict[str, Any]] = []
            for metric, scoring_prompt in prompts.scoring_prompts[mode_key].items():
                subjective_text = _subjective_text(
                    prompts.subjective_wrapper,
                    user_query=example.user_query,
                    mode=example.mode,
                    generated_video=f"Generated {example.mode} presentation",
                    generated_transcript=generated_transcript,
                    reference_video=reference_descriptor,
                    retrieved_resources=resource_summary,
                    metric=metric,
                    scoring_prompt=scoring_prompt,
                )
                parsed = client.complete_json(
                    system_prompt="你是一个严谨的 presentation VLM judge。请只返回合法 JSON。",
                    user_text=subjective_text,
                    image_paths=generated_frames + reference_frames[: max(1, max_video_frames // 2)] + resource_images,
                    raw_output_path=example_raw_root / f"subjective_{_safe_id(metric)}_raw.txt",
                    prompt_dump_path=example_raw_root / f"subjective_{_safe_id(metric)}_prompt.json",
                )
                score = int(max(1, min(5, int(parsed["score"]))))
                reason = str(parsed.get("reason", "")).strip()
                subjective_scores.append({"metric": metric, "score": score, "reason": reason})
                subjective_raw.append(parsed)

            average_subjective_score = round(
                sum(item["score"] for item in subjective_scores) / max(1, len(subjective_scores)),
                4,
            )
            _write_json(
                subjective_path,
                {
                    "example_id": example.example_id,
                    "mode": example.mode,
                    "subjective_scores": subjective_scores,
                    "average_subjective_score": average_subjective_score,
                },
            )

        results.append(
            {
                **asdict(example),
                "quiz_score": quiz_result["quiz_score"],
                "quiz_accuracy": quiz_result["quiz_accuracy"],
                "average_subjective_score": average_subjective_score,
                "subjective_scores": subjective_scores,
            }
        )

    _write_json(outputs_root / "final_results.json", results)
    csv_path = outputs_root / "final_results.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "example_id",
                "mode",
                "sample_name",
                "user_query",
                "reference_video_path",
                "generated_video_path",
                "generated_transcript_path",
                "retrieved_resources_path",
                "quiz_score",
                "quiz_accuracy",
                "average_subjective_score",
                "subjective_scores",
            ],
        )
        writer.writeheader()
        for item in results:
            row = item.copy()
            row["subjective_scores"] = json.dumps(item["subjective_scores"], ensure_ascii=False)
            writer.writerow(row)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate query-to-presentation video benchmark for single and discussion modes.")
    parser.add_argument("--single-root", default=r"G:\PresentAgent\nips_evaluate\single presentation\presentation_top20_ppts_new")
    parser.add_argument("--single-reference-root", default=r"G:\PresentAgent\nips_evaluate\single presentation\single_presentation_benchmark_reference_videos")
    parser.add_argument("--single-instruction-bundle-root", default=r"G:\PresentAgent\paper_url_to_source_document\presentation_top20_instruction_bundle")
    parser.add_argument("--single-pipeline-bundle-root", default=r"G:\PresentAgent\paper_url_to_source_document\presentation_top20_pipeline_bundle")
    parser.add_argument("--discussion-root", default=r"G:\PresentAgent\nips_evaluate\discussion_new")
    parser.add_argument("--discussion-reference-manifest", default=r"G:\PresentAgent\nips_evaluate\discuss\reference_videos\reference_video_manifest.json")
    parser.add_argument("--prompt-root", default=r"G:\PresentAgent\nips_evaluate\eval_pipeline\prompts")
    parser.add_argument("--output-root", default=r"G:\PresentAgent\nips_evaluate\query_to_presentation_eval")
    parser.add_argument("--mode", choices=["all", "single", "discussion"], default="all")
    parser.add_argument("--example-id", default="", help="Run only one example_id.")
    parser.add_argument("--build-examples-only", action="store_true")
    parser.add_argument("--rerun-objective-only", action="store_true")
    parser.add_argument("--max-video-frames", type=int, default=6)
    parser.add_argument("--max-resource-images", type=int, default=4)
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY") or "")
    parser.add_argument("--api-base", default=os.getenv("API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"))
    parser.add_argument("--vlm-model", default=os.getenv("VISION_MODEL", "qwen3-vl-plus"))
    return parser


def main() -> None:
    parser = build_argument_parser()
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    prompts = EvalPrompts(Path(args.prompt_root))

    examples: list[BenchmarkExample] = []
    if args.mode in {"all", "single"}:
        examples.extend(
            discover_single_examples(
                single_root=Path(args.single_root),
                single_reference_root=Path(args.single_reference_root),
                instruction_bundle_root=Path(args.single_instruction_bundle_root),
                single_pipeline_bundle_root=Path(args.single_pipeline_bundle_root),
            )
        )
    if args.mode in {"all", "discussion"}:
        examples.extend(
            discover_discussion_examples(
                discussion_root=Path(args.discussion_root),
                reference_manifest_path=Path(args.discussion_reference_manifest),
            )
        )

    examples = sorted(examples, key=lambda x: x.example_id.lower())
    if args.example_id:
        examples = [item for item in examples if item.example_id == args.example_id]

    output_root = Path(args.output_root)
    _write_json(output_root / "outputs" / "benchmark_examples.json", [asdict(item) for item in examples])
    if args.build_examples_only:
        print(f"Discovered {len(examples)} examples.")
        return

    if not args.api_key:
        raise SystemExit("Missing API key. Provide --api-key or set OPENAI_API_KEY/API_KEY.")

    client = VLMClient(model=args.vlm_model, api_key=args.api_key, api_base=args.api_base)
    run_evaluation(
        repo_root=repo_root,
        examples=examples,
        output_root=output_root,
        prompts=prompts,
        client=client,
        max_video_frames=args.max_video_frames,
        max_resource_images=args.max_resource_images,
        rerun_objective_only=args.rerun_objective_only,
    )


if __name__ == "__main__":
    main()
