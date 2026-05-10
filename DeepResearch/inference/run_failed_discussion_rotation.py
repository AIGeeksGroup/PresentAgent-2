from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


SECONDS_PER_RUN = 7 * 60


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_failed_instructions(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a list in {path}")
    return data


def write_example_jsonl(path: Path, instruction: str) -> None:
    record = {"question": instruction, "answer": ""}
    path.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")


def start_run(repo_root: Path, log_path: Path) -> subprocess.Popen:
    command = "bash ./DeepResearch/inference/run_react_infer.sh"
    log_file = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        command,
        cwd=repo_root,
        shell=True,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def stop_run(proc: subprocess.Popen, grace_seconds: int = 15) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return

    deadline = time.time() + grace_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return
        time.sleep(1)

    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def archive_log(current_log: Path, archive_log: Path) -> None:
    if current_log.exists():
        archive_log.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(current_log, archive_log)


def main() -> None:
    repo_root = project_root()
    inference_root = repo_root / "DeepResearch" / "inference"
    grouped_root = (
        repo_root
        / "ai_paper_slop"
        / "html_to_source_batch_discussion"
        / "grouped_by_status"
    )
    instructions_path = grouped_root / "12_failed_instructions.json"
    example_jsonl = inference_root / "eval_data" / "example.jsonl"
    backup_jsonl = inference_root / "eval_data" / "example.backup_before_failed_rotation.jsonl"
    current_log = repo_root / "discuss.log"
    archive_root = repo_root / "DeepResearch" / "inference" / "failed_discussion_logs"

    instructions = load_failed_instructions(instructions_path)
    if example_jsonl.exists() and not backup_jsonl.exists():
        shutil.copyfile(example_jsonl, backup_jsonl)

    manifest: list[dict] = []

    for index, item in enumerate(instructions, start=1):
        run_id = str(item["id"])
        slug = str(item["slug"])
        instruction = str(item["instruction"])

        print(f"[{index}/{len(instructions)}] Starting {slug}", flush=True)
        write_example_jsonl(example_jsonl, instruction)

        proc = start_run(repo_root, current_log)
        started_at = time.strftime("%Y-%m-%d %H:%M:%S")
        time.sleep(SECONDS_PER_RUN)
        stop_run(proc)
        finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

        archive_log_path = archive_root / f"{run_id}_{slug}.log"
        archive_log(current_log, archive_log_path)

        manifest.append(
            {
                "id": run_id,
                "slug": slug,
                "topic": item.get("topic", ""),
                "instruction": instruction,
                "example_jsonl": str(example_jsonl),
                "log_path": str(archive_log_path),
                "started_at": started_at,
                "finished_at": finished_at,
                "seconds_run": SECONDS_PER_RUN,
            }
        )

        (archive_root / "rotation_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[{index}/{len(instructions)}] Finished {slug}", flush=True)

    print("All 12 failed instructions have been processed.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        raise
