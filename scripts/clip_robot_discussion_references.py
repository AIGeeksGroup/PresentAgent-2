from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import ai_paper_slop.build_ai_paper_slop as build_mod
from ai_paper_slop.build_ai_paper_slop import (
    VideoRow,
    benchmark_entry,
    candidate_to_dict,
    clip_video,
    download_transcript,
    download_video,
    ensure_dir,
    extract_youtube_id,
    llm_rescore_candidates,
    parse_vtt_or_srt,
    rank_candidates,
    save_clean_transcript,
)


DEFAULT_SLUGS = [
    "DexWM",
    "EgoScale",
    "GenMimic",
    "GigaBrain_0_5M",
    "MultiWorld",
]


def load_meta(bundle_root: Path, slug: str) -> dict[str, Any]:
    meta_path = bundle_root / slug / "meta.json"
    return json.loads(meta_path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def flush_outputs(output_root: Path, selected_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]]) -> None:
    save_json(output_root / "selected_clips.json", selected_rows)
    save_csv(output_root / "selected_clips.csv", selected_rows)
    save_json(output_root / "candidate_segments.json", candidate_rows)


def build_row(meta: dict[str, Any]) -> VideoRow:
    return VideoRow(
        id=str(meta["id"]),
        topic=str(meta["topic"]),
        youtube_url=str(meta["youtube_url"]),
    )


def configure_external_tools(project_root: Path) -> None:
    bundled_yt_dlp = project_root / ".venv-presentagent" / "Scripts" / "yt-dlp.exe"
    if bundled_yt_dlp.exists():
        build_mod.yt_dlp_command = lambda: [str(bundled_yt_dlp)]


def maybe_reuse_existing_clip(
    slug: str,
    topic: str,
    instruction: str,
    project_root: Path,
    output_dir: Path,
) -> dict[str, Any] | None:
    if slug != "EgoScale":
        return None
    manifest_path = project_root / "ai_paper_slop" / "corrected_batch2" / "manifest.json"
    if not manifest_path.exists():
        return None
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    for item in payload:
        if str(item.get("topic")) != topic:
            continue
        src = Path(str(item["clip_path"]))
        if not src.exists():
            return None
        out_name = f"{slug}_discussion_reference.mp4"
        dst = output_dir / out_name
        if not dst.exists():
            shutil.copy2(src, dst)
        return {
            "slug": slug,
            "topic": topic,
            "source_video_url": item.get("source_video_url", ""),
            "clip_path": str(dst),
            "clip_start": item["clip_start"],
            "clip_end": item["clip_end"],
            "clip_duration_seconds": item["duration_seconds"],
            "instruction": instruction,
            "status": "success",
            "notes": "reused corrected EgoScale reference clip",
        }
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Clip discussion-style reference segments for robotics/video topics.")
    parser.add_argument("--bundle-root", default="robot_vla_worldmodel_batch15", help="Root containing per-sample meta.json files.")
    parser.add_argument("--output-root", default="", help="Directory to place clipped reference videos.")
    parser.add_argument("--slugs", nargs="*", default=DEFAULT_SLUGS, help="Sample slugs to process.")
    parser.add_argument("--reencode", action="store_true", help="Force ffmpeg re-encode instead of stream copy.")
    args = parser.parse_args()

    project_root = PROJECT_ROOT
    configure_external_tools(project_root)
    bundle_root = (project_root / args.bundle_root).resolve()
    output_root = (
        Path(args.output_root).resolve()
        if args.output_root
        else (bundle_root / "reference_discussion_clips").resolve()
    )
    raw_dir = output_root / "videos_raw"
    subtitles_dir = output_root / "transcripts_raw"
    clean_dir = output_root / "transcripts_clean"
    clips_dir = output_root / "clips"
    for path in [raw_dir, subtitles_dir, clean_dir, clips_dir]:
        ensure_dir(path)

    selected_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []

    for slug in args.slugs:
        meta = load_meta(bundle_root, slug)
        topic = str(meta["topic"])
        instruction = str(meta["instruction"])

        reused = maybe_reuse_existing_clip(slug, topic, instruction, project_root, clips_dir)
        if reused is not None:
            selected_rows.append(reused)
            flush_outputs(output_root, selected_rows, candidate_rows)
            continue

        row = build_row(meta)
        subtitle_path, subtitle_note = download_transcript(row, subtitles_dir)
        if subtitle_path is None:
            selected_rows.append(
                {
                    "slug": slug,
                    "topic": topic,
                    "source_video_url": row.youtube_url,
                    "clip_path": "",
                    "clip_start": "",
                    "clip_end": "",
                    "clip_duration_seconds": 0,
                    "instruction": instruction,
                    "status": "subtitle_failed",
                    "notes": subtitle_note,
                }
            )
            flush_outputs(output_root, selected_rows, candidate_rows)
            continue

        blocks = parse_vtt_or_srt(subtitle_path)
        if not blocks:
            selected_rows.append(
                {
                    "slug": slug,
                    "topic": topic,
                    "source_video_url": row.youtube_url,
                    "clip_path": "",
                    "clip_start": "",
                    "clip_end": "",
                    "clip_duration_seconds": 0,
                    "instruction": instruction,
                    "status": "empty_transcript",
                    "notes": subtitle_note,
                }
            )
            flush_outputs(output_root, selected_rows, candidate_rows)
            continue

        save_clean_transcript(blocks, clean_dir / f"{meta['id']}_{extract_youtube_id(row.youtube_url)}.txt")
        candidates = llm_rescore_candidates(row, rank_candidates(row, blocks))
        for cand in candidates:
            row_dict = candidate_to_dict(cand)
            row_dict["slug"] = slug
            row_dict["locked_instruction"] = instruction
            candidate_rows.append(row_dict)
        if not candidates:
            selected_rows.append(
                {
                    "slug": slug,
                    "topic": topic,
                    "source_video_url": row.youtube_url,
                    "clip_path": "",
                    "clip_start": "",
                    "clip_end": "",
                    "clip_duration_seconds": 0,
                    "instruction": instruction,
                    "status": "no_candidate",
                    "notes": "No transcript window scored into the target 5-7 minute range.",
                }
            )
            flush_outputs(output_root, selected_rows, candidate_rows)
            continue

        best = candidates[0]
        best.generated_instruction = instruction
        video_path, video_note = download_video(row, raw_dir)
        if video_path is None:
            selected_rows.append(
                {
                    "slug": slug,
                    "topic": topic,
                    "source_video_url": row.youtube_url,
                    "clip_path": "",
                    "clip_start": best.start_time,
                    "clip_end": best.end_time,
                    "clip_duration_seconds": best.duration_seconds,
                    "instruction": instruction,
                    "status": "video_failed",
                    "notes": video_note,
                }
            )
            flush_outputs(output_root, selected_rows, candidate_rows)
            continue

        clip_name = f"{slug}_discussion_reference.mp4"
        clip_path = clips_dir / clip_name
        ok, clip_note = clip_video(
            video_path,
            clip_path,
            start_time=best.start_time,
            end_time=best.end_time,
            reencode=args.reencode,
        )
        status = "success" if ok else "clip_failed"
        record = benchmark_entry(row, best, str(clip_path) if ok else "", status, f"{subtitle_note}; {video_note}; {clip_note}")
        record["slug"] = slug
        record["instruction"] = instruction
        selected_rows.append(record)
        flush_outputs(output_root, selected_rows, candidate_rows)

    flush_outputs(output_root, selected_rows, candidate_rows)
    print(output_root / "selected_clips.json")


if __name__ == "__main__":
    main()
