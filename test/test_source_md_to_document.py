from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from pptagent.agent import AsyncAgent
from pptagent.document import Document
from pptagent.document import document as document_module
from pptagent.document.element import Media, Table, Video
from pptagent.model_utils import ModelManager


MARKDOWN_IMAGE_REGEX = re.compile(r"!\[.*?\]\((.*?)\)", re.DOTALL)
HTML_VIDEO_REGEX = re.compile(
    r'<video[^>]*src=["\']([^"\']+)["\'][^>]*>(?:.*?</video>)?',
    re.IGNORECASE | re.DOTALL,
)
DEBUG_DIR: Path | None = None
DEBUG_COUNTER = 0


def _configure_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def _write_debug_file(name: str, content: str) -> None:
    if DEBUG_DIR is None:
        return
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (DEBUG_DIR / name).write_text(content, encoding="utf-8")


def _install_llm_io_logging() -> None:
    original_from_markdown_async = Document.from_markdown_async.__func__
    original_async_agent_call = AsyncAgent.__call__

    async def from_markdown_async_with_debug(
        cls,
        markdown_content,
        language_model,
        vision_model,
        image_dir,
        table_model=None,
    ):
        headings = re.findall(r"^#+\s+.*", markdown_content, re.MULTILINE)
        heading_context = document_module.build_heading_context(
            markdown_content, headings
        )
        heading_prompt = document_module.HEADING_EXTRACT_PROMPT.render(
            heading_context=heading_context
        )
        _write_debug_file("llm_input_heading_extract.txt", heading_prompt)
        adjusted_headings = await language_model(heading_prompt, return_json=True)
        _write_debug_file(
            "llm_output_heading_extract.json",
            json.dumps(adjusted_headings, ensure_ascii=False, indent=2),
        )
        return await original_from_markdown_async(
            cls,
            markdown_content,
            language_model,
            vision_model,
            image_dir,
            table_model,
        )

    async def async_agent_call_with_debug(self, *args, **kwargs):
        global DEBUG_COUNTER
        turn_id, result = await original_async_agent_call(self, *args, **kwargs)
        if self.name == "doc_extractor" and self.history:
            turn = self.history[-1]
            suffix = f"{self.name}_{DEBUG_COUNTER:03d}_turn_{turn.id:03d}"
            DEBUG_COUNTER += 1
            _write_debug_file(
                f"llm_input_{suffix}.txt",
                f"System:\n{self.system_message}\n\nPrompt:\n{turn.prompt}",
            )
            _write_debug_file(
                f"llm_output_{suffix}.json",
                json.dumps(result, ensure_ascii=False, indent=2)
                if isinstance(result, (dict, list))
                else str(result),
            )
        return turn_id, result

    Document.from_markdown_async = classmethod(from_markdown_async_with_debug)
    AsyncAgent.__call__ = async_agent_call_with_debug


def _install_progress_logging() -> None:
    original_media_parse_async = Media.parse_async
    original_media_caption_async = Media.get_caption_async
    original_video_parse_async = Video.parse_async
    original_video_caption_async = Video.get_caption_async
    original_table_parse_async = Table.parse_async
    original_table_caption_async = Table.get_caption_async

    async def media_parse_async(self, language_model, image_dir: str):
        raw_path = ""
        match = MARKDOWN_IMAGE_REGEX.search(self.markdown_content)
        if match is not None:
            raw_path = match.group(1)
        print(f"[image:parse] path={raw_path or '<unknown>'}")
        return await original_media_parse_async(self, language_model, image_dir)

    async def media_caption_async(self, vision_model):
        print(f"[image:caption:start] path={self.path}")
        result = await original_media_caption_async(self, vision_model)
        print(f"[image:caption:done] path={self.path} caption={self.caption}")
        return result

    async def video_parse_async(self, language_model, image_dir: str):
        raw_path = ""
        match = HTML_VIDEO_REGEX.search(self.markdown_content)
        if match is not None:
            raw_path = match.group(1)
        print(f"[video:parse] path={raw_path or '<unknown>'}")
        return await original_video_parse_async(self, language_model, image_dir)

    async def video_caption_async(self, vision_model):
        print(f"[video:caption:start] path={self.path}")
        result = await original_video_caption_async(self, vision_model)
        print(f"[video:caption:done] path={self.path} caption={self.caption}")
        return result

    async def table_parse_async(self, table_model, image_dir: str):
        print("[table:parse]")
        return await original_table_parse_async(self, table_model, image_dir)

    async def table_caption_async(self, language_model):
        print("[table:caption:start]")
        result = await original_table_caption_async(self, language_model)
        print(f"[table:caption:done] caption={self.caption}")
        return result

    Media.parse_async = media_parse_async
    Media.get_caption_async = media_caption_async
    Video.parse_async = video_parse_async
    Video.get_caption_async = video_caption_async
    Table.parse_async = table_parse_async
    Table.get_caption_async = table_caption_async


async def _build_document(source_md_path: Path, output_dir: Path) -> None:
    global DEBUG_DIR
    DEBUG_DIR = output_dir / "llm_debug"
    models = ModelManager()
    markdown_text = source_md_path.read_text(encoding="utf-8")
    image_refs = MARKDOWN_IMAGE_REGEX.findall(markdown_text)
    video_refs = HTML_VIDEO_REGEX.findall(markdown_text)
    table_count = markdown_text.count("\n|")
    print(
        f"[source-md] images={len(image_refs)} videos={len(video_refs)} tables={table_count}"
    )
    if image_refs:
        print(f"[source-md] first_image={image_refs[0]}")
    if video_refs:
        print(f"[source-md] first_video={video_refs[0]}")
    _install_llm_io_logging()
    _install_progress_logging()
    source_doc = await Document.from_markdown_async(
        markdown_text,
        models.language_model,
        models.vision_model,
        str(source_md_path.parent),
    )

    refined_doc_path = output_dir / "refined_doc.json"
    refined_doc_path.write_text(
        json.dumps(source_doc.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    overview_path = output_dir / "document_overview.txt"
    overview_path.write_text(
        source_doc.get_overview(include_summary=True),
        encoding="utf-8",
    )

    media_summary = {
        "image_count": sum(1 for _ in source_doc.iter_medias("image")),
        "video_count": sum(1 for _ in source_doc.iter_medias("video")),
        "table_count": sum(1 for _ in source_doc.iter_medias("table")),
        "metadata": source_doc.metadata,
    }
    (output_dir / "media_summary.json").write_text(
        json.dumps(media_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("success: True")
    print(f"source_md: {source_md_path}")
    print(f"refined_doc: {refined_doc_path}")
    print(f"overview: {overview_path}")
    print(f"media_summary: {output_dir / 'media_summary.json'}")


def main() -> None:
    _configure_stdio()
    parser = argparse.ArgumentParser(
        description="Build refined_doc.json and document debug outputs from source.md."
    )
    parser.add_argument("--source-md", required=True, help="Path to source.md")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to save refined_doc.json and document debug outputs",
    )
    args = parser.parse_args()

    source_md_path = Path(args.source_md).resolve()
    if not source_md_path.exists():
        raise SystemExit(f"source.md not found: {source_md_path}")

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(_build_document(source_md_path, output_dir))


if __name__ == "__main__":
    main()
