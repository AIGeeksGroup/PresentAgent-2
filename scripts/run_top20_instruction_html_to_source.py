from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class InstructionSpec:
    paper_dir: str
    instruction: str
    url: str


TOP20_SPECS: list[InstructionSpec] = [
    InstructionSpec(
        paper_dir="3DInAction",
        instruction="Create a presentation video explaining 3DInAction, focusing on human action understanding in 3D point clouds, the core pipeline, the t-patch representation, and the key results.",
        url="https://sitzikbs.github.io/3dinaction.github.io/",
    ),
    InstructionSpec(
        paper_dir="AutoSDF",
        instruction="Create a presentation video explaining AutoSDF, covering multimodal 3D shape generation and completion, the progressive reconstruction idea, and the main qualitative advantages over baselines.",
        url="https://yccyenchicheng.github.io/AutoSDF/",
    ),
    InstructionSpec(
        paper_dir="BANMo_Building_Animatable_3D_Neural_Models_from_Many_Casual_Videos",
        instruction="Create a presentation video explaining BANMo, including how animatable 3D neural models are reconstructed from many casual videos, the canonical representation, and the resulting motion-aware reconstructions.",
        url="https://banmo-www.github.io/",
    ),
    InstructionSpec(
        paper_dir="Dual_Shutter_Optical_Vibration_Sensing",
        instruction="Create a presentation video explaining Dual-Shutter Optical Vibration Sensing, covering the vibration sensing problem, the dual-shutter design, the signal recovery process, and the practical benefits.",
        url="https://imaging.cs.cmu.edu/vibration",
    ),
    InstructionSpec(
        paper_dir="FastForward",
        instruction="Create a presentation video explaining FastForward, including the problem it addresses, the key acceleration idea, the pipeline design, and the observed efficiency gains.",
        url="https://nianticspatial.github.io/fastforward/",
    ),
    InstructionSpec(
        paper_dir="Feature_3DGS",
        instruction="Create a presentation video explaining Feature 3DGS, focusing on how semantic or learned features are incorporated into 3D Gaussian Splatting and why that matters for downstream tasks.",
        url="https://feature-3dgs.github.io/",
    ),
    InstructionSpec(
        paper_dir="General_Virtual_Sketching",
        instruction="Create a presentation video explaining General Virtual Sketching, including the task of vector line art generation, the framework design, representative use cases, and the visual outputs.",
        url="https://markmohr.github.io/virtual_sketching/",
    ),
    InstructionSpec(
        paper_dir="K_Plane",
        instruction="Create a presentation video explaining K-Planes, focusing on planar factorization for radiance fields, the extension from static to dynamic scenes, and the efficiency-performance tradeoff.",
        url="https://sarafridov.github.io/K-Planes/",
    ),
    InstructionSpec(
        paper_dir="Learning_Neural_Volumetric_Representations_of_Dynamic_Humans_in_Minutes",
        instruction="Create a presentation video explaining how neural volumetric representations of dynamic humans can be learned in minutes, including the efficiency motivation, the representation, and the main reconstruction results.",
        url="https://zju3dv.github.io/instant_nvr/",
    ),
    InstructionSpec(
        paper_dir="LightIt",
        instruction="Create a presentation video explaining LightIt, including the lighting editing problem, the method workflow, and how the system improves controllable visual relighting.",
        url="https://peter-kocsis.github.io/LightIt/",
    ),
    InstructionSpec(
        paper_dir="LMTraj",
        instruction="Create a presentation video explaining LMTraj, focusing on how language models are adapted for trajectory prediction, the social reasoning idea, and the main forecasting results.",
        url="https://ihbae.com/publication/lmtrajectory/",
    ),
    InstructionSpec(
        paper_dir="MobileNeRF",
        instruction="Create a presentation video explaining MobileNeRF, covering how neural field rendering is adapted to mobile graphics pipelines and why this leads to efficient mobile deployment.",
        url="https://mobile-nerf.github.io/",
    ),
    InstructionSpec(
        paper_dir="MultiPly",
        instruction="Create a presentation video explaining MultiPly, including multi-person dynamic scene modeling, the core representation, and the main qualitative reconstructions.",
        url="https://eth-ait.github.io/MultiPly/",
    ),
    InstructionSpec(
        paper_dir="RainyGS",
        instruction="Create a presentation video explaining RainyGS, focusing on Gaussian Splatting under rainy conditions, the robustness challenges, and the improvements in rendering or reconstruction quality.",
        url="https://pku-vcl-geometry.github.io/RainyGS/",
    ),
    InstructionSpec(
        paper_dir="RoDynRF_Robust_Dynamic_Radiance_Fields",
        instruction="Create a presentation video explaining RoDynRF, including robust dynamic radiance fields, the challenge of dynamic scene reconstruction, and the method's improvements over prior approaches.",
        url="https://robust-dynrf.github.io/",
    ),
    InstructionSpec(
        paper_dir="SCANimate",
        instruction="Create a presentation video explaining SCANimate, covering how clothed human avatars are animated from scans, the underlying deformation model, and the quality of the resulting animations.",
        url="https://scanimate.is.tue.mpg.de/",
    ),
    InstructionSpec(
        paper_dir="SemanticDraw_Towards_Real_Time_Interactive_Content_Creation_from_Image_Diffusion_Models",
        instruction="Create a presentation video explaining SemanticDraw, including real-time interactive content creation from image diffusion models, the user interaction loop, and the main editing capabilities.",
        url="https://jaerinlee.com/research/semantic-draw/",
    ),
    InstructionSpec(
        paper_dir="SpectroMotion",
        instruction="Create a presentation video explaining SpectroMotion, focusing on how spectrogram or audio-conditioned representations are used for motion generation and what advantages they provide.",
        url="https://cdfan0627.github.io/spectromotion/",
    ),
    InstructionSpec(
        paper_dir="Trajectory2Pose",
        instruction="Create a presentation video explaining Trajectory2Pose, including how trajectories are translated into human pose sequences, the representation design, and the generated motion quality.",
        url="https://jaewoo97.github.io/t2p_/",
    ),
    InstructionSpec(
        paper_dir="ViewDiff",
        instruction="Create a presentation video explaining ViewDiff, covering diffusion-based multi-view or novel-view synthesis, the conditioning strategy, and the resulting view-consistent generation quality.",
        url="https://lukashoel.github.io/ViewDiff/",
    ),
]


def _run_subprocess(command: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed with exit code {completed.returncode}: {' '.join(command)}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch full HTML and build source.md for the fixed top-20 presentation instructions."
    )
    parser.add_argument("--output-root", required=True, help="Root directory for per-paper bundle folders")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip samples that already contain url_to_source/source.md",
    )
    args = parser.parse_args()

    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]:
        env[key] = ""

    summary: list[dict[str, str]] = []
    html_to_source_script = Path(__file__).resolve().parents[1] / "test" / "test_html_to_source_md.py"

    for spec in TOP20_SPECS:
        paper_root = output_root / spec.paper_dir
        url_to_source_dir = paper_root / "url_to_source"
        paper_root.mkdir(parents=True, exist_ok=True)
        url_to_source_dir.mkdir(parents=True, exist_ok=True)
        (paper_root / "instruction.txt").write_text(spec.instruction + "\n", encoding="utf-8")
        (paper_root / "instruction_meta.json").write_text(
            json.dumps(asdict(spec), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        source_md_path = url_to_source_dir / "source.md"
        if args.skip_existing and source_md_path.exists():
            summary.append(
                {
                    "paper_dir": spec.paper_dir,
                    "url": spec.url,
                    "status": "skipped_existing",
                    "instruction_path": str(paper_root / "instruction.txt"),
                    "source_md": str(source_md_path),
                }
            )
            continue

        command = [
            sys.executable,
            str(html_to_source_script),
            "--url",
            spec.url,
            "--output-dir",
            str(url_to_source_dir),
        ]

        try:
            _run_subprocess(command, env)
            summary.append(
                {
                    "paper_dir": spec.paper_dir,
                    "url": spec.url,
                    "status": "success",
                    "instruction_path": str(paper_root / "instruction.txt"),
                    "source_md": str(source_md_path),
                }
            )
        except Exception as exc:
            summary.append(
                {
                    "paper_dir": spec.paper_dir,
                    "url": spec.url,
                    "status": "failed",
                    "instruction_path": str(paper_root / "instruction.txt"),
                    "error": str(exc),
                }
            )

        (output_root / "batch_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    manifest = [asdict(spec) for spec in TOP20_SPECS]
    (output_root / "instruction_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_root / "batch_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_root / "batch_summary.json")


if __name__ == "__main__":
    main()
