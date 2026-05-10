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


def run_source_to_document(source_md: Path, output_dir: Path, env: dict[str, str], repo_root: Path) -> None:
    command = [
        sys.executable,
        str(repo_root / "test" / "test_source_md_to_document.py"),
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
    env: dict[str, str],
    repo_root: Path,
    template_pptx: Path,
    notes_mode: str,
    num_slides: int,
) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run url->source->document->ppt for ai_paper_slop discussion samples."
    )
    parser.add_argument("--bundle-root", required=True, help="Directory containing per-topic folders with meta.json")
    parser.add_argument(
        "--template-pptx",
        default="",
        help="Optional path to the template pptx. Defaults to resource/templates/default_template.pptx",
    )
    parser.add_argument(
        "--notes-mode",
        default="single_presentation",
        help="Notes mode passed to test_document_to_ppt.py",
    )
    parser.add_argument("--num-slides", type=int, default=8)
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    bundle_root = Path(args.bundle_root).resolve()
    template_pptx = (
        Path(args.template_pptx).resolve()
        if args.template_pptx
        else repo_root / "resource" / "templates" / "default_template.pptx"
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = f"{repo_root};{repo_root / 'presentagent' / 'MegaTTS3'}"
    env["PATH"] = (
        f"{repo_root / '.venv-presentagent' / 'Scripts'};"
        f"{repo_root / 'tools' / 'ffmpeg' / 'bin'};"
        f"{repo_root};"
        f"{env.get('PATH', '')}"
    )
    env["PRESENTAGENT_DEEPRESEARCH_ROOT"] = ""
    env["PRESENTAGENT_ENABLE_DOCUMENT_MEDIA_RESEARCH"] = "0"

    summaries: list[dict[str, str]] = []

    for item_dir in sorted(p for p in bundle_root.iterdir() if p.is_dir()):
        meta_path = item_dir / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8-sig"))
        url = meta["normalized_source_url"]
        url_to_source_dir = item_dir / "url_to_source"
        source_to_document_dir = item_dir / "source_to_document"
        document_to_ppt_dir = item_dir / "document_to_ppt"
        url_to_source_dir.mkdir(parents=True, exist_ok=True)
        source_to_document_dir.mkdir(parents=True, exist_ok=True)
        document_to_ppt_dir.mkdir(parents=True, exist_ok=True)

        source_md_path = url_to_source_dir / "source.md"
        refined_doc_path = source_to_document_dir / "refined_doc.json"
        final_ppt_path = document_to_ppt_dir / args.notes_mode / f"final_{args.notes_mode}.pptx"

        if args.skip_existing and source_md_path.exists() and refined_doc_path.exists() and final_ppt_path.exists():
            summaries.append(
                {
                    "slug": meta["slug"],
                    "status": "skipped_existing",
                    "source_md": str(source_md_path),
                    "refined_doc": str(refined_doc_path),
                    "final_ppt": str(final_ppt_path),
                }
            )
            continue

        try:
            if looks_like_pdf(url):
                source_md = resolve_pdf_to_source(url, url_to_source_dir)
                source_provenance = "pdf_to_source"
            else:
                source_md = run_html_to_source(url, url_to_source_dir, env, repo_root)
                source_provenance = "html_to_source"

            run_source_to_document(source_md, source_to_document_dir, env, repo_root)
            run_document_to_ppt(
                refined_doc=refined_doc_path,
                image_dir=url_to_source_dir,
                output_dir=document_to_ppt_dir,
                env=env,
                repo_root=repo_root,
                template_pptx=template_pptx,
                notes_mode=args.notes_mode,
                num_slides=args.num_slides,
            )

            summaries.append(
                {
                    "slug": meta["slug"],
                    "status": "success",
                    "url": url,
                    "source_provenance": source_provenance,
                    "source_md": str(source_md_path),
                    "refined_doc": str(refined_doc_path),
                    "final_ppt": str(final_ppt_path),
                }
            )
        except Exception as exc:
            summaries.append(
                {
                    "slug": meta["slug"],
                    "status": "failed",
                    "url": url,
                    "error": str(exc),
                }
            )

        (bundle_root / "url_to_document_ppt_summary.json").write_text(
            json.dumps(summaries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    print(bundle_root / "url_to_document_ppt_summary.json")


if __name__ == "__main__":
    main()
