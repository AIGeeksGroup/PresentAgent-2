from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .content_resolver import ContentResolver, ResolvedContent
from ..utils import package_join

WEBPAGE_SELECTOR_PROMPT = open(
    package_join("prompts", "webpage_selector.txt"), encoding="utf-8"
).read()

TARGET_PRESENTATION_VISUALS = 13
MAX_PRESENTATION_VISUALS = 18
MIN_MOTION_VISUALS = 1
TOP_HTML_CANDIDATES = 3
HTML_MARKDOWN_PREFIX_CLEANUP_PROMPT = open(
    package_join("prompts", "html_markdown_prefix_cleanup.txt"), encoding="utf-8"
).read()
HTML_MARKDOWN_SUFFIX_CLEANUP_PROMPT = open(
    package_join("prompts", "html_markdown_suffix_cleanup.txt"), encoding="utf-8"
).read()
MARKDOWN_IMAGE_REGEX = re.compile(r"!\[.*?\]\((.*?)\)", re.DOTALL)
MARKDOWN_VIDEO_REGEX = re.compile(
    r'<video[^>]*src=["\']([^"\']+)["\'][^>]*>(?:.*?</video>)?',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class _CandidatePage:
    source_url: str
    final_url: str
    title: str
    html_text: str
    excerpt: str
    total_text_length: int
    substantial_block_count: int
    media_urls: list[str]
    media_candidates: list
    media_stats: object


def _build_deepresearch_question(user_question: str) -> str:
    normalized_question = user_question.strip()
    return (
        "We are preparing a presentation for a user based on a short request.\n"
        f"User request: {normalized_question}\n\n"
        "Your job is to find webpages that can support a presentation which directly addresses the user's concern or question.\n"
        "Prioritize HTML pages whose content can realistically be turned into presentation material, not just pages that mention the topic.\n\n"
        "When searching and visiting pages, prefer pages that:\n"
        "1. explain the topic clearly and substantially,\n"
        "2. contain usable visual material such as figures, GIFs, videos, diagrams, or illustrated examples,\n"
        "3. are likely to remain useful after webpage cleanup into source.md,\n"
        "4. are complete content pages rather than thin landing pages, navigation pages, or citation-only pages.\n\n"
        "Focus on finding HTML links that we can use to build a strong presentation for the user."
    )


def _candidate_rank_key(page: _CandidatePage) -> tuple[int, int, int, int, int]:
    total_visuals = int(getattr(page.media_stats, "total_visual_count", 0))
    gif_count = int(getattr(page.media_stats, "gif_count", 0))
    video_count = int(getattr(page.media_stats, "video_count", 0))
    within_limit = int(total_visuals <= MAX_PRESENTATION_VISUALS)
    has_motion = int(gif_count >= 1 or video_count >= 1)
    has_both_motion_types = int(gif_count >= 1 and video_count >= 1)
    visual_distance = abs(total_visuals - TARGET_PRESENTATION_VISUALS)
    return (
        within_limit,
        has_motion,
        has_both_motion_types,
        -visual_distance,
        page.total_text_length,
    )


def _update_top_candidate_pages(
    candidates: list[_CandidatePage],
    page: _CandidatePage,
) -> list[_CandidatePage]:
    filtered = [candidate for candidate in candidates if candidate.final_url != page.final_url]
    filtered.append(page)
    filtered.sort(key=_candidate_rank_key, reverse=True)
    return filtered[:TOP_HTML_CANDIDATES]


def _normalize_media_urls(lines: list[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for raw in lines:
        url = str(raw).strip()
        lowered = url.lower()
        if not url or lowered == "none":
            continue
        if lowered.startswith("blob:"):
            continue
        if "localhost" in lowered or "127.0.0.1" in lowered:
            continue
        if not lowered.startswith(("http://", "https://")):
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def _build_markdown_excerpt(markdown_text: str, max_lines: int = 200) -> str:
    lines = markdown_text.splitlines()
    return "\n".join(lines[:max_lines])


def _build_markdown_tail_excerpt(markdown_text: str, max_lines: int = 160) -> str:
    lines = markdown_text.splitlines()
    start_index = max(len(lines) - max_lines, 0)
    return "\n".join(lines[start_index:])


def _build_markdown_head_excerpt(markdown_text: str, max_lines: int = 200) -> str:
    lines = markdown_text.splitlines()
    excerpt_lines = lines[:max_lines]
    return "\n".join(excerpt_lines)


def _apply_delete_ranges(
    markdown_text: str,
    delete_ranges: list[dict],
) -> str:
    def _normalize_match_text(value: str) -> str:
        normalized = re.sub(r"\s+", " ", value.strip())
        # LLM cleanup outputs sometimes collapse BibTeX escape sequences such as
        # {\"o} -> {"o}. Normalize both forms so anchor matching is resilient.
        normalized = normalized.replace('\\"', '"')
        return normalized

    def _line_matches(expected: str, actual: str) -> bool:
        if not expected:
            return True
        normalized_expected = _normalize_match_text(expected)
        normalized_actual = _normalize_match_text(actual)
        if not normalized_expected or not normalized_actual:
            return False
        return (
            actual.startswith(expected)
            or normalized_actual == normalized_expected
            or normalized_actual.startswith(normalized_expected)
        )

    def _find_matching_line(
        expected: str,
        *,
        lines: list[str],
        search_start: int,
        search_end: int,
    ) -> int:
        for line_idx in range(search_start, search_end + 1):
            if _line_matches(expected, lines[line_idx - 1]):
                return line_idx
        raise ValueError(f"cleanup anchor text not found: {expected!r}")

    def _expand_repeated_single_line_range(
        expected: str,
        *,
        lines: list[str],
        start_line: int,
        search_end: int,
    ) -> int:
        """
        When the cleanup model uses the same start/end anchor for duplicated
        boilerplate, treat the range as the whole contiguous repeated block
        rather than only the first matching line.
        """
        end_line = start_line
        cursor = start_line + 1
        while cursor <= search_end:
            current_line = lines[cursor - 1]
            if not current_line.strip():
                lookahead = cursor + 1
                if lookahead <= search_end and _line_matches(expected, lines[lookahead - 1]):
                    end_line = cursor
                    cursor += 1
                    continue
                break
            if _line_matches(expected, current_line):
                end_line = cursor
                cursor += 1
                continue
            break
        return end_line

    lines = markdown_text.splitlines()
    if not delete_ranges:
        return markdown_text

    normalized_ranges: list[tuple[int, int, str, str]] = []
    previous_end = 0
    for item in delete_ranges:
        if not isinstance(item, dict):
            raise ValueError("cleanup output contains a non-dict delete range")
        start_text = str(item.get("start_text", ""))
        end_text = str(item.get("end_text", ""))
        if not start_text or not end_text:
            raise ValueError(
                "cleanup output must contain non-empty start_text and end_text"
            )
        start_line = _find_matching_line(
            start_text,
            lines=lines,
            search_start=previous_end + 1,
            search_end=len(lines),
        )
        if _normalize_match_text(start_text) == _normalize_match_text(end_text):
            end_line = _expand_repeated_single_line_range(
                end_text,
                lines=lines,
                start_line=start_line,
                search_end=len(lines),
            )
        else:
            end_line = _find_matching_line(
                end_text,
                lines=lines,
                search_start=start_line,
                search_end=len(lines),
            )

        if start_line <= previous_end:
            raise ValueError("cleanup output contains overlapping delete ranges")
        normalized_ranges.append((start_line, end_line, start_text, end_text))
        previous_end = end_line

    kept_lines: list[str] = []
    next_keep_start = 1
    for start_line, end_line, _, _ in normalized_ranges:
        kept_lines.extend(lines[next_keep_start - 1 : start_line - 1])
        next_keep_start = end_line + 1
    kept_lines.extend(lines[next_keep_start - 1 :])
    cleaned_markdown = "\n".join(kept_lines)
    if markdown_text.endswith("\n"):
        cleaned_markdown += "\n"
    return cleaned_markdown


def _cleanup_markdown_prefix_with_llm(
    *,
    markdown_text: str,
    resolver: ContentResolver,
) -> str:
    if not resolver.llm_model:
        return markdown_text

    try:
        from openai import OpenAI
    except Exception:
        return markdown_text

    excerpt = _build_markdown_excerpt(markdown_text)
    prompt = HTML_MARKDOWN_PREFIX_CLEANUP_PROMPT.format(markdown_excerpt=excerpt)
    client = OpenAI(
        base_url=resolver.llm_base_url,
        api_key=resolver.llm_api_key,
        timeout=max(float(resolver.timeout), 180.0),
        max_retries=0,
    )
    last_error = None
    completion = None
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=resolver.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You identify noisy markdown line ranges copied from webpage HTML.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(min(2 * (attempt + 1), 8))
    if completion is None:
        raise ValueError(f"cleanup model call failed: {last_error}")
    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("cleanup model returned empty content")
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        left = content.find("{")
        right = content.rfind("}")
        if left == -1 or right == -1 or left > right:
            raise ValueError("cleanup model did not return valid JSON")
        decision = json.loads(content[left : right + 1])

    delete_ranges = decision.get("delete_ranges", [])
    if not isinstance(delete_ranges, list):
        raise ValueError("cleanup model returned non-list delete_ranges")
    return _apply_delete_ranges(markdown_text, delete_ranges)


def _suffix_cleanup_should_skip(
    markdown_text: str,
    *,
    head_max_lines: int = 200,
    tail_max_lines: int = 160,
) -> bool:
    lines = markdown_text.splitlines()
    if not lines:
        return True
    # If the article is short enough that the tail excerpt substantially overlaps
    # the whole body, suffix cleanup becomes too risky and tends to erase real
    # content instead of only trailing recommendations/footer noise.
    if len(lines) <= max(head_max_lines, tail_max_lines):
        return True
    return False


def _cleanup_markdown_suffix_with_llm(
    *,
    markdown_text: str,
    resolver: ContentResolver,
) -> str:
    if not resolver.llm_model:
        return markdown_text
    if _suffix_cleanup_should_skip(markdown_text):
        return markdown_text

    try:
        from openai import OpenAI
    except Exception:
        return markdown_text

    tail_excerpt = _build_markdown_tail_excerpt(markdown_text)
    markdown_head_excerpt = _build_markdown_head_excerpt(markdown_text)
    prompt = HTML_MARKDOWN_SUFFIX_CLEANUP_PROMPT.format(
        markdown_head_excerpt=markdown_head_excerpt,
        markdown_tail_excerpt=tail_excerpt,
    )
    client = OpenAI(
        base_url=resolver.llm_base_url,
        api_key=resolver.llm_api_key,
        timeout=max(float(resolver.timeout), 180.0),
        max_retries=0,
    )
    last_error = None
    completion = None
    for attempt in range(3):
        try:
            completion = client.chat.completions.create(
                model=resolver.llm_model,
                messages=[
                    {
                        "role": "system",
                        "content": "You identify noisy markdown line ranges copied from webpage HTML.",
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            break
        except Exception as exc:
            last_error = exc
            if attempt == 2:
                raise
            time.sleep(min(2 * (attempt + 1), 8))
    if completion is None:
        raise ValueError(f"suffix cleanup model call failed: {last_error}")
    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise ValueError("suffix cleanup model returned empty content")
    try:
        decision = json.loads(content)
    except json.JSONDecodeError:
        left = content.find("{")
        right = content.rfind("}")
        if left == -1 or right == -1 or left > right:
            raise ValueError("suffix cleanup model did not return valid JSON")
        decision = json.loads(content[left : right + 1])

    delete_ranges = decision.get("delete_ranges", [])
    if not isinstance(delete_ranges, list):
        raise ValueError("suffix cleanup model returned non-list delete_ranges")
    return _apply_delete_ranges(markdown_text, delete_ranges)


def _fallback_choose_better_page(*, current: _CandidatePage, best: _CandidatePage) -> _CandidatePage:
    return current if _candidate_rank_key(current) > _candidate_rank_key(best) else best


def _llm_choose_better_page(
    *,
    current: _CandidatePage,
    best: _CandidatePage,
    question: str,
    resolver: ContentResolver,
) -> _CandidatePage:
    if not resolver.llm_model:
        return _fallback_choose_better_page(current=current, best=best)

    try:
        from openai import OpenAI
    except Exception:
        return _fallback_choose_better_page(current=current, best=best)

    prompt = WEBPAGE_SELECTOR_PROMPT.format(
        question=question,
        current_url=current.final_url,
        current_media_urls=json.dumps(current.media_urls, ensure_ascii=False),
        current_excerpt=current.excerpt,
        best_url=best.final_url,
        best_media_urls=json.dumps(best.media_urls, ensure_ascii=False),
        best_excerpt=best.excerpt,
    )
    try:
        client = OpenAI(
            base_url=resolver.llm_base_url,
            api_key=resolver.llm_api_key,
            timeout=resolver.timeout,
        )
        completion = client.chat.completions.create(
            model=resolver.llm_model,
            messages=[
                {"role": "system", "content": "You are a careful webpage selector for presentation generation."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = (completion.choices[0].message.content or "").strip()
        if not content:
            return _fallback_choose_better_page(current=current, best=best)
        try:
            decision = json.loads(content)
        except json.JSONDecodeError:
            left = content.find("{")
            right = content.rfind("}")
            if left == -1 or right == -1 or left > right:
                return _fallback_choose_better_page(current=current, best=best)
            decision = json.loads(content[left : right + 1])
        return current if str(decision.get("pick", "")).strip().lower() == "current" else best
    except Exception:
        return _fallback_choose_better_page(current=current, best=best)


def _fetch_candidate_page(
    *,
    url: str,
    resolver: ContentResolver,
) -> tuple[_CandidatePage | None, str]:
    try:
        response = resolver.session.get(
            url,
            timeout=resolver.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
    except Exception as exc:
        return None, f"failed to fetch source url: {exc}"

    final_url = resolver._normalize_url(response.url)
    content_type = resolver._content_type(response)
    if not resolver._is_html(response):
        return None, f"unsupported non-html content type: {content_type or 'unknown'}"

    html_text = response.text or ""
    if not html_text.strip():
        return None, "empty html response"

    assessment = resolver._assess_html(html_text)
    media_candidates, media_stats = resolver._extract_media_candidates(
        page_url=final_url,
        html_text=html_text,
    )
    direct_media_urls, _ = resolver._extract_media_link_signals(
        page_url=final_url,
        html_text=html_text,
    )
    media_urls = _normalize_media_urls(
        [candidate.url for candidate in media_candidates if getattr(candidate, "url", "")]
        + direct_media_urls
    )
    motion_count = int(getattr(media_stats, "motion_count", 0))
    gif_count = int(getattr(media_stats, "gif_count", 0))
    video_count = int(getattr(media_stats, "video_count", 0))
    total_visual_count = int(getattr(media_stats, "total_visual_count", 0))
    if motion_count < MIN_MOTION_VISUALS or (gif_count < 1 and video_count < 1):
        return None, "html page does not contain required gif or video media"
    if total_visual_count > MAX_PRESENTATION_VISUALS:
        return None, "html page contains too many visuals for presentation use"
    excerpt = (assessment.full_text or assessment.best_run_text or "").strip()
    if len(excerpt) > 16000:
        excerpt = excerpt[:16000]
    if not excerpt:
        return None, "html page does not contain readable text"

    return (
        _CandidatePage(
            source_url=url,
            final_url=final_url,
            title=assessment.title or final_url,
            html_text=html_text,
            excerpt=excerpt,
            total_text_length=assessment.total_text_length,
            substantial_block_count=assessment.substantial_block_count,
            media_urls=media_urls,
            media_candidates=media_candidates,
            media_stats=media_stats,
        ),
        "",
    )


def _materialize_candidate_page(
    *,
    page: _CandidatePage,
    resolver: ContentResolver,
    output_dir: str,
    question: str,
) -> ResolvedContent:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    localized_media_candidates = resolver._download_media_candidates(
        media_candidates=page.media_candidates,
        output_root=output_root,
    )
    assessment = resolver._assess_html(page.html_text)
    html_path = resolver._save_html(
        html_text=page.html_text,
        title=page.title,
        final_url=page.final_url,
        output_root=output_root,
    )
    markdown_text = resolver._build_presentation_ready_markdown(
        html_text=page.html_text,
        page_url=page.final_url,
        assessment=assessment,
        media_candidates=localized_media_candidates,
        output_root=output_root,
    )
    markdown_text = _cleanup_markdown_prefix_with_llm(
        markdown_text=markdown_text,
        resolver=resolver,
    )
    markdown_text = _cleanup_markdown_suffix_with_llm(
        markdown_text=markdown_text,
        resolver=resolver,
    )
    source_md_path = resolver._save_source_markdown(
        title=page.title,
        text=markdown_text,
        output_root=output_root,
    )
    motion_url_count = sum(
        1
        for url in page.media_urls
        if any(token in url.lower() for token in (".gif", ".mp4", ".webm", ".mov", "youtube.com", "youtu.be", "vimeo.com"))
    )
    return resolver._success(
        source_url=page.source_url,
        content_type="html",
        final_url=page.final_url,
        local_path=str(html_path),
        document_path=str(source_md_path),
        text_length=len(markdown_text),
        substantial_block_count=page.substantial_block_count,
        extraction_method="direct",
        goal=question,
        summary_hint="",
        total_text_length=page.total_text_length,
        has_explanatory_motion_media=(page.media_stats.motion_count + motion_url_count) > 0,
        has_complete_content=False,
        has_static_visual_media=page.media_stats.static_visual_count > 0,
        has_direct_media_links=len(page.media_urls) > 0,
        presentation_fitness_score=0,
        media_candidates=localized_media_candidates,
        media_stats=page.media_stats,
        tried_urls=[page.final_url],
        external_signals={},
    )


def _score_cleaned_source_markdown(markdown_text: str) -> tuple[int, int, int, int]:
    image_paths = MARKDOWN_IMAGE_REGEX.findall(markdown_text)
    gif_count = sum(1 for path in image_paths if path.lower().endswith(".gif"))
    figure_count = sum(1 for path in image_paths if not path.lower().endswith(".gif"))
    video_count = len(MARKDOWN_VIDEO_REGEX.findall(markdown_text))
    total_visuals = gif_count + video_count + figure_count
    return total_visuals, gif_count + video_count, figure_count, len(markdown_text)


def _promote_materialized_candidate(
    result: ResolvedContent,
    target_output_dir: str,
) -> ResolvedContent:
    candidate_root = Path(result.document_path).resolve().parent
    target_root = Path(target_output_dir).resolve()
    target_root.mkdir(parents=True, exist_ok=True)

    if candidate_root != target_root:
        for item in candidate_root.iterdir():
            destination = target_root / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)

    promoted = deepcopy(result)
    if promoted.local_path:
        promoted.local_path = str(target_root / Path(promoted.local_path).name)
    if promoted.document_path:
        promoted.document_path = str(target_root / Path(promoted.document_path).name)
    for candidate in promoted.media_candidates:
        if not candidate.local_path:
            continue
        try:
            relative_path = Path(candidate.local_path).resolve().relative_to(candidate_root)
            candidate.local_path = str(target_root / relative_path)
        except ValueError:
            candidate.local_path = str(target_root / Path(candidate.local_path).name)
    return promoted


def run_deepresearch_live(
    *,
    question: str,
    deepresearch_root: str,
    output_dir: str,
    report_path: str | None = None,
    dataset_path: str | None = None,
    conda_env: str = "",
    conda_executable: str = "conda",
    max_wait_seconds: float = 600.0,
    poll_interval_seconds: float = 60.0,
    min_motion_media_count: int | None = None,
    progress_callback: Callable[[str], None] | None = None,
    terminate_runner_on_return: bool = True,
) -> ResolvedContent:
    del min_motion_media_count
    deepresearch_question = _build_deepresearch_question(question)
    root = Path(deepresearch_root).resolve()
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", question.strip().lower()).strip("_") or "query"
    report = Path(report_path) if report_path else root / f"{slug}.log"

    if dataset_path:
        dataset = Path(dataset_path)
    else:
        dataset = None
        env_path = root / ".env"
        if env_path.exists():
            for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                if raw_line.strip().startswith("DATASET="):
                    value = raw_line.split("=", 1)[1].strip().strip("\"'")
                    path = Path(value)
                    dataset = path if path.is_absolute() else (root / "inference" / path).resolve()
                    break
        if dataset is None:
            raise ValueError(f"DATASET is not configured in {env_path}")

    dataset.parent.mkdir(parents=True, exist_ok=True)
    dataset.write_text(
        json.dumps(
            {"question": deepresearch_question, "answer": ""},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    report.parent.mkdir(parents=True, exist_ok=True)
    bash_path = shutil.which("bash")
    if bash_path is None:
        raise FileNotFoundError("bash was not found in PATH")

    runner_path = root / "inference" / "run_react_infer.sh"
    if not runner_path.exists():
        raise FileNotFoundError(f"DeepResearch runner script not found: {runner_path}")

    base_command = [bash_path, str(runner_path)]
    command = (
        [conda_executable, "run", "--no-capture-output", "-n", conda_env, *base_command]
        if conda_env
        else base_command
    )
    report_handle = open(report, "a", encoding="utf-8", buffering=1)
    process = subprocess.Popen(
        command,
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=report_handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if progress_callback:
        progress_callback(f"[launch] dataset={dataset}")
        progress_callback(f"[launch] report={report}")
        progress_callback(f"[launch] pid={process.pid}")

    resolver = ContentResolver()
    tool_call_re = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)
    seen_urls: set[str] = set()
    top_pages: list[_CandidatePage] = []
    offset = 0
    partial = ""
    deadline = time.monotonic() + max(max_wait_seconds, 0.0)

    try:
        while time.monotonic() < deadline:
            if progress_callback:
                progress_callback(
                    f"[poll] seen={len(seen_urls)} best={top_pages[0].final_url if top_pages else '-'}"
                )
            if report.exists():
                with open(report, encoding="utf-8") as f:
                    f.seek(offset)
                    chunk = f.read()
                    offset = f.tell()
                if chunk:
                    buffer = partial + chunk
                    lines = buffer.splitlines(keepends=True)
                    partial = lines.pop() if lines and not lines[-1].endswith(("\n", "\r")) else ""
                    raw_text = "".join(lines)

                    for tool_call in tool_call_re.findall(raw_text):
                        try:
                            payload = json.loads(tool_call)
                        except json.JSONDecodeError:
                            continue
                        if payload.get("name") != "visit":
                            continue
                        urls = payload.get("arguments", {}).get("url", [])
                        if isinstance(urls, str):
                            urls = [urls]
                        for url in urls:
                            if not isinstance(url, str):
                                continue
                            url = url.strip()
                            if not url or url in seen_urls:
                                continue
                            seen_urls.add(url)
                            if progress_callback:
                                progress_callback(f"[resolve:start] url={url}")
                            page, error = _fetch_candidate_page(url=url, resolver=resolver)
                            if progress_callback:
                                if page is None:
                                    progress_callback(f"[resolve:done] success=False type=none text_chars=0 media_urls=0 url={url} error={error}")
                                else:
                                    progress_callback(
                                        f"[resolve:done] success=True type=html "
                                        f"text_chars={page.total_text_length} "
                                        f"media_urls={len(page.media_urls)} "
                                        f"url={page.final_url}"
                                    )
                            if page is None:
                                continue
                            previous_best_url = top_pages[0].final_url if top_pages else ""
                            top_pages = _update_top_candidate_pages(top_pages, page)
                            if progress_callback:
                                progress_callback(
                                    "[best:top3] "
                                    + ", ".join(candidate.final_url for candidate in top_pages)
                                )
                                if top_pages and top_pages[0].final_url != previous_best_url:
                                    progress_callback(f"[best:update] url={top_pages[0].final_url}")

            sleep_seconds = min(
                max(poll_interval_seconds, 0.0),
                max(deadline - time.monotonic(), 0.0),
            )
            if sleep_seconds <= 0:
                break
            if progress_callback:
                progress_callback(f"[poll] sleeping={sleep_seconds:.2f}s")
            time.sleep(sleep_seconds)
    finally:
        try:
            if terminate_runner_on_return and process.poll() is None:
                if progress_callback:
                    progress_callback("[launch] terminating_deepresearch_process")
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    if progress_callback:
                        progress_callback("[launch] killing_deepresearch_process")
                    process.kill()
                    process.wait(timeout=10)
        finally:
            if report_handle is not None:
                report_handle.close()

    if not top_pages:
        return ResolvedContent(
            source_url="",
            success=False,
            error="no valid html content found before timeout",
        )
    candidate_results: list[tuple[ResolvedContent, tuple[int, int, int, int], _CandidatePage]] = []
    candidates_root = Path(output_dir).resolve() / "candidates"
    for candidate_idx, page in enumerate(top_pages, start=1):
        candidate_output_dir = candidates_root / f"candidate_{candidate_idx}"
        result = _materialize_candidate_page(
            page=page,
            resolver=resolver,
            output_dir=str(candidate_output_dir),
            question=question,
        )
        markdown_text = Path(result.document_path).read_text(encoding="utf-8")
        source_score = _score_cleaned_source_markdown(markdown_text)
        candidate_results.append((result, source_score, page))
        if progress_callback:
            progress_callback(
                f"[source:score] url={page.final_url} "
                f"total={source_score[0]} motion={source_score[1]} figure={source_score[2]}"
            )

    selected_result, selected_score, selected_page = max(
        candidate_results,
        key=lambda item: (item[1], _candidate_rank_key(item[2])),
    )
    if progress_callback:
        progress_callback(
            f"[source:pick] url={selected_page.final_url} "
            f"total={selected_score[0]} motion={selected_score[1]} figure={selected_score[2]}"
        )
    return _promote_materialized_candidate(selected_result, output_dir)
