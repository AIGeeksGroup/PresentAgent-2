from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from presentagent import backend


def _find_repo_root(script_path: Path) -> Path:
    current = script_path.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "pptagent").is_dir():
            return candidate
    raise RuntimeError(
        f"Could not locate repository root from {script_path}; expected a parent directory containing 'pptagent/'."
    )


ROOT = _find_repo_root(Path(__file__))
DEFAULT_TEMPLATE_PPTX = ROOT / "resource" / "templates" / "default_template.pptx"


def _copytree_contents(source_dir: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    for item in source_dir.iterdir():
        destination = target_dir / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


async def _run_backend_ppt_gen(
    *,
    document_json_path: Path,
    image_dir: Path,
    template_pptx_path: Path,
    output_dir: Path,
    num_slides: int,
    notes_mode: str,
) -> None:
    template_blob = template_pptx_path.read_bytes()
    pptx_id = hashlib.md5(template_blob).hexdigest()
    doc_id = hashlib.md5(str(document_json_path.resolve()).encode("utf-8")).hexdigest()
    task_id = f"debug_document_to_ppt/{int(time.time())}"

    pptx_dir = Path(backend.RUNS_DIR) / "pptx" / pptx_id
    pptx_dir.mkdir(parents=True, exist_ok=True)
    (pptx_dir / "source.pptx").write_bytes(template_blob)

    parsed_doc_dir = Path(backend.RUNS_DIR) / "pdf" / doc_id
    parsed_doc_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(document_json_path, parsed_doc_dir / "refined_doc.json")
    (parsed_doc_dir / "source.md").write_text(
        "# Debug document\n\nThis source.md placeholder is only used to enter the backend document mode.\n",
        encoding="utf-8",
    )
    if image_dir.exists():
        _copytree_contents(image_dir, parsed_doc_dir)

    run_dir = Path(backend.RUNS_DIR) / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "numberOfPages": num_slides,
        "pptx": pptx_id,
        "pdf": doc_id,
    }
    (run_dir / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    backend.progress_store[task_id] = task
    backend.active_connections[task_id] = None
    previous_slide_media_output_dir = os.environ.get(
        "PRESENTAGENT_SLIDE_MEDIA_OUTPUT_DIR"
    )
    previous_notes_mode = os.environ.get("PRESENTAGENT_NOTES_MODE")
    os.environ["PRESENTAGENT_SLIDE_MEDIA_OUTPUT_DIR"] = str(output_dir)
    os.environ["PRESENTAGENT_NOTES_MODE"] = notes_mode
    try:
        await backend.ppt_gen(task_id)
    finally:
        if previous_slide_media_output_dir is None:
            os.environ.pop("PRESENTAGENT_SLIDE_MEDIA_OUTPUT_DIR", None)
        else:
            os.environ["PRESENTAGENT_SLIDE_MEDIA_OUTPUT_DIR"] = (
                previous_slide_media_output_dir
            )
        if previous_notes_mode is None:
            os.environ.pop("PRESENTAGENT_NOTES_MODE", None)
        else:
            os.environ["PRESENTAGENT_NOTES_MODE"] = previous_notes_mode

    output_dir.mkdir(parents=True, exist_ok=True)
    _copytree_contents(run_dir, output_dir)

    final_pptx = output_dir / f"final_{notes_mode}.pptx"
    generated_final_pptx = output_dir / "final.pptx"
    if generated_final_pptx.exists():
        generated_final_pptx.replace(final_pptx)
    if not final_pptx.exists():
        raise RuntimeError(f"Backend PPT generation did not produce {final_pptx}")

    print("success: True")
    print(f"notes_mode: {notes_mode}")
    print(f"backend_task_id: {task_id}")
    print(f"backend_run_dir: {run_dir}")
    print(f"document_json: {document_json_path}")
    print(f"template_pptx: {template_pptx_path}")
    print(f"final_pptx: {final_pptx}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate PPT from refined_doc.json by calling the original backend.ppt_gen flow."
    )
    parser.add_argument("--document-json", required=True, help="Path to refined_doc.json")
    parser.add_argument(
        "--image-dir",
        default="",
        help="Directory containing media assets referenced by the document",
    )
    parser.add_argument(
        "--template-pptx",
        default=str(DEFAULT_TEMPLATE_PPTX),
        help="Path to the template .pptx used by the backend flow",
    )
    parser.add_argument("--output-dir", required=True, help="Directory to copy PPT outputs")
    parser.add_argument("--num-slides", type=int, default=8, help="Target number of slides")
    parser.add_argument(
        "--notes-modes",
        default="single_presentation",
        help="Comma-separated notes modes to run, e.g. single_presentation,discussion",
    )
    args = parser.parse_args()

    document_json_path = Path(args.document_json).resolve()
    if not document_json_path.exists():
        raise SystemExit(f"refined_doc.json not found: {document_json_path}")

    template_pptx_path = Path(args.template_pptx).resolve()
    if not template_pptx_path.exists():
        raise SystemExit(f"template pptx not found: {template_pptx_path}")

    image_dir = Path(args.image_dir).resolve() if args.image_dir else document_json_path.parent
    output_dir = Path(args.output_dir).resolve()
    notes_modes = [
        mode.strip()
        for mode in args.notes_modes.split(",")
        if mode.strip()
    ]
    for notes_mode in notes_modes:
        mode_output_dir = output_dir / notes_mode
        asyncio.run(
            _run_backend_ppt_gen(
                document_json_path=document_json_path,
                image_dir=image_dir,
                template_pptx_path=template_pptx_path,
                output_dir=mode_output_dir,
                num_slides=args.num_slides,
                notes_mode=notes_mode,
            )
        )


if __name__ == "__main__":
    main()
