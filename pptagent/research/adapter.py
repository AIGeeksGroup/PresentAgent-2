from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from urllib.parse import urlparse

from .content_resolver import ContentResolver, ResolvedContent
from .dossier import (
    ResearchDossier,
    ResearchMediaCandidate,
    ResearchOutlineCandidate,
    ResearchOutlineSection,
    ResearchSource,
)
from .pdf_resolver import PdfResolutionResult, PdfResolver

if TYPE_CHECKING:
    from pptagent.document import Document, Section, SubSection

MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
URL_RE = re.compile(r"https?://[^\s<>\]\)]+")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TOOL_CALL_RE = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
SUMMARY_BLOCK_RE = re.compile(
    r"The useful information in (?P<quote>\"?)(?P<url>https?://[^\s\"\\]+)(?P=quote).*?"
    r"Summary:\s*\n(?P<summary>.*?)(?=\n=======\nThe useful information in |\Z)",
    re.S,
)


@dataclass
class VisitEntry:
    url: str
    goal: str = ""
    summary_hint: str = ""
    order: int = 0


class DeepResearchAdapter:
    """
    Adapter between raw DeepResearch outputs and PresentAgent's internal types.

    By default, the adapter can:
    1. load a standardized ResearchDossier json file, or
    2. parse a raw DeepResearch json/jsonl result into a ResearchDossier.

    If PRESENTAGENT_DEEPRESEARCH_RUNNER is configured, it can also invoke an
    external DeepResearch command using a topic string and then parse the result.
    """

    def __init__(
        self,
        runner_template: str | None = None,
        result_glob: str | None = None,
        dossier_filename: str = "research_dossier.json",
        pdf_resolver: PdfResolver | None = None,
        content_resolver: ContentResolver | None = None,
        preferred_motion_min_total_text_length: int = 15_000,
    ):
        self.runner_template = runner_template or os.getenv(
            "PRESENTAGENT_DEEPRESEARCH_RUNNER"
        )
        self.result_glob = result_glob or os.getenv(
            "PRESENTAGENT_DEEPRESEARCH_RESULT_GLOB", "**/iter*.jsonl"
        )
        self.dossier_filename = dossier_filename
        self.pdf_resolver = pdf_resolver or PdfResolver()
        self.content_resolver = content_resolver or ContentResolver()
        self.preferred_motion_min_total_text_length = preferred_motion_min_total_text_length

    def topic_to_document(
        self,
        topic: str,
        workspace_dir: str,
        result_path: str | None = None,
    ) -> tuple[ResearchDossier, Document]:
        dossier = self.topic_to_dossier(topic, workspace_dir, result_path=result_path)
        document = self.dossier_to_document(dossier, workspace_dir)
        return dossier, document

    def topic_to_dossier(
        self,
        topic: str,
        workspace_dir: str,
        result_path: str | None = None,
    ) -> ResearchDossier:
        workspace = Path(workspace_dir)
        workspace.mkdir(parents=True, exist_ok=True)
        dossier_path = workspace / self.dossier_filename
        if dossier_path.exists():
            return self.load_dossier(str(dossier_path))

        raw_result_path = self._resolve_result_path(workspace, result_path)
        if raw_result_path is None:
            raw_result_path = self._run_deepresearch(topic, workspace)
        dossier = self.load_raw_result(raw_result_path, topic=topic)
        dossier.metadata.setdefault("workspace_dir", str(workspace))
        dossier.metadata.setdefault("raw_result_path", str(raw_result_path))
        with open(dossier_path, "w", encoding="utf-8") as f:
            json.dump(dossier.to_dict(), f, ensure_ascii=False, indent=2)
        return dossier

    def load_dossier(self, dossier_path: str) -> ResearchDossier:
        with open(dossier_path, encoding="utf-8") as f:
            return ResearchDossier.from_dict(json.load(f))

    def load_raw_result(
        self,
        result_path: str,
        topic: str | None = None,
    ) -> ResearchDossier:
        result_file = Path(result_path)
        if result_file.suffix == ".json":
            with open(result_file, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, list):
                payload = payload[0]
        elif result_file.suffix == ".jsonl":
            with open(result_file, encoding="utf-8") as f:
                payloads = [json.loads(line) for line in f if line.strip()]
            payload = payloads[0]
            if topic is not None:
                for candidate in payloads:
                    if candidate.get("question", "").strip() == topic.strip():
                        payload = candidate
                        break
        else:
            raise ValueError(f"Unsupported DeepResearch result format: {result_path}")
        return self._raw_result_to_dossier(payload, topic=topic)

    def resolve_source_pdf(
        self,
        url: str,
        download_dir: str,
        topic: str = "",
    ) -> PdfResolutionResult:
        return self.pdf_resolver.resolve_to_pdf(
            url=url,
            download_dir=download_dir,
            topic=topic,
        )

    def resolve_source_content(
        self,
        url: str,
        output_dir: str,
        topic: str = "",
        goal: str = "",
        summary_hint: str = "",
    ) -> ResolvedContent:
        return self.content_resolver.resolve(
            url=url,
            output_dir=output_dir,
            topic=topic,
            goal=goal,
            summary_hint=summary_hint,
        )

    def resolve_first_complete_content(
        self,
        result_path: str,
        output_dir: str,
        topic: str | None = None,
    ) -> ResolvedContent:
        result_file = Path(result_path)
        if result_file.suffix == ".json":
            with open(result_file, encoding="utf-8") as f:
                payload = json.load(f)
            if isinstance(payload, list):
                payload = payload[0]
        elif result_file.suffix == ".jsonl":
            with open(result_file, encoding="utf-8") as f:
                payloads = [json.loads(line) for line in f if line.strip()]
            payload = payloads[0]
            if topic is not None:
                for candidate in payloads:
                    if candidate.get("question", "").strip() == topic.strip():
                        payload = candidate
                        break
        else:
            raise ValueError(f"Unsupported DeepResearch result format: {result_path}")
        return self.resolve_first_complete_content_from_payload(
            payload=payload,
            output_dir=output_dir,
            topic=topic,
        )

    def resolve_first_complete_content_from_payload(
        self,
        payload: dict,
        output_dir: str,
        topic: str | None = None,
    ) -> ResolvedContent:
        normalized_topic = topic or payload.get("question", "").strip()
        visit_entries = self._extract_visit_entries(payload.get("messages", []))
        if len(visit_entries) == 0:
            return ResolvedContent(
                source_url="",
                success=False,
                error="no visit urls found in DeepResearch payload",
            )

        first_success: ResolvedContent | None = None
        last_failure: ResolvedContent | None = None
        for entry in visit_entries:
            result = self.resolve_source_content(
                url=entry.url,
                output_dir=output_dir,
                topic=normalized_topic,
                goal=entry.goal,
                summary_hint=entry.summary_hint,
            )
            if self._is_preferred_motion_presentation_source(result):
                return result
            if result.success and first_success is None:
                first_success = result
                continue
            last_failure = result

        if first_success is not None:
            return first_success
        if last_failure is not None:
            return last_failure
        return ResolvedContent(
            source_url="",
            success=False,
            error="no complete content found from visit urls",
        )

    def resolve_best_media_content_live(
        self,
        result_path: str,
        output_dir: str,
        topic: str | None = None,
        max_wait_seconds: float = 600.0,
        poll_interval_seconds: float = 2.0,
        min_motion_media_count: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
    ) -> ResolvedContent:
        result_file = Path(result_path)
        deadline = time.monotonic() + max(max_wait_seconds, 0.0)
        active_topic = topic or ""
        seen_urls: set[str] = set()
        first_success: ResolvedContent | None = None
        best_result: ResolvedContent | None = None
        best_video_result: ResolvedContent | None = None
        last_failure: ResolvedContent | None = None
        file_offset = 0
        partial_line = ""

        while True:
            if progress_callback is not None:
                best_url = ""
                best_video_url = ""
                if best_result is not None:
                    best_url = best_result.final_url or best_result.source_url
                if best_video_result is not None:
                    best_video_url = best_video_result.final_url or best_video_result.source_url
                progress_callback(
                    f"[poll] seen={len(seen_urls)} "
                    f"best={best_url or '-'} "
                    f"motion={0 if best_result is None else self._motion_media_count(best_result)} "
                    f"video={0 if best_result is None else best_result.media_stats.video_count} "
                    f"best_video={best_video_url or '-'}"
                )
            raw_text, file_offset, partial_line = self._read_text_since(
                result_file,
                file_offset=file_offset,
                partial_line=partial_line,
            )
            log_entries = self._extract_visit_entries_from_text(raw_text)
            if progress_callback is not None and log_entries:
                progress_callback(f"[log] extracted_visit_urls={len(log_entries)}")
            for entry in log_entries:
                if entry.url in seen_urls:
                    continue
                seen_urls.add(entry.url)
                if progress_callback is not None:
                    progress_callback(f"[resolve:start] url={entry.url}")
                    result = self.resolve_source_content(
                        url=entry.url,
                        output_dir=output_dir,
                        topic=active_topic,
                        goal=entry.goal,
                        summary_hint=entry.summary_hint,
                    )
                    if progress_callback is not None:
                        error_suffix = f" error={result.error}" if result.error else ""
                        progress_callback(
                            "[resolve:done] "
                            f"success={result.success} "
                            f"type={result.content_type} "
                            f"motion={self._motion_media_count(result)} "
                            f"figures={result.media_stats.figure_count} "
                            f"url={entry.url}"
                            f"{error_suffix}"
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
                    and self._motion_media_count(result) >= min_motion_media_count
                ):
                    if progress_callback is not None:
                        progress_callback(
                            f"[resolve:early-stop] motion={self._motion_media_count(result)} url={result.final_url or result.source_url}"
                        )
                    return result
                if best_result is None or self._motion_rank_key(result) > self._motion_rank_key(best_result):
                    best_result = result
                    if progress_callback is not None:
                        progress_callback(
                            f"[resolve:best] motion={self._motion_media_count(best_result)} "
                            f"gif={best_result.media_stats.gif_count} "
                            f"video={best_result.media_stats.video_count} "
                            f"url={best_result.final_url or best_result.source_url}"
                        )
                if result.media_stats.video_count > 0 and (
                    best_video_result is None
                    or self._motion_rank_key(result) > self._motion_rank_key(best_video_result)
                ):
                    best_video_result = result
                    if progress_callback is not None:
                        progress_callback(
                            f"[resolve:best-video] motion={self._motion_media_count(best_video_result)} "
                            f"gif={best_video_result.media_stats.gif_count} "
                            f"video={best_video_result.media_stats.video_count} "
                            f"url={best_video_result.final_url or best_video_result.source_url}"
                        )

            if time.monotonic() >= deadline:
                if progress_callback is not None:
                    progress_callback("[poll] max_wait_seconds_reached")
                break
            sleep_seconds = min(
                max(poll_interval_seconds, 0.0),
                max(deadline - time.monotonic(), 0.0),
            )
            if sleep_seconds > 0:
                if progress_callback is not None:
                    progress_callback(f"[poll] sleeping={sleep_seconds:.2f}s")
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

    def prepare_deepresearch_eval_data(
        self,
        question: str,
        deepresearch_root: str,
        dataset_path: str | None = None,
    ) -> str:
        normalized_question = question.strip()
        if not normalized_question:
            raise ValueError("question must not be empty")

        if dataset_path is not None:
            target_path = Path(dataset_path)
        else:
            target_path = self._resolve_deepresearch_dataset_path(deepresearch_root)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"question": normalized_question, "answer": ""}, ensure_ascii=False))
            f.write("\n")
        return str(target_path)

    def launch_deepresearch(
        self,
        *,
        deepresearch_root: str,
        dataset_path: str,
        report_path: str,
        runner_script: str | None = None,
        conda_env: str | None = None,
        conda_executable: str = "conda",
        run_label: str = "generic_deepresearch",
        env_overrides: dict[str, str] | None = None,
    ) -> tuple[subprocess.Popen[str], object | None]:
        deepresearch_root_path = Path(deepresearch_root)
        report_file = Path(report_path)
        report_file.parent.mkdir(parents=True, exist_ok=True)

        env = os.environ.copy()
        if env_overrides:
            env.update(env_overrides)
        prompt_module = env.get("PRESENTAGENT_DEEPRESEARCH_PROMPT_MODULE", "prompt")

        bash_path = shutil.which("bash")
        if bash_path is None:
            raise FileNotFoundError("bash was not found in PATH")

        default_runner = deepresearch_root_path / "inference" / "run_react_infer.sh"
        runner_path = Path(runner_script) if runner_script is not None else default_runner
        if not runner_path.exists():
            raise FileNotFoundError(f"DeepResearch runner script not found: {runner_path}")
        script_command = f"{shlex.quote(bash_path)} {shlex.quote(str(runner_path))}"

        if conda_env:
            command_str = (
                f"{shlex.quote(conda_executable)} run --no-capture-output -n "
                f"{shlex.quote(conda_env)} {script_command}"
            )
        else:
            command_str = script_command

        header_lines = [
            "=== PresentAgent DeepResearch Launch ===",
            f"run_label={run_label}",
            f"prompt_module={prompt_module}",
            f"dataset_path={dataset_path}",
            f"runner_script={runner_path}",
            f"conda_env={conda_env or ''}",
            "=== Begin Runner Output ===",
        ]
        header_command = " && ".join(
            f"printf '%s\\n' {shlex.quote(line)}"
            for line in header_lines
        )
        shell_command = (
            f"cd {shlex.quote(str(deepresearch_root_path))} && "
            f"( {header_command} && {command_str} ) > {shlex.quote(str(report_file))} 2>&1"
        )
        print(
            "[deepresearch] "
            f"run_label={run_label} "
            f"prompt_module={prompt_module} "
            f"report_path={report_file}"
        )
        process = subprocess.Popen(
            [bash_path, "-lc", shell_command],
            cwd=str(deepresearch_root_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
        )
        return process, None

    def run_deepresearch_and_resolve_best_media_content_live(
        self,
        *,
        question: str,
        deepresearch_root: str,
        output_dir: str,
        report_path: str,
        dataset_path: str | None = None,
        runner_script: str | None = None,
        conda_env: str | None = None,
        conda_executable: str = "conda",
        topic: str | None = None,
        max_wait_seconds: float = 600.0,
        poll_interval_seconds: float = 2.0,
        min_motion_media_count: int | None = None,
        progress_callback: Callable[[str], None] | None = None,
        terminate_runner_on_return: bool = True,
        env_overrides: dict[str, str] | None = None,
    ) -> ResolvedContent:
        prepared_dataset_path = self.prepare_deepresearch_eval_data(
            question=question,
            deepresearch_root=deepresearch_root,
            dataset_path=dataset_path,
        )
        if progress_callback is not None:
            progress_callback(f"[launch] dataset={prepared_dataset_path}")
            progress_callback(f"[launch] report={report_path}")

        process, report_handle = self.launch_deepresearch(
            deepresearch_root=deepresearch_root,
            dataset_path=prepared_dataset_path,
            report_path=report_path,
            runner_script=runner_script,
            conda_env=conda_env,
            conda_executable=conda_executable,
            env_overrides=env_overrides,
        )
        if progress_callback is not None:
            progress_callback(f"[launch] pid={process.pid}")

        try:
            return self.resolve_best_media_content_live(
                result_path=report_path,
                output_dir=output_dir,
                topic=topic or question,
                max_wait_seconds=max_wait_seconds,
                poll_interval_seconds=poll_interval_seconds,
                min_motion_media_count=min_motion_media_count,
                progress_callback=progress_callback,
            )
        finally:
            try:
                if terminate_runner_on_return and process.poll() is None:
                    if progress_callback is not None:
                        progress_callback("[launch] terminating_deepresearch_process")
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        if progress_callback is not None:
                            progress_callback("[launch] killing_deepresearch_process")
                        process.kill()
                        process.wait(timeout=10)
            finally:
                if report_handle is not None:
                    report_handle.close()

    def _is_preferred_motion_presentation_source(self, result: ResolvedContent) -> bool:
        return (
            result.success
            and result.content_type == "html"
            and result.has_explanatory_motion_media
            and result.total_text_length >= self.preferred_motion_min_total_text_length
        )

    def _motion_rank_key(self, result: ResolvedContent) -> tuple[int, int, int, int]:
        stats = result.media_stats
        return (
            stats.gif_count + stats.video_count,
            stats.gif_count,
            stats.video_count,
            result.total_text_length,
        )

    def _motion_media_count(self, result: ResolvedContent) -> int:
        stats = result.media_stats
        return stats.gif_count + stats.video_count

    def _slugify_topic(self, topic: str) -> str:
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", topic.strip().lower()).strip("_")
        if normalized:
            return normalized[:80]
        return hashlib.md5(topic.encode("utf-8")).hexdigest()[:12]

    def _resolve_deepresearch_dataset_path(self, deepresearch_root: str) -> Path:
        env_values = self._load_deepresearch_env(Path(deepresearch_root))
        dataset_value = env_values.get("DATASET", "").strip()
        if not dataset_value:
            raise ValueError(f"DATASET is not configured in {Path(deepresearch_root) / '.env'}")
        dataset_path = Path(dataset_value)
        if dataset_path.is_absolute():
            return dataset_path
        return (Path(deepresearch_root) / "inference" / dataset_path).resolve()

    def _load_deepresearch_env(self, deepresearch_root: Path) -> dict[str, str]:
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

    def _build_run_multi_react_command(self, deepresearch_root: Path) -> str:
        env_values = self._load_deepresearch_env(deepresearch_root)
        inference_dir = (deepresearch_root / "inference").resolve()

        def _required_env(name: str) -> str:
            value = env_values.get(name, "").strip()
            if not value:
                raise ValueError(f"{name} is not configured in {deepresearch_root / '.env'}")
            return value

        dataset_value = _required_env("DATASET")
        output_value = _required_env("OUTPUT_PATH")
        model_value = _required_env("MODEL_PATH")
        max_workers = _required_env("MAX_WORKERS")
        temperature = _required_env("TEMPERATURE")
        presence_penalty = _required_env("PRESENCE_PENALTY")

        return (
            f'cd "{inference_dir}" && '
            f'export DATASET="{dataset_value}" OUTPUT_PATH="{output_value}" MODEL_PATH="{model_value}" '
            f'MAX_WORKERS="{max_workers}" TEMPERATURE="{temperature}" PRESENCE_PENALTY="{presence_penalty}"; '
            'python -u run_multi_react.py '
            '--dataset "$DATASET" '
            '--output "$OUTPUT_PATH" '
            '--max_workers "$MAX_WORKERS" '
            '--model "$MODEL_PATH" '
            '--temperature "$TEMPERATURE" '
            '--presence_penalty "$PRESENCE_PENALTY" '
            '--total_splits "${WORLD_SIZE:-1}" '
            '--worker_split $((${RANK:-0} + 1)) '
            '--roll_out_count 1'
        )

    def _read_dotenv_value(self, env_file: Path, key: str) -> str | None:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)\s*$")
        for raw_line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = pattern.match(raw_line)
            if not match:
                continue
            value = match.group(1).strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            return value
        return None

    def _is_terminated_payload(self, payload: dict) -> bool:
        termination = str(payload.get("termination", "")).strip().lower()
        if termination and termination not in {"running", "in_progress", "processing"}:
            return True
        prediction = str(payload.get("prediction", "")).strip()
        return bool(prediction and prediction != "[Failed]")

    def _read_text_since(
        self,
        result_file: Path,
        *,
        file_offset: int,
        partial_line: str,
    ) -> tuple[str, int, str]:
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
        raw_text = "".join(lines)
        return raw_text, new_offset, partial_line

    def _extract_visit_entries_from_text(self, text: str) -> list[VisitEntry]:
        if not text.strip():
            return []
        visit_entries: list[VisitEntry] = []
        seen: set[str] = set()
        order = 0
        for tool_call in TOOL_CALL_RE.findall(text):
            try:
                payload = json.loads(tool_call)
            except json.JSONDecodeError:
                continue
            if payload.get("name") != "visit":
                continue
            arguments = payload.get("arguments", {})
            goal = arguments.get("goal", "")
            urls = arguments.get("url", [])
            if isinstance(urls, str):
                urls = [urls]
            for url in urls:
                if not isinstance(url, str):
                    continue
                cleaned_url = url.strip()
                if not cleaned_url or cleaned_url in seen:
                    continue
                seen.add(cleaned_url)
                visit_entries.append(
                    VisitEntry(
                        url=cleaned_url,
                        goal=goal,
                        summary_hint="",
                        order=order,
                    )
                )
                order += 1
        return visit_entries

    def dossier_to_document(
        self,
        dossier: ResearchDossier,
        image_dir: str,
    ) -> Document:
        from pptagent.document import Document, Section, SubSection

        image_root = Path(image_dir)
        image_root.mkdir(parents=True, exist_ok=True)

        sections: list[Section] = []
        best_outline = dossier.best_outline
        if best_outline is not None and len(best_outline.sections) > 0:
            for outline_section in best_outline.sections:
                subsection_chunks = []
                if outline_section.summary.strip():
                    subsection_chunks.append(
                        SubSection(
                            title=f"{outline_section.title} Summary",
                            content=outline_section.summary.strip(),
                            medias=[],
                        )
                    )
                for idx, bullet in enumerate(outline_section.bullet_points, start=1):
                    subsection_chunks.append(
                        SubSection(
                            title=f"{outline_section.title} Point {idx}",
                            content=bullet,
                            medias=[],
                        )
                    )
                if len(subsection_chunks) == 0:
                    subsection_chunks.append(
                        SubSection(
                            title=outline_section.title,
                            content=outline_section.summary or outline_section.title,
                            medias=[],
                        )
                    )
                sections.append(
                    Section(
                        title=outline_section.title,
                        summary=outline_section.summary or outline_section.title,
                        subsections=subsection_chunks,
                        markdown_content=outline_section.summary or outline_section.title,
                    )
                )
        else:
            summary_paragraphs = [
                paragraph.strip()
                for paragraph in dossier.summary.split("\n\n")
                if paragraph.strip()
            ]
            if len(summary_paragraphs) == 0 and dossier.summary.strip():
                summary_paragraphs = [dossier.summary.strip()]
            if len(summary_paragraphs) == 0:
                summary_paragraphs = [dossier.topic]

            summary_subsections = [
                SubSection(
                    title=f"Topic Summary {idx}",
                    content=paragraph,
                    medias=[],
                )
                for idx, paragraph in enumerate(summary_paragraphs, start=1)
            ]
            sections.append(
                Section(
                    title="Topic Overview",
                    summary=summary_paragraphs[0],
                    subsections=summary_subsections,
                    markdown_content=dossier.summary,
                )
            )

        if len(dossier.sources) > 0:
            sections.append(
                Section(
                    title="Sources",
                    summary=f"{len(dossier.sources)} retrieved sources",
                    subsections=[
                        SubSection(
                            title=source.title or f"Source {idx}",
                            content="\n".join(
                                [
                                    f"URL: {source.url}",
                                    f"Type: {source.source_type}",
                                    source.snippet,
                                ]
                            ).strip(),
                            medias=[],
                        )
                        for idx, source in enumerate(dossier.sources, start=1)
                    ],
                    markdown_content=dossier.summary,
                )
            )

        if len(dossier.media_candidates) > 0:
            sections.append(
                Section(
                    title="Media Opportunities",
                    summary=f"{len(dossier.media_candidates)} candidate media assets",
                    subsections=[
                        SubSection(
                            title=media.title or f"Media {idx}",
                            content="\n".join(
                                [
                                    f"URL: {media.url}",
                                    f"Type: {media.media_type}",
                                    media.rationale,
                                ]
                            ).strip(),
                            medias=[],
                        )
                        for idx, media in enumerate(dossier.media_candidates, start=1)
                    ],
                    markdown_content=dossier.summary,
                )
            )

        metadata = {
            "title": dossier.topic,
            "topic": dossier.topic,
            "source-count": str(len(dossier.sources)),
            "mode": "topic",
        }
        metadata.update(dossier.metadata)
        return Document(image_dir=str(image_root), sections=sections, metadata=metadata)

    def _resolve_result_path(
        self,
        workspace: Path,
        result_path: str | None,
    ) -> str | None:
        if result_path is not None and Path(result_path).exists():
            return str(Path(result_path))
        for candidate in workspace.glob(self.result_glob):
            if candidate.is_file():
                return str(candidate)
        return None

    def _run_deepresearch(self, topic: str, workspace: Path) -> str:
        if not self.runner_template:
            raise FileNotFoundError(
                "No DeepResearch result found and PRESENTAGENT_DEEPRESEARCH_RUNNER is not configured."
            )

        topic_hash = hashlib.md5(topic.encode("utf-8")).hexdigest()[:8]
        dataset_path = workspace / f"{topic_hash}.jsonl"
        output_dir = workspace / "deepresearch_output"
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(dataset_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"question": topic, "answer": ""}, ensure_ascii=False))
            f.write("\n")

        command = self.runner_template.format(
            topic=topic,
            topic_file=str(dataset_path),
            output_dir=str(output_dir),
            workspace=str(workspace),
        )
        subprocess.run(command, shell=True, check=True, cwd=str(workspace))

        result_path = self._resolve_result_path(output_dir, None)
        if result_path is None:
            raise FileNotFoundError(
                f"DeepResearch runner completed but no result matching {self.result_glob} was found under {output_dir}."
            )
        return result_path

    def _raw_result_to_dossier(
        self,
        payload: dict,
        topic: str | None = None,
    ) -> ResearchDossier:
        normalized_topic = topic or payload.get("question", "").strip()
        summary = self._extract_summary(payload)
        sources = self._extract_sources(payload.get("messages", []))
        outline_candidates = self._build_outline_candidates(normalized_topic, summary)
        media_candidates = self._extract_media_candidates(sources)
        return ResearchDossier(
            topic=normalized_topic,
            summary=summary,
            sources=sources,
            outline_candidates=outline_candidates,
            media_candidates=media_candidates,
            metadata={
                "termination": payload.get("termination", ""),
                "prediction": payload.get("prediction", ""),
            },
            raw_result=payload,
        )

    def _extract_summary(self, payload: dict) -> str:
        prediction = payload.get("prediction", "").strip()
        if prediction and prediction != "[Failed]":
            return prediction

        for message in reversed(payload.get("messages", [])):
            content = message.get("content", "")
            if "<answer>" in content and "</answer>" in content:
                return content.split("<answer>", 1)[1].split("</answer>", 1)[0].strip()
            if message.get("role") == "assistant" and content.strip():
                return content.strip()
        return ""

    def _extract_sources(self, messages: list[dict]) -> list[ResearchSource]:
        seen_urls: set[str] = set()
        sources: list[ResearchSource] = []
        for message in messages:
            content = message.get("content", "")
            if not content:
                continue
            for title, url in MARKDOWN_LINK_RE.findall(content):
                source = self._build_source(url=url, title=title, snippet=content)
                if source.url not in seen_urls:
                    seen_urls.add(source.url)
                    sources.append(source)
            for url in URL_RE.findall(content):
                if url in seen_urls:
                    continue
                source = self._build_source(url=url, title="", snippet=content)
                seen_urls.add(source.url)
                sources.append(source)
        return sources

    def _extract_visit_entries(self, messages: list[dict]) -> list[VisitEntry]:
        summary_hints_by_url = self._extract_summary_hints(messages)
        visit_entries: list[VisitEntry] = []
        order = 0
        for message in messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content", "")
            if not content:
                continue
            for tool_call in TOOL_CALL_RE.findall(content):
                try:
                    payload = json.loads(tool_call)
                except json.JSONDecodeError:
                    continue
                if payload.get("name") != "visit":
                    continue
                arguments = payload.get("arguments", {})
                goal = arguments.get("goal", "")
                urls = arguments.get("url", [])
                if isinstance(urls, str):
                    urls = [urls]
                for url in urls:
                    if not isinstance(url, str) or not url.strip():
                        continue
                    cleaned_url = url.strip()
                    visit_entries.append(
                        VisitEntry(
                            url=cleaned_url,
                            goal=goal,
                            summary_hint=summary_hints_by_url.get(cleaned_url, ""),
                            order=order,
                        )
                    )
                    order += 1
        return visit_entries

    def _extract_summary_hints(self, messages: list[dict]) -> dict[str, str]:
        hints: dict[str, str] = {}
        for message in messages:
            content = message.get("content", "")
            if not content or "The useful information in " not in content:
                continue
            for match in SUMMARY_BLOCK_RE.finditer(content):
                url = match.group("url").strip()
                summary = match.group("summary").strip()
                if url and summary and url not in hints:
                    hints[url] = summary
        return hints

    def _build_source(self, url: str, title: str, snippet: str) -> ResearchSource:
        hostname = urlparse(url).netloc or "web"
        source_type = "web"
        if "arxiv.org" in hostname:
            source_type = "paper"
        elif "github.com" in hostname:
            source_type = "repo"
        elif any(token in hostname for token in ["youtube.com", "youtu.be", "x.com", "twitter.com", "tiktok.com", "vimeo.com"]):
            source_type = "video"
        return ResearchSource(
            title=title or hostname,
            url=url,
            snippet=snippet.strip(),
            source_type=source_type,
        )

    def _build_outline_candidates(
        self,
        topic: str,
        summary: str,
    ) -> list[ResearchOutlineCandidate]:
        sections = self._sections_from_summary(summary)
        if len(sections) == 0:
            sections = [
                ResearchOutlineSection(
                    title="Topic Overview",
                    summary=topic,
                    bullet_points=[],
                )
            ]
        return [
            ResearchOutlineCandidate(
                title=f"{topic} Presentation Outline",
                sections=sections,
                rationale="Derived from DeepResearch summary output.",
            )
        ]

    def _sections_from_summary(self, summary: str) -> list[ResearchOutlineSection]:
        lines = [line.rstrip() for line in summary.splitlines()]
        sections: list[ResearchOutlineSection] = []
        current_title: str | None = None
        current_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            heading_match = HEADING_RE.match(stripped)
            if heading_match:
                if current_title is not None:
                    sections.append(
                        self._finalize_outline_section(current_title, current_lines)
                    )
                current_title = heading_match.group(2).strip()
                current_lines = []
                continue
            if current_title is None:
                current_title = "Topic Overview"
            current_lines.append(stripped)

        if current_title is not None:
            sections.append(self._finalize_outline_section(current_title, current_lines))

        if len(sections) > 0:
            return sections

        paragraphs = [para.strip() for para in summary.split("\n\n") if para.strip()]
        return [
            self._finalize_outline_section(f"Insight {idx}", [paragraph])
            for idx, paragraph in enumerate(paragraphs[:5], start=1)
        ]

    def _finalize_outline_section(
        self,
        title: str,
        content_lines: list[str],
    ) -> ResearchOutlineSection:
        bullets = []
        summary_lines = []
        for line in content_lines:
            if line.startswith(("-", "*")):
                bullets.append(line[1:].strip())
            else:
                summary_lines.append(line)
        summary = "\n".join(summary_lines).strip()
        if not summary and len(bullets) > 0:
            summary = bullets[0]
            bullets = bullets[1:]
        return ResearchOutlineSection(
            title=title,
            summary=summary,
            bullet_points=bullets,
        )

    def _extract_media_candidates(
        self,
        sources: list[ResearchSource],
    ) -> list[ResearchMediaCandidate]:
        media_candidates = []
        for source in sources:
            if source.source_type != "video":
                continue
            media_candidates.append(
                ResearchMediaCandidate(
                    title=source.title,
                    url=source.url,
                    media_type=source.source_type,
                    rationale="Retrieved by DeepResearch and likely useful as an explainer asset.",
                    source_ref=source.url,
                )
            )
        return media_candidates
