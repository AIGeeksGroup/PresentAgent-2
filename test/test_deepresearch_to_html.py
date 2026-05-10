from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from pathlib import Path


def _find_repo_root(script_path: Path) -> Path:
    current = script_path.resolve().parent
    for candidate in (current, *current.parents):
        if (candidate / "pptagent").is_dir():
            return candidate
    raise RuntimeError(
        f"Could not locate repository root from {script_path}; expected a parent directory containing 'pptagent/'."
    )


ROOT = _find_repo_root(Path(__file__))


def _load_module(name: str, path: Path, package_path: Path | None = None):
    kwargs = {}
    if package_path is not None:
        kwargs["submodule_search_locations"] = [str(package_path)]
    spec = importlib.util.spec_from_file_location(name, path, **kwargs)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bootstrap_live_runner():
    pptagent_pkg = types.ModuleType("pptagent")
    pptagent_pkg.__path__ = [str(ROOT / "pptagent")]
    sys.modules["pptagent"] = pptagent_pkg

    research_pkg = types.ModuleType("pptagent.research")
    research_pkg.__path__ = [str(ROOT / "pptagent" / "research")]
    sys.modules["pptagent.research"] = research_pkg

    utils_mod = types.ModuleType("pptagent.utils")

    def package_join(*parts):
        return str(ROOT / "pptagent" / Path(*parts))

    utils_mod.package_join = package_join
    sys.modules["pptagent.utils"] = utils_mod

    return _load_module(
        "pptagent.research.live_runner",
        ROOT / "pptagent" / "research" / "live_runner.py",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run DeepResearch via run_react_infer.sh and apply PresentAgent top-3 HTML selection."
    )
    parser.add_argument("--question", required=True, help="User request passed into DeepResearch.")
    parser.add_argument(
        "--deepresearch-root",
        required=True,
        help="Path to the DeepResearch repository root.",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory where selected candidate outputs should be written.",
    )
    parser.add_argument(
        "--report-path",
        default="",
        help="Optional explicit report.log path. Defaults to <output-dir>/report.log.",
    )
    parser.add_argument(
        "--dataset-path",
        default="",
        help="Optional explicit dataset jsonl path. Defaults to DATASET from DeepResearch .env.",
    )
    parser.add_argument(
        "--conda-env",
        default="",
        help="Optional conda environment name used to launch run_react_infer.sh.",
    )
    parser.add_argument(
        "--conda-executable",
        default="conda",
        help="Conda executable used when --conda-env is provided.",
    )
    parser.add_argument(
        "--max-wait-seconds",
        type=float,
        default=900.0,
        help="Maximum time to wait for DeepResearch before returning.",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=30.0,
        help="Polling interval for reading DeepResearch output logs.",
    )
    parser.add_argument(
        "--keep-runner",
        action="store_true",
        help="Do not terminate the DeepResearch process when the wrapper returns.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = (
        Path(args.report_path).resolve()
        if args.report_path
        else output_dir / "report.log"
    )

    live_runner = _bootstrap_live_runner()
    result = live_runner.run_deepresearch_live(
        question=args.question,
        deepresearch_root=str(Path(args.deepresearch_root).resolve()),
        output_dir=str(output_dir),
        report_path=str(report_path),
        dataset_path=str(Path(args.dataset_path).resolve()) if args.dataset_path else None,
        conda_env=args.conda_env,
        conda_executable=args.conda_executable,
        max_wait_seconds=args.max_wait_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        progress_callback=print,
        terminate_runner_on_return=not args.keep_runner,
    )

    print("\nResolvedContent:")
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2, default=str))
    print("\nArtifacts:")
    print(f"report_log: {report_path}")
    print(f"output_dir: {output_dir}")
    print(f"candidates_dir: {output_dir / 'candidates'}")


if __name__ == "__main__":
    main()
