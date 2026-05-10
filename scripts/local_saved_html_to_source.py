from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from pathlib import Path
from urllib.parse import unquote
from urllib.request import urlopen

from bs4 import BeautifulSoup
from markdownify import markdownify as md


MEDIA_EXTS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".mp4",
    ".webm",
    ".mov",
    ".m4v",
}


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip() + "\n"


def safe_name(name: str) -> str:
    name = unquote(name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
    return name[:180] or "asset"


def copy_local_assets(companion_dir: Path, assets_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not companion_dir.exists():
        return mapping
    ensure_dir(assets_dir)
    for src in companion_dir.rglob("*"):
        if not src.is_file():
            continue
        if src.suffix.lower() not in MEDIA_EXTS:
            continue
        digest = hashlib.md5(str(src.relative_to(companion_dir)).encode("utf-8")).hexdigest()[:8]
        dst_name = f"{digest}_{safe_name(src.name)}"
        dst = assets_dir / dst_name
        shutil.copy2(src, dst)
        rel_key = src.relative_to(companion_dir).as_posix()
        mapping[rel_key] = f"assets/{dst_name}"
    return mapping


def rewrite_local_refs(html_text: str, companion_dir_name: str, asset_map: dict[str, str]) -> str:
    updated = html_text
    for rel_key, new_rel in sorted(asset_map.items(), key=lambda item: len(item[0]), reverse=True):
        updated = updated.replace(f"{companion_dir_name}/{rel_key}", new_rel)
        updated = updated.replace(f"./{companion_dir_name}/{rel_key}", new_rel)
        updated = updated.replace(rel_key, new_rel)
    return updated


def download_remote_media_assets(html_text: str, assets_dir: Path) -> dict[str, str]:
    mapping: dict[str, str] = {}
    ensure_dir(assets_dir)
    urls = re.findall(r'https?://[^\s"\']+?\.(?:mp4|webm|mov|m4v|png|jpg|jpeg|gif|webp|svg)', html_text, flags=re.IGNORECASE)
    for url in urls:
        if url in mapping:
            continue
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in MEDIA_EXTS:
            continue
        digest = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
        dst_name = f"{digest}_{safe_name(Path(url.split('?', 1)[0]).name)}"
        dst = assets_dir / dst_name
        if not dst.exists():
            with urlopen(url) as response, dst.open("wb") as f:
                shutil.copyfileobj(response, f)
        mapping[url] = f"assets/{dst_name}"
    return mapping


def extract_video_refs(html_text: str) -> list[str]:
    matches = re.findall(
        r'<source[^>]+src=["\']([^"\']+)["\'][^>]*>|<video[^>]+src=["\']([^"\']+)["\'][^>]*>',
        html_text,
        flags=re.IGNORECASE,
    )
    refs: list[str] = []
    seen: set[str] = set()
    for source_src, video_src in matches:
        src = source_src or video_src
        if not src or src in seen:
            continue
        seen.add(src)
        refs.append(src)
    return refs


def normalize_markdown(markdown: str, title: str | None, video_refs: list[str] | None = None) -> str:
    markdown = markdown.replace("\xa0", " ")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = re.sub(r"!\[(.*?)\]\((?!assets/)(.*?)\)", "", markdown)
    if title:
        markdown = f"# {title}\n\n{markdown.lstrip()}"
    if video_refs:
        video_lines = ["## Videos", ""]
        for ref in video_refs:
            video_lines.append(f'<video src="{ref}"></video>')
            video_lines.append("")
        markdown = markdown.rstrip() + "\n\n" + "\n".join(video_lines)
    return clean_text(markdown)


def extract_title(soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:title"})
    if og and og.get("content"):
        return og["content"].strip()
    if soup.title and soup.title.string:
        return soup.title.string.strip()
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    return "Untitled"


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a browser-saved local HTML page into source.md without LLM cleanup.")
    parser.add_argument("--html-file", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--title", default="")
    args = parser.parse_args()

    html_file = Path(args.html_file).resolve()
    output_dir = Path(args.output_dir).resolve()
    ensure_dir(output_dir)
    assets_dir = output_dir / "assets"

    companion_dir = html_file.with_name(f"{html_file.stem}_files")
    asset_map = copy_local_assets(companion_dir, assets_dir)

    raw_html = html_file.read_text(encoding="utf-8", errors="replace")
    remote_asset_map = download_remote_media_assets(raw_html, assets_dir)
    combined_asset_map = {**asset_map, **remote_asset_map}
    rewritten_html = rewrite_local_refs(raw_html, companion_dir.name, combined_asset_map)
    for original_url, new_rel in remote_asset_map.items():
        rewritten_html = rewritten_html.replace(original_url, new_rel)
    saved_html_name = safe_name(html_file.stem) + ".html"
    (output_dir / saved_html_name).write_text(rewritten_html, encoding="utf-8")

    soup = BeautifulSoup(rewritten_html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = args.title.strip() or extract_title(soup)
    body = soup.body or soup
    video_refs = extract_video_refs(rewritten_html)
    for new_rel in remote_asset_map.values():
        if Path(new_rel).suffix.lower() in {".mp4", ".webm", ".mov", ".m4v"} and new_rel not in video_refs:
            video_refs.append(new_rel)

    markdown = md(
        str(body),
        heading_style="ATX",
        bullets="-",
        strip=["script", "style", "noscript"],
    )
    markdown = normalize_markdown(markdown, title, video_refs)

    (output_dir / "source_before_cleanup.md").write_text(markdown, encoding="utf-8")
    (output_dir / "source_after_prefix_cleanup.md").write_text(markdown, encoding="utf-8")
    (output_dir / "source_after_cleanup.md").write_text(markdown, encoding="utf-8")
    (output_dir / "source.md").write_text(markdown, encoding="utf-8")
    (output_dir / "llm_input_prompt_prefix.txt").write_text("", encoding="utf-8")
    (output_dir / "llm_input_prompt_suffix.txt").write_text("", encoding="utf-8")
    (output_dir / "llm_raw_output_prefix.txt").write_text("", encoding="utf-8")
    (output_dir / "llm_raw_output_suffix.txt").write_text("", encoding="utf-8")
    (output_dir / "llm_output_prefix.json").write_text('{"delete_ranges":[]}', encoding="utf-8")
    (output_dir / "llm_output_suffix.json").write_text('{"delete_ranges":[]}', encoding="utf-8")

    print(output_dir / "source.md")


if __name__ == "__main__":
    main()
