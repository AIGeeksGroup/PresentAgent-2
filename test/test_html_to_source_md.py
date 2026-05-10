from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
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


def _bootstrap_modules():
    """
    Load only the modules needed for the direct single-HTML -> source.md test.
    This script does NOT call ContentResolver.resolve() and does NOT traverse child links.
    """
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

    content_resolver = _load_module(
        "pptagent.research.content_resolver",
        ROOT / "pptagent" / "research" / "content_resolver" / "__init__.py",
        ROOT / "pptagent" / "research" / "content_resolver",
    )
    live_runner = _load_module(
        "pptagent.research.live_runner",
        ROOT / "pptagent" / "research" / "live_runner.py",
    )
    return content_resolver, live_runner


def _build_resolver(content_resolver_module):
    return content_resolver_module.ContentResolver(
        llm_enabled=os.getenv("PRESENTAGENT_CONTENT_RESOLVER_USE_LLM", "1") != "0",
        llm_model=os.getenv("LANGUAGE_MODEL") or os.getenv("SUMMARY_MODEL_NAME"),
        llm_base_url=os.getenv("API_BASE"),
        llm_api_key=os.getenv("OPENAI_API_KEY") or os.getenv("API_KEY"),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Directly test single HTML -> source.md with mandatory LLM cleanup and no child-link traversal."
    )
    parser.add_argument("--url", required=True, help="Target HTML page URL")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to save html, source.md, assets, and cleanup debug files",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=180.0,
        help="Timeout in seconds for the cleanup LLM call",
    )
    parser.add_argument(
        "--llm-retries",
        type=int,
        default=3,
        help="Number of retries for the cleanup LLM call",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    content_resolver_module, live_runner = _bootstrap_modules()
    resolver = _build_resolver(content_resolver_module)

    response = resolver.session.get(args.url, timeout=resolver.timeout, allow_redirects=True)
    response.raise_for_status()

    final_url = resolver._normalize_url(response.url)
    if not resolver._is_html(response):
        raise SystemExit(
            f"URL did not return HTML. content-type={resolver._content_type(response) or 'unknown'}"
        )

    html_text = response.text or ""
    if not html_text.strip():
        raise SystemExit("Fetched HTML is empty.")

    html_path = resolver._save_html(
        html_text=html_text,
        title=final_url,
        final_url=final_url,
        output_root=output_dir,
    )

    assessment = resolver._assess_html(html_text)
    media_candidates, media_stats = resolver._extract_media_candidates(
        page_url=final_url,
        html_text=html_text,
    )
    localized_media_candidates = resolver._download_media_candidates(
        media_candidates=media_candidates,
        output_root=output_dir,
    )

    markdown_before = resolver._build_presentation_ready_markdown(
        html_text=html_text,
        page_url=final_url,
        assessment=assessment,
        media_candidates=localized_media_candidates,
        output_root=output_dir,
    )

    if not resolver.llm_model or not resolver.llm_api_key:
        raise SystemExit(
            "LLM cleanup is mandatory for this script. Please set LANGUAGE_MODEL or SUMMARY_MODEL_NAME, "
            "and API_KEY/OPENAI_API_KEY plus API_BASE."
        )

    excerpt = live_runner._build_markdown_excerpt(markdown_before)
    prefix_prompt = live_runner.HTML_MARKDOWN_PREFIX_CLEANUP_PROMPT.format(
        markdown_excerpt=excerpt,
    )
    (output_dir / "source_before_cleanup.md").write_text(markdown_before, encoding="utf-8")
    (output_dir / "llm_input_prompt_prefix.txt").write_text(prefix_prompt, encoding="utf-8")

    from openai import OpenAI

    client = OpenAI(
        base_url=resolver.llm_base_url,
        api_key=resolver.llm_api_key,
        timeout=max(float(resolver.timeout), float(args.llm_timeout)),
        max_retries=0,
    )

    def run_cleanup(prompt_text: str) -> tuple[str, dict]:
        last_error = None
        completion = None
        for attempt in range(max(args.llm_retries, 1)):
            try:
                completion = client.chat.completions.create(
                    model=resolver.llm_model,
                    messages=[
                        {
                            "role": "system",
                            "content": "You identify noisy markdown line ranges copied from webpage HTML.",
                        },
                        {"role": "user", "content": prompt_text},
                    ],
                    temperature=0,
                )
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= max(args.llm_retries, 1):
                    raise
                time.sleep(min(2 * (attempt + 1), 8))
        if completion is None:
            raise RuntimeError(f"Cleanup LLM call failed: {last_error}")
        raw = (completion.choices[0].message.content or "").strip()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            left = raw.find("{")
            right = raw.rfind("}")
            if left == -1 or right == -1 or left > right:
                raise RuntimeError("LLM raw output is not valid JSON")
            parsed = json.loads(raw[left : right + 1])
        return raw, parsed

    prefix_raw_content, prefix_decision = run_cleanup(prefix_prompt)
    (output_dir / "llm_raw_output_prefix.txt").write_text(prefix_raw_content, encoding="utf-8")
    (output_dir / "llm_output_prefix.json").write_text(
        json.dumps(prefix_decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    markdown_after_prefix = live_runner._apply_delete_ranges(
        markdown_before, prefix_decision.get("delete_ranges", [])
    )
    (output_dir / "source_after_prefix_cleanup.md").write_text(
        markdown_after_prefix, encoding="utf-8"
    )

    tail_excerpt = live_runner._build_markdown_tail_excerpt(markdown_after_prefix)
    head_excerpt = live_runner._build_markdown_head_excerpt(markdown_after_prefix)
    if live_runner._suffix_cleanup_should_skip(markdown_after_prefix):
        suffix_prompt = ""
        suffix_raw_content = ""
        suffix_decision = {"delete_ranges": []}
        markdown_after = markdown_after_prefix
    else:
        suffix_prompt = live_runner.HTML_MARKDOWN_SUFFIX_CLEANUP_PROMPT.format(
            markdown_head_excerpt=head_excerpt,
            markdown_tail_excerpt=tail_excerpt,
        )
        suffix_raw_content, suffix_decision = run_cleanup(suffix_prompt)
        markdown_after = live_runner._apply_delete_ranges(
            markdown_after_prefix, suffix_decision.get("delete_ranges", [])
        )
    (output_dir / "llm_input_prompt_suffix.txt").write_text(suffix_prompt, encoding="utf-8")
    (output_dir / "llm_raw_output_suffix.txt").write_text(suffix_raw_content, encoding="utf-8")
    (output_dir / "llm_output_suffix.json").write_text(
        json.dumps(suffix_decision, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "source_after_cleanup.md").write_text(markdown_after, encoding="utf-8")

    source_md_path = resolver._save_source_markdown(
        title=assessment.title or final_url,
        text=markdown_after,
        output_root=output_dir,
    )

    print("success: True")
    print(f"final_url: {final_url}")
    print(f"html_path: {html_path}")
    print(f"document_path: {source_md_path}")
    print(f"media_candidates: {len(localized_media_candidates)}")
    print(f"motion_media: {media_stats.motion_count > 0}")
    print("\nsource.md saved to:")
    print(source_md_path)
    print("\nCleanup debug files:")
    for name in [
        "source_before_cleanup.md",
        "llm_input_prompt_prefix.txt",
        "llm_raw_output_prefix.txt",
        "llm_output_prefix.json",
        "source_after_prefix_cleanup.md",
        "llm_input_prompt_suffix.txt",
        "llm_raw_output_suffix.txt",
        "llm_output_suffix.json",
        "source_after_cleanup.md",
    ]:
        print(output_dir / name)


if __name__ == "__main__":
    main()
