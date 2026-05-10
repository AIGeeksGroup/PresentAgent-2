from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pptagent.research.content_resolver import ContentResolver, ResolvedContent

TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _progress(message: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def _slugify(text: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return normalized[:80] or "query"


def _load_document_content(document_path: str) -> str:
    if not document_path:
        return ""
    path = Path(document_path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_deepresearch_env(deepresearch_root: Path) -> dict[str, str]:
    env_file = deepresearch_root / ".env"
    if not env_file.exists():
        raise FileNotFoundError(f"DeepResearch .env not found: {env_file}")
    env_values: dict[str, str] = {}
    for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in raw_line:
            continue
        key, value = raw_line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        env_values[key] = value
    return env_values


def _resolve_dataset_path(deepresearch_root: Path, dataset_path: str | None = None) -> Path:
    if dataset_path:
        return Path(dataset_path)
    env_values = _load_deepresearch_env(deepresearch_root)
    dataset_value = env_values.get("DATASET", "").strip()
    if not dataset_value:
        raise ValueError(f"DATASET is not configured in {deepresearch_root / '.env'}")
    path = Path(dataset_value)
    if path.is_absolute():
        return path
    return (deepresearch_root / "inference" / path).resolve()


def _write_dataset(question: str, deepresearch_root: Path, dataset_path: str | None = None) -> Path:
    target_path = _resolve_dataset_path(deepresearch_root, dataset_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps({"question": question.strip(), "answer": ""}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target_path


def _launch_run_react_infer(
    deepresearch_root: Path,
    report_path: Path,
    conda_env: str = "",
    conda_executable: str = "conda",
) -> tuple[subprocess.Popen[str], object]:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_handle = open(report_path, "a", encoding="utf-8", buffering=1)

    bash_command = f'cd "{deepresearch_root}" && bash inference/run_react_infer.sh'
    if conda_env:
        command = [
            "bash",
            "-lc",
            f'source "$({conda_executable} info --base)/etc/profile.d/conda.sh" && '
            f'conda activate {conda_env} && {bash_command}',
        ]
    else:
        command = ["bash", "-lc", bash_command]

    process = subprocess.Popen(
        command,
        cwd=str(deepresearch_root),
        stdout=report_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, report_handle


def _read_text_since(result_file: Path, file_offset: int, partial_line: str) -> tuple[str, int, str]:
    if not result_file.exists():
        return "", file_offset, partial_line
    with open(result_file, encoding="utf-8") as f:
        f.seek(file_offset)
        chunk = f.read()
        new_offset = f.tell()
    if not chunk:
        return "", new_offset, partial_line
    buffer = partial_line + chunk
    lines = buffer.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        partial_line = lines.pop()
    else:
        partial_line = ""
    return "".join(lines), new_offset, partial_line


def _extract_visit_urls(text: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for tool_call in TOOL_CALL_RE.findall(text):
        try:
            payload = json.loads(tool_call)
        except json.JSONDecodeError:
            continue
        if payload.get("name") != "visit":
            continue
        arguments = payload.get("arguments", {})
        value = arguments.get("url", [])
        if isinstance(value, str):
            value = [value]
        for url in value:
            if not isinstance(url, str):
                continue
            cleaned = url.strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            urls.append(cleaned)
    return urls


def _motion_media_count(result: ResolvedContent) -> int:
    return result.media_stats.gif_count + result.media_stats.video_count


def _motion_rank_key(result: ResolvedContent) -> tuple[int, int, int, int]:
    stats = result.media_stats
    return (
        stats.gif_count + stats.video_count,
        stats.gif_count,
        stats.video_count,
        result.total_text_length,
    )


def _resolve_best_media_content_live(
    *,
    result_path: Path,
    output_dir: str,
    topic: str,
    max_wait_seconds: float,
    poll_interval_seconds: float,
    min_motion_media_count: int | None,
    quiet: bool,
) -> ResolvedContent:
    resolver = ContentResolver()
    deadline = time.monotonic() + max(max_wait_seconds, 0.0)
    seen_urls: set[str] = set()
    first_success: ResolvedContent | None = None
    best_result: ResolvedContent | None = None
    best_video_result: ResolvedContent | None = None
    last_failure: ResolvedContent | None = None
    file_offset = 0
    partial_line = ""

    while True:
        if not quiet:
            best_url = (best_result.final_url or best_result.source_url) if best_result else "-"
            best_video_url = (
                (best_video_result.final_url or best_video_result.source_url)
                if best_video_result
                else "-"
            )
            _progress(
                f"[poll] seen={len(seen_urls)} "
                f"best={best_url} "
                f"motion={0 if best_result is None else _motion_media_count(best_result)} "
                f"video={0 if best_result is None else best_result.media_stats.video_count} "
                f"best_video={best_video_url}"
            )

        raw_text, file_offset, partial_line = _read_text_since(
            result_path,
            file_offset=file_offset,
            partial_line=partial_line,
        )
        visit_urls = _extract_visit_urls(raw_text)
        if visit_urls and not quiet:
            _progress(f"[log] extracted_visit_urls={len(visit_urls)}")

        for url in visit_urls:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if not quiet:
                _progress(f"[resolve:start] url={url}")
            result = resolver.resolve(
                url=url,
                output_dir=output_dir,
                topic=topic,
                goal="",
                summary_hint="",
            )
            if not quiet:
                error_suffix = f" error={result.error}" if result.error else ""
                _progress(
                    "[resolve:done] "
                    f"success={result.success} "
                    f"type={result.content_type} "
                    f"motion={_motion_media_count(result)} "
                    f"figures={result.media_stats.figure_count} "
                    f"url={url}{error_suffix}"
                )

            if result.success and first_success is None:
                first_success = result
            if not result.success:
                last_failure = result
            if not result.success or result.content_type != "html":
                continue

            if (
                min_motion_media_count is not None
                and result.media_stats.video_count > 0
                and _motion_media_count(result) >= min_motion_media_count
            ):
                if not quiet:
                    _progress(
                        f"[resolve:early-stop] motion={_motion_media_count(result)} "
                        f"url={result.final_url or result.source_url}"
                    )
                return result

            if best_result is None or _motion_rank_key(result) > _motion_rank_key(best_result):
                best_result = result
                if not quiet:
                    _progress(
                        f"[resolve:best] motion={_motion_media_count(best_result)} "
                        f"gif={best_result.media_stats.gif_count} "
                        f"video={best_result.media_stats.video_count} "
                        f"url={best_result.final_url or best_result.source_url}"
                    )

            if result.media_stats.video_count > 0 and (
                best_video_result is None
                or _motion_rank_key(result) > _motion_rank_key(best_video_result)
            ):
                best_video_result = result
                if not quiet:
                    _progress(
                        f"[resolve:best-video] motion={_motion_media_count(best_video_result)} "
                        f"gif={best_video_result.media_stats.gif_count} "
                        f"video={best_video_result.media_stats.video_count} "
                        f"url={best_video_result.final_url or best_video_result.source_url}"
                    )

        if time.monotonic() >= deadline:
            if not quiet:
                _progress("[poll] max_wait_seconds_reached")
            break

        sleep_seconds = min(max(poll_interval_seconds, 0.0), max(deadline - time.monotonic(), 0.0))
        if sleep_seconds > 0:
            if not quiet:
                _progress(f"[poll] sleeping={sleep_seconds:.2f}s")
            time.sleep(sleep_seconds)
        else:
            break

    if best_video_result is not None:
        return best_video_result
    if best_result is not None:
        return best_result
    if first_success is not None:
        return first_success
    if last_failure is not None:
        return last_failure
    return ResolvedContent(
        source_url="",
        success=False,
        error="no visit urls found before live media selection timed out",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write query to DeepResearch dataset, run DeepResearch, poll live log, resolve visit URLs."
    )
    parser.add_argument("--result-path", default="", help="Existing DeepResearch report/log file to monitor.")
    parser.add_argument("--query", default="", help="User question for end-to-end run.")
    parser.add_argument("--deepresearch-root", default="", help="DeepResearch project root.")
    parser.add_argument("--report-path", default="", help="Optional log path.")
    parser.add_argument("--dataset-path", default="", help="Optional dataset path override.")
    parser.add_argument("--deepresearch-conda-env", default="", help="Conda env used to run DeepResearch.")
    parser.add_argument("--deepresearch-conda-executable", default="conda", help="Conda executable. Default: conda.")
    parser.add_argument("--output-dir", default="", help="Directory for content_resolver output.")
    parser.add_argument("--topic", default="", help="Topic string for resolver.")
    parser.add_argument("--max-wait-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    parser.add_argument("--min-motion-media-count", type=int, default=None)
    parser.add_argument("--save-json", default="")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.query:
        if not args.deepresearch_root:
            raise SystemExit("--deepresearch-root is required when --query is used")
        deepresearch_root = Path(args.deepresearch_root).resolve()
        query_slug = _slugify(args.query)
        report_path = Path(args.report_path) if args.report_path else deepresearch_root / f"{query_slug}.log"
        output_dir = args.output_dir or str(PROJECT_ROOT / f"tmp_{query_slug}")
        dataset_path = _write_dataset(args.query, deepresearch_root, args.dataset_path or None)
        if not args.quiet:
            _progress(f"[launch] dataset={dataset_path}")
            _progress(f"[launch] report={report_path}")
        process, report_handle = _launch_run_react_infer(
            deepresearch_root=deepresearch_root,
            report_path=report_path,
            conda_env=args.deepresearch_conda_env,
            conda_executable=args.deepresearch_conda_executable,
        )
        if not args.quiet:
            _progress(f"[launch] pid={process.pid}")
        try:
            result = _resolve_best_media_content_live(
                result_path=report_path,
                output_dir=output_dir,
                topic=args.topic or args.query,
                max_wait_seconds=args.max_wait_seconds,
                poll_interval_seconds=args.poll_interval_seconds,
                min_motion_media_count=args.min_motion_media_count,
                quiet=args.quiet,
            )
        finally:
            if process.poll() is None:
                if not args.quiet:
                    _progress("[launch] terminating_deepresearch_process")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if not args.quiet:
                        _progress("[launch] killing_deepresearch_process")
                    process.kill()
                    process.wait(timeout=10)
            if report_handle is not None:
                report_handle.close()
    else:
        if not args.result_path:
            raise SystemExit("either --result-path or --query must be provided")
        if not args.output_dir:
            raise SystemExit("--output-dir is required when --result-path is used")
        result = _resolve_best_media_content_live(
            result_path=Path(args.result_path),
            output_dir=args.output_dir,
            topic=args.topic or "",
            max_wait_seconds=args.max_wait_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
            min_motion_media_count=args.min_motion_media_count,
            quiet=args.quiet,
        )

    payload = {
        "source_url": result.source_url,
        "final_url": result.final_url,
        "success": result.success,
        "content_type": result.content_type,
        "local_path": result.local_path,
        "document_path": result.document_path,
        "document_content": _load_document_content(result.document_path),
        "media_stats": result.media_stats.to_dict(),
        "media_candidates": [candidate.to_dict() for candidate in result.media_candidates],
        "error": result.error,
    }

    if args.save_json:
        save_path = Path(args.save_json)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
