from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from pptagent.model_utils import ModelManager, parse_pdf
from pptagent.research.pdf_resolver import PdfResolver


@dataclass
class TopicSpec:
    topic: str
    paper_dir: str
    url: str


TOPICS: list[TopicSpec] = [
    TopicSpec(
        topic="SemanticDraw",
        paper_dir="SemanticDraw_Towards_Real_Time_Interactive_Content_Creation_from_Image_Diffusion_Models",
        url="https://jaerinlee.com/research/semantic-draw",
    ),
    TopicSpec(
        topic="Feature 3DGS",
        paper_dir="Feature_3DGS",
        url="https://feature-3dgs.github.io/",
    ),
    TopicSpec(
        topic="UniDepth",
        paper_dir="UniDepth",
        url="https://lpiccinelli-eth.github.io/pub/unidepth/",
    ),
    TopicSpec(
        topic="3DInAction",
        paper_dir="3DInAction",
        url="https://sitzikbs.github.io/3dinaction.github.io/",
    ),
    TopicSpec(
        topic="TubeDETR",
        paper_dir="TubeDETR",
        url="https://antoyang.github.io/tubedetr.html",
    ),
    TopicSpec(
        topic="FrozenBiLM",
        paper_dir="FrozenBiLM",
        url="https://antoyang.github.io/slides/frozenbilm-neurips-poster.pdf",
    ),
    TopicSpec(
        topic="Trajectory2Pose",
        paper_dir="Trajectory2Pose",
        url="https://jaewoo97.github.io/t2p_/",
    ),
    TopicSpec(
        topic="LightIt",
        paper_dir="LightIt",
        url="https://peter-kocsis.github.io/LightIt/",
    ),
    TopicSpec(
        topic="AirPlanes",
        paper_dir="AirPlanes",
        url="https://nianticlabs.github.io/airplanes/",
    ),
    TopicSpec(
        topic="ViewDiff",
        paper_dir="ViewDiff",
        url="https://lukashoel.github.io/ViewDiff/",
    ),
    TopicSpec(
        topic="EfficientGrasp",
        paper_dir="EfficientGrasp",
        url="https://arxiv.org/pdf/2206.15159",
    ),
    TopicSpec(
        topic="Sweep_Your_Map",
        paper_dir="Sweep_Your_Map",
        url="https://lucabartolomei.github.io/publications/RAL_2022_Sweep_Your_Map.pdf",
    ),
]


def _run_subprocess(command: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


def _looks_like_pdf(url: str) -> bool:
    lowered = url.lower()
    return lowered.endswith(".pdf") or "/pdf/" in lowered or "arxiv.org/pdf/" in lowered


def _resolve_pdf_to_source(url: str, output_dir: Path) -> Path:
    resolver = PdfResolver()
    result = resolver.resolve_to_pdf(url, str(output_dir), topic="")
    if not result.success or not result.local_path:
        raise RuntimeError(f"failed to resolve pdf from url: {url}; error={result.error}")
    models = ModelManager()
    parse_pdf(result.local_path, str(output_dir), models.marker_model)
    return output_dir / "source.md"


def _run_source_to_document(source_md: Path, output_dir: Path, env: dict[str, str]) -> None:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "test_source_md_to_document.py"),
        "--source-md",
        str(source_md),
        "--output-dir",
        str(output_dir),
    ]
    _run_subprocess(command, env)


def _run_html_to_source(url: str, output_dir: Path, env: dict[str, str]) -> Path:
    command = [
        sys.executable,
        str(Path(__file__).resolve().parent / "test_html_to_source_md.py"),
        "--url",
        url,
        "--output-dir",
        str(output_dir),
    ]
    _run_subprocess(command, env)
    return output_dir / "source.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch run url->source->document for a fixed topic list.")
    parser.add_argument(
        "--output-root",
        required=True,
        help="Root directory where per-paper folders will be created",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env[key] = ""

    summary: list[dict[str, str]] = []

    for spec in TOPICS:
        paper_root = output_root / spec.paper_dir
        html_to_source_dir = paper_root / "url_to_source"
        source_to_document_dir = paper_root / "source_to_document"
        paper_root.mkdir(parents=True, exist_ok=True)
        html_to_source_dir.mkdir(parents=True, exist_ok=True)
        source_to_document_dir.mkdir(parents=True, exist_ok=True)

        try:
            if _looks_like_pdf(spec.url):
                source_md = _resolve_pdf_to_source(spec.url, html_to_source_dir)
            else:
                source_md = _run_html_to_source(spec.url, html_to_source_dir, env)
            _run_source_to_document(source_md, source_to_document_dir, env)
            summary.append(
                {
                    "topic": spec.topic,
                    "paper_dir": spec.paper_dir,
                    "url": spec.url,
                    "status": "success",
                    "source_md": str(source_md),
                    "refined_doc": str(source_to_document_dir / "refined_doc.json"),
                }
            )
        except Exception as exc:
            summary.append(
                {
                    "topic": spec.topic,
                    "paper_dir": spec.paper_dir,
                    "url": spec.url,
                    "status": "failed",
                    "error": str(exc),
                }
            )

    summary_path = output_root / "batch_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)


if __name__ == "__main__":
    main()
