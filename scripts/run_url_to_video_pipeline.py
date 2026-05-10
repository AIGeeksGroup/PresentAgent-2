from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pptagent.model_utils import ModelManager, parse_pdf
from pptagent.research.pdf_resolver import PdfResolver
from pptagent.research.live_runner import run_deepresearch_live


def run_subprocess(command: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {' '.join(command)}"
        )


def looks_like_pdf(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith(".pdf") or "/pdf/" in lowered or "arxiv.org/pdf/" in lowered


def resolve_pdf_to_source(url: str, output_dir: Path) -> Path:
    resolver = PdfResolver()
    result = resolver.resolve_to_pdf(url, str(output_dir), topic="")
    if not result.success or not result.local_path:
        raise RuntimeError(f"failed to resolve pdf from url: {url}; error={result.error}")
    models = ModelManager()
    parse_pdf(result.local_path, str(output_dir), models.marker_model)
    return output_dir / "source.md"


def run_html_to_source(url: str, output_dir: Path, env: dict[str, str], repo_root: Path) -> Path:
    command = [
        sys.executable,
        str(repo_root / "test" / "test_html_to_source_md.py"),
        "--url",
        url,
        "--output-dir",
        str(output_dir),
    ]
    run_subprocess(command, env)
    return output_dir / "source.md"


def run_source_to_document(source_md: Path, output_dir: Path, env: dict[str, str], repo_root: Path) -> Path:
    command = [
        sys.executable,
        str(repo_root / "test" / "test_source_md_to_document.py"),
        "--source-md",
        str(source_md),
        "--output-dir",
        str(output_dir),
    ]
    run_subprocess(command, env)
    return output_dir / "refined_doc.json"


def run_document_to_ppt(
    refined_doc: Path,
    image_dir: Path,
    output_dir: Path,
    env: dict[str, str],
    repo_root: Path,
    template_pptx: Path,
    notes_mode: str,
    num_slides: int,
) -> Path:
    command = [
        str(repo_root / ".venv-presentagent" / "Scripts" / "python.exe"),
        str(repo_root / "test" / "test_document_to_ppt.py"),
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
    return output_dir / notes_mode / f"final_{notes_mode}.pptx"


def run_ppt_to_video(pptx_path: Path, output_dir: Path, env: dict[str, str], repo_root: Path) -> Path:
    command = [
        str(repo_root / ".venv-presentagent" / "Scripts" / "python.exe"),
        str(repo_root / "test" / "test_ppt_to_video.py"),
        "--pptx",
        str(pptx_path),
        "--output-dir",
        str(output_dir),
    ]
    run_subprocess(command, env)
    return output_dir / "output.mp4"


def build_runtime_env(repo_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root};{repo_root / 'presentagent' / 'MegaTTS3'}"
    env["PATH"] = (
        f"{repo_root / '.venv-presentagent' / 'Scripts'};"
        f"{repo_root / 'tools' / 'ffmpeg' / 'bin'};"
        f"{repo_root};"
        f"{env.get('PATH', '')}"
    )
    env["PRESENTAGENT_DEEPRESEARCH_ROOT"] = env.get("PRESENTAGENT_DEEPRESEARCH_ROOT", "")
    env["PRESENTAGENT_ENABLE_DOCUMENT_MEDIA_RESEARCH"] = env.get(
        "PRESENTAGENT_ENABLE_DOCUMENT_MEDIA_RESEARCH",
        "0",
    )
    return env


def run_question_to_source_via_deepresearch(
    *,
    question: str,
    deepresearch_root: Path,
    output_dir: Path,
    report_path: Path,
    dataset_path: str,
    conda_env: str,
    conda_executable: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
) -> tuple[Path, str]:
    result = run_deepresearch_live(
        question=question,
        deepresearch_root=str(deepresearch_root),
        output_dir=str(output_dir),
        report_path=str(report_path),
        dataset_path=dataset_path or None,
        conda_env=conda_env,
        conda_executable=conda_executable,
        max_wait_seconds=max_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        progress_callback=print,
        terminate_runner_on_return=True,
    )
    if not result.success or not result.document_path:
        raise RuntimeError(f"DeepResearch top-3 html selection failed: {result.error}")
    return Path(result.document_path).resolve(), str(result.final_url or result.source_url or "")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run question/url -> source -> document -> ppt -> video in one script."
    )
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("--url", help="Input webpage or pdf url")
    input_group.add_argument("--question", help="User question used to launch DeepResearch and choose top-3 html candidates")
    parser.add_argument("--output-root", required=True, help="Root output directory")
    parser.add_argument(
        "--deepresearch-root",
        default="",
        help="Path to DeepResearch repository root. Required when --question is used.",
    )
    parser.add_argument(
        "--dataset-path",
        default="",
        help="Optional explicit DeepResearch dataset jsonl path.",
    )
    parser.add_argument(
        "--deepresearch-conda-env",
        default="",
        help="Optional conda env used to launch DeepResearch.",
    )
    parser.add_argument(
        "--deepresearch-conda-executable",
        default="conda",
        help="Conda executable used with --deepresearch-conda-env.",
    )
    parser.add_argument("--max-wait-seconds", type=float, default=900.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=30.0)
    parser.add_argument(
        "--template-pptx",
        default="",
        help="Template .pptx. Defaults to resource/templates/default_template.pptx",
    )
    parser.add_argument(
        "--notes-mode",
        default="single_presentation",
        help="Presentation mode, e.g. single_presentation or discussion",
    )
    parser.add_argument("--num-slides", type=int, default=8)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    repo_root = REPO_ROOT
    output_root = Path(args.output_root).resolve()
    template_pptx = (
        Path(args.template_pptx).resolve()
        if args.template_pptx
        else repo_root / "resource" / "templates" / "default_template.pptx"
    )
    if not template_pptx.exists():
        raise SystemExit(f"template pptx not found: {template_pptx}")

    url_to_source_dir = output_root / "url_to_source"
    source_to_document_dir = output_root / "source_to_document"
    document_to_ppt_dir = output_root / "document_to_ppt"
    ppt_to_video_dir = output_root / "ppt_to_video" / args.notes_mode
    for directory in (
        url_to_source_dir,
        source_to_document_dir,
        document_to_ppt_dir,
        ppt_to_video_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    source_md_path = url_to_source_dir / "source.md"
    refined_doc_path = source_to_document_dir / "refined_doc.json"
    final_ppt_path = document_to_ppt_dir / args.notes_mode / f"final_{args.notes_mode}.pptx"
    final_video_path = ppt_to_video_dir / "output.mp4"
    input_descriptor = args.url or args.question or ""

    if (
        args.skip_existing
        and source_md_path.exists()
        and refined_doc_path.exists()
        and final_ppt_path.exists()
        and final_video_path.exists()
    ):
        print(json.dumps(
            {
                "status": "skipped_existing",
                "input": input_descriptor,
                "source_md": str(source_md_path),
                "refined_doc": str(refined_doc_path),
                "final_ppt": str(final_ppt_path),
                "final_video": str(final_video_path),
            },
            ensure_ascii=False,
            indent=2,
        ))
        return

    env = build_runtime_env(repo_root)

    if args.question:
        if not args.deepresearch_root:
            raise SystemExit("--deepresearch-root is required when --question is used")
        source_md, selected_html_url = run_question_to_source_via_deepresearch(
            question=args.question,
            deepresearch_root=Path(args.deepresearch_root).resolve(),
            output_dir=url_to_source_dir,
            report_path=url_to_source_dir / "report.log",
            dataset_path=args.dataset_path,
            conda_env=args.deepresearch_conda_env,
            conda_executable=args.deepresearch_conda_executable,
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
        source_provenance = "deepresearch_top3_html"
    else:
        selected_html_url = args.url
        if looks_like_pdf(args.url):
            source_md = resolve_pdf_to_source(args.url, url_to_source_dir)
            source_provenance = "pdf_to_source"
        else:
            source_md = run_html_to_source(args.url, url_to_source_dir, env, repo_root)
            source_provenance = "html_to_source"

    refined_doc = run_source_to_document(source_md, source_to_document_dir, env, repo_root)
    final_ppt = run_document_to_ppt(
        refined_doc=refined_doc,
        image_dir=url_to_source_dir,
        output_dir=document_to_ppt_dir,
        env=env,
        repo_root=repo_root,
        template_pptx=template_pptx,
        notes_mode=args.notes_mode,
        num_slides=args.num_slides,
    )
    final_video = run_ppt_to_video(final_ppt, ppt_to_video_dir, env, repo_root)

    summary = {
        "status": "success",
        "input": input_descriptor,
        "selected_html_url": selected_html_url,
        "source_provenance": source_provenance,
        "notes_mode": args.notes_mode,
        "source_md": str(source_md),
        "refined_doc": str(refined_doc),
        "final_ppt": str(final_ppt),
        "final_video": str(final_video),
    }
    (output_root / "pipeline_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
