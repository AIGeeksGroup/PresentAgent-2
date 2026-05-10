from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def find_samples(batch_root: Path) -> list[tuple[str, Path, Path]]:
    samples: list[tuple[str, Path, Path]] = []
    for item_dir in sorted(p for p in batch_root.iterdir() if p.is_dir()):
        nested_ppt = item_dir / "document_to_ppt" / "single_presentation" / "final_single_presentation.pptx"
        flat_ppt = item_dir / "final_single_presentation.pptx"
        out_dir = item_dir / "single_presentation_video"
        if nested_ppt.exists():
            samples.append((item_dir.name, nested_ppt, out_dir))
        elif flat_ppt.exists():
            samples.append((item_dir.name, flat_ppt, out_dir))
    return samples


def run_one(py: Path, repo_root: Path, ppt_path: Path, out_dir: Path) -> None:
    cmd = [
        str(py),
        str(repo_root / "test" / "test_ppt_to_video.py"),
        "--pptx",
        str(ppt_path),
        "--output-dir",
        str(out_dir),
    ]
    completed = subprocess.run(cmd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(cmd)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch render generated PPTs into narrated MP4 videos.")
    parser.add_argument("--batch-root", required=True, help="Root directory containing per-sample subfolders.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python executable used to run test/test_ppt_to_video.py",
    )
    parser.add_argument("--skip-existing", action="store_true", help="Skip samples that already have output.mp4")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    batch_root = Path(args.batch_root).resolve()
    py = Path(args.python).resolve()

    summary: list[dict[str, str]] = []
    samples = find_samples(batch_root)
    if not samples:
        (batch_root / "ppt_to_video_summary.json").write_text(
            json.dumps(
                [
                    {
                        "status": "no_samples_found",
                        "batch_root": str(batch_root),
                        "expected_ppt_names": [
                            "document_to_ppt/single_presentation/final_single_presentation.pptx",
                            "final_single_presentation.pptx",
                        ],
                    }
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(batch_root / "ppt_to_video_summary.json")
        return

    for name, ppt_path, out_dir in samples:
        video_path = out_dir / "output.mp4"
        notes_path = out_dir / "slide_notes.json"
        try:
            if args.skip_existing and video_path.exists():
                summary.append(
                    {
                        "sample": name,
                        "status": "skipped_existing",
                        "pptx": str(ppt_path),
                        "video": str(video_path),
                    }
                )
                continue

            out_dir.mkdir(parents=True, exist_ok=True)
            run_one(py, repo_root, ppt_path, out_dir)
            summary.append(
                {
                    "sample": name,
                    "status": "success",
                    "pptx": str(ppt_path),
                    "video": str(video_path),
                    "notes": str(notes_path),
                }
            )
        except Exception as exc:
            summary.append(
                {
                    "sample": name,
                    "status": "failed",
                    "pptx": str(ppt_path),
                    "error": str(exc),
                }
            )

        (batch_root / "ppt_to_video_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    (batch_root / "ppt_to_video_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(batch_root / "ppt_to_video_summary.json")


if __name__ == "__main__":
    main()
