from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PRESENTAGENT_PYTHON = REPO_ROOT / ".venv-presentagent" / "Scripts" / "python.exe"


def run_subprocess(command: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}"
        )


def run_source_to_document(source_md: Path, output_dir: Path, env: dict[str, str]) -> None:
    command = [
        str(PRESENTAGENT_PYTHON),
        str(REPO_ROOT / "test" / "test_source_md_to_document.py"),
        "--source-md",
        str(source_md),
        "--output-dir",
        str(output_dir),
    ]
    run_subprocess(command, env)


def run_document_to_ppt(
    refined_doc: Path,
    image_dir: Path,
    output_dir: Path,
    template_pptx: Path,
    notes_mode: str,
    num_slides: int,
    env: dict[str, str],
) -> None:
    command = [
        str(PRESENTAGENT_PYTHON),
        str(REPO_ROOT / "test" / "test_document_to_ppt.py"),
        "--document-json",
        str(refined_doc),
        "--image-dir",
        str(image_dir),
        "--template-pptx",
        str(template_pptx),
        "--output-dir",
        str(output_dir),
        "--notes-modes",
        notes_mode,
        "--num-slides",
        str(num_slides),
    ]
    run_subprocess(command, env)


def build_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{REPO_ROOT};{REPO_ROOT / 'presentagent' / 'MegaTTS3'}"
    env["PATH"] = (
        f"{REPO_ROOT / '.venv-presentagent' / 'Scripts'};"
        f"{REPO_ROOT / 'tools' / 'ffmpeg' / 'bin'};"
        f"{REPO_ROOT};"
        f"{env.get('PATH', '')}"
    )
    env["PRESENTAGENT_DEEPRESEARCH_ROOT"] = ""
    env["PRESENTAGENT_ENABLE_DOCUMENT_MEDIA_RESEARCH"] = "0"
    return env


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resume ai_paper_slop discussion pipeline from existing source.md files."
    )
    parser.add_argument("--bundle-root", required=True)
    parser.add_argument("--template-pptx", required=True)
    parser.add_argument("--notes-mode", default="single_presentation")
    parser.add_argument("--num-slides", type=int, default=8)
    parser.add_argument("--force-document", action="store_true")
    parser.add_argument("--force-ppt", action="store_true")
    args = parser.parse_args()

    bundle_root = Path(args.bundle_root).resolve()
    template_pptx = Path(args.template_pptx).resolve()
    env = build_env()

    results: list[dict[str, str]] = []

    for item_dir in sorted(p for p in bundle_root.iterdir() if p.is_dir()):
        meta_path = item_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))

        source_md = item_dir / "url_to_source" / "source.md"
        refined_doc = item_dir / "source_to_document" / "refined_doc.json"
        ppt_dir = item_dir / "document_to_ppt"
        final_ppt = ppt_dir / args.notes_mode / f"final_{args.notes_mode}.pptx"

        if not source_md.exists():
            results.append(
                {
                    "slug": meta["slug"],
                    "status": "missing_source_md",
                    "source_md": str(source_md),
                }
            )
            continue

        try:
            if args.force_document or not refined_doc.exists():
                run_source_to_document(source_md, refined_doc.parent, env)
            if args.force_ppt or not final_ppt.exists():
                run_document_to_ppt(
                    refined_doc=refined_doc,
                    image_dir=source_md.parent,
                    output_dir=ppt_dir,
                    template_pptx=template_pptx,
                    notes_mode=args.notes_mode,
                    num_slides=args.num_slides,
                    env=env,
                )
            results.append(
                {
                    "slug": meta["slug"],
                    "status": "success",
                    "source_md": str(source_md),
                    "refined_doc": str(refined_doc),
                    "final_ppt": str(final_ppt),
                }
            )
        except Exception as exc:
            results.append(
                {
                    "slug": meta["slug"],
                    "status": "failed",
                    "source_md": str(source_md),
                    "error": str(exc),
                }
            )

        (bundle_root / "retry_from_source_summary.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(bundle_root / "retry_from_source_summary.json")


if __name__ == "__main__":
    main()
