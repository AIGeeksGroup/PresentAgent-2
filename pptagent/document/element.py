import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any, Optional, abstractmethod

from bs4 import BeautifulSoup
from jinja2 import Environment, StrictUndefined
from mistune import html as markdown
from PIL import Image, UnidentifiedImageError

from pptagent.llms import LLM, AsyncLLM
from pptagent.utils import (
    edit_distance,
    get_logger,
    markdown_table_to_image,
    package_join,
    pbasename,
    pexists,
    pjoin,
)

env = Environment(undefined=StrictUndefined)

IMAGE_PARSING_REGEX = re.compile(r"!\[.*?\]\((.*?)\)", re.DOTALL)
VIDEO_PARSING_REGEX = re.compile(
    r'<video[^>]*src=["\']([^"\']+)["\'][^>]*>(?:.*?</video>)?',
    re.IGNORECASE | re.DOTALL,
)
TABLE_PARSING_PROMPT = env.from_string(
    open(package_join("prompts", "table_parsing.txt")).read()
)
TABLE_CAPTION_PROMPT = env.from_string(
    open(package_join("prompts", "markdown_table_caption.txt")).read()
)
IMAGE_CAPTION_PROMPT = env.from_string(
    open(package_join("prompts", "markdown_image_caption.txt")).read()
)

logger = get_logger(__name__)
CAPTION_IMAGE_MAX_BYTES = int(os.environ.get("CAPTION_IMAGE_MAX_BYTES", "512000"))
CAPTION_IMAGE_MAX_SIDE = int(os.environ.get("CAPTION_IMAGE_MAX_SIDE", "1280"))
CAPTION_IMAGE_QUALITY = int(os.environ.get("CAPTION_IMAGE_QUALITY", "85"))


def _build_gif_caption_prompt(near_chunks: tuple[str, str]) -> str:
    base_prompt = IMAGE_CAPTION_PROMPT.render(markdown_caption=near_chunks).strip()
    return (
        base_prompt
        + "\n\nThe provided images are three frames from the same GIF animation, in temporal order: first frame, middle frame, last frame. "
        + "Write one unified caption for the original GIF animation itself, not three separate captions for the frames. "
        + "Describe the overall animated behavior across time while keeping the same output format as a normal single-image caption."
    )


def _extract_gif_summary_frames(gif_path: str) -> list[str]:
    extracted_paths: list[str] = []
    with Image.open(gif_path) as gif:
        frame_count = getattr(gif, "n_frames", 1)
        frame_indexes = sorted({0, max(frame_count // 2, 0), max(frame_count - 1, 0)})
        for frame_index in frame_indexes:
            gif.seek(frame_index)
            frame = gif.copy().convert("RGB")
            with tempfile.NamedTemporaryFile(
                prefix="pptagent_gif_frame_",
                suffix=".png",
                delete=False,
            ) as tmp:
                frame.save(tmp.name, format="PNG")
                extracted_paths.append(tmp.name)
    return extracted_paths


def _prepare_caption_image(image_path: str) -> tuple[str, Optional[str]]:
    if os.path.getsize(image_path) <= CAPTION_IMAGE_MAX_BYTES:
        return image_path, None

    with Image.open(image_path) as image:
        frame = image.convert("RGB")
        frame.thumbnail(
            (CAPTION_IMAGE_MAX_SIDE, CAPTION_IMAGE_MAX_SIDE),
            Image.Resampling.LANCZOS,
        )
        with tempfile.NamedTemporaryFile(
            prefix="pptagent_caption_image_",
            suffix=".jpg",
            delete=False,
        ) as tmp:
            frame.save(tmp.name, format="JPEG", quality=CAPTION_IMAGE_QUALITY)
            return tmp.name, tmp.name


def _cleanup_temp_files(paths: list[str]) -> None:
    for path in paths:
        try:
            os.remove(path)
        except OSError:
            continue


def _resolve_media_path(media_path: str, image_dir: str) -> str:
    candidates = [
        media_path,
        pjoin(image_dir, media_path),
        pjoin(image_dir, pbasename(media_path)),
        pjoin(image_dir, "assets", pbasename(media_path)),
    ]
    for candidate in candidates:
        if pexists(candidate):
            return candidate
    raise AssertionError(f"media file not found: {media_path}")


def _is_gif_path(path: Optional[str]) -> bool:
    return bool(path and re.search(r"\.gif(?:[\?#\)]|$)", path.lower()))


@dataclass
class Media:
    markdown_content: str
    near_chunks: tuple[str, str]
    path: Optional[str] = None
    caption: Optional[str] = None
    media_type: str = "image"

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        assert (
            "markdown_content" in data and "near_chunks" in data
        ), f"'markdown_content' and 'near_chunks' keys are required in data dictionary but were not found. Input keys: {list(data.keys())}"
        path = data.get("path", None)
        markdown_content = data["markdown_content"]
        media_type = data.get("media_type", data.get("type", "image"))
        if _is_gif_path(path) or _is_gif_path(markdown_content):
            media_type = "video"
        return cls(
            markdown_content=markdown_content,
            near_chunks=data["near_chunks"],
            path=path,
            caption=data.get("caption", None),
            media_type=media_type,
        )

    @property
    def size(self):
        assert self.path is not None, "Path is required to get size"
        return Image.open(self.path).size

    @abstractmethod
    def parse(self, _: Optional[LLM], image_dir: str):
        """
        Parse the markdown content to extract image path and alt text.
        Format expected: ![alt text](image.png)
        """
        match = IMAGE_PARSING_REGEX.search(self.markdown_content)
        if match is None:
            raise ValueError("No image found in the markdown content")
        image_path = match.group(1)
        self.path = _resolve_media_path(image_path, image_dir)
        if _is_gif_path(self.path):
            self.media_type = "video"

    async def parse_async(self, language_model: Optional[AsyncLLM], image_dir: str):
        self.parse(language_model, image_dir)

    def get_caption(self, vision_model: LLM):
        assert self.path is not None, "Path is required to get caption"
        if self.caption is None:
            if self.path.lower().endswith(".gif"):
                frame_paths = _extract_gif_summary_frames(self.path)
                try:
                    self.caption = vision_model(
                        _build_gif_caption_prompt(self.near_chunks),
                        frame_paths,
                    )
                finally:
                    _cleanup_temp_files(frame_paths)
            else:
                caption_path, temp_path = _prepare_caption_image(self.path)
                try:
                    self.caption = vision_model(
                        IMAGE_CAPTION_PROMPT.render(
                            markdown_caption=self.near_chunks,
                        ),
                        caption_path,
                    )
                finally:
                    if temp_path is not None:
                        _cleanup_temp_files([temp_path])
            logger.debug(f"Caption: {self.caption}")

    async def get_caption_async(self, vision_model: AsyncLLM):
        assert self.path is not None, "Path is required to get caption"
        if self.caption is None:
            if self.path.lower().endswith(".gif"):
                frame_paths = _extract_gif_summary_frames(self.path)
                try:
                    self.caption = await vision_model(
                        _build_gif_caption_prompt(self.near_chunks),
                        frame_paths,
                    )
                finally:
                    _cleanup_temp_files(frame_paths)
            else:
                try:
                    caption_path, temp_path = _prepare_caption_image(self.path)
                    self.caption = await vision_model(
                        IMAGE_CAPTION_PROMPT.render(
                            markdown_caption=self.near_chunks,
                        ),
                        caption_path,
                    )
                except Exception as exc:
                    # Some crawled assets are corrupt, mislabeled, SVG-backed, or
                    # otherwise rejected by the vision backend. Keep the document
                    # pipeline moving with a deterministic fallback caption.
                    message = str(exc).lower()
                    recoverable = isinstance(exc, UnidentifiedImageError) or any(
                        token in message
                        for token in (
                            "image format is illegal",
                            "cannot be opened",
                            "unsupported image",
                            "invalid image",
                            "cannot identify image file",
                        )
                    )
                    if not recoverable:
                        raise
                    nearby = "\n".join(
                        chunk.strip() for chunk in self.near_chunks if chunk.strip()
                    ).strip()
                    fallback = pbasename(self.path)
                    self.caption = (nearby[:512] if nearby else fallback) or "Image"
                finally:
                    if "temp_path" in locals() and temp_path is not None:
                        _cleanup_temp_files([temp_path])
            logger.debug(f"Caption: {self.caption}")


@dataclass
class Video(Media):
    media_type: str = "video"

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        return cls(
            markdown_content=data["markdown_content"],
            near_chunks=data["near_chunks"],
            path=data.get("path", None),
            caption=data.get("caption", None),
        )

    @property
    def size(self):
        raise NotImplementedError("Video size is not supported by the image backend")

    def parse(self, _: Optional[LLM], image_dir: str):
        match = VIDEO_PARSING_REGEX.search(self.markdown_content)
        if match is None:
            raise ValueError("No video found in the markdown content")
        self.path = _resolve_media_path(match.group(1), image_dir)

    async def parse_async(self, language_model: Optional[AsyncLLM], image_dir: str):
        self.parse(language_model, image_dir)

    def _fallback_caption(self) -> str:
        nearby = "\n".join(chunk.strip() for chunk in self.near_chunks if chunk.strip())
        if nearby:
            return nearby[:512]
        if self.path is not None:
            return pbasename(self.path)
        return "Video"

    def get_caption(self, _: LLM):
        if self.caption is None:
            self.caption = self._fallback_caption()

    async def get_caption_async(self, _: AsyncLLM):
        if self.caption is None:
            self.caption = self._fallback_caption()


@dataclass
class Table(Media):
    cells: Optional[list[list[str]]] = None
    merge_area: Optional[list[tuple[int, int, int, int]]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        assert (
            "markdown_content" in data and "near_chunks" in data
        ), f"'markdown_content' and 'near_chunks' keys are required in data dictionary but were not found. Input keys: {list(data.keys())}"
        return cls(
            markdown_content=data["markdown_content"],
            near_chunks=data["near_chunks"],
            path=data.get("path", None),
            caption=data.get("caption", None),
            cells=data.get("cells", None),
            merge_area=data.get("merge_area", None),
        )

    def parse_table(self, image_dir: str):
        html = markdown(self.markdown_content)
        soup = BeautifulSoup(html, "html.parser")
        table = soup.find("table")
        self.cells = []
        for row in table.find_all("tr"):
            self.cells.append(
                [cell.text for cell in row.find_all("td") + row.find_all("th")]
            )
        for i in range(len(self.cells)):
            row = self.cells[i]
            unstacked = row[0].split("\n")
            if len(unstacked) == len(row) and all(
                cell.strip() == "" for cell in row[1:]
            ):
                self.cells[i] = unstacked

        if self.path is None:
            self.path = pjoin(
                image_dir,
                f"table_{hashlib.md5(str(self.cells).encode()).hexdigest()[:4]}.png",
            )
        markdown_table_to_image(self.markdown_content, self.path)

    def parse(self, table_model: Optional[LLM], image_dir: str):
        self.parse_table(image_dir)
        if table_model is None:
            return
        result = table_model(
            TABLE_PARSING_PROMPT.render(cells=self.cells, caption=self.caption),
            return_json=True,
        )
        self.merge_area = result["merge_area"]
        table = [row for row in result["table_data"]]
        if (
            all(len(row) == len(table[0]) for row in table)
            and len(table) == len(self.cells)
            and len(table[0]) == len(self.cells[0])
        ):
            self.cells = table

    async def parse_async(self, table_model: Optional[AsyncLLM], image_dir: str):
        self.parse_table(image_dir)
        if table_model is None:
            return
        result = await table_model(
            TABLE_PARSING_PROMPT.render(cells=self.cells, caption=self.caption),
            return_json=True,
        )
        self.merge_area = result["merge_area"]
        table = [row for row in result["table_data"]]
        if (
            all(len(row) == len(table[0]) for row in table)
            and len(table) == len(self.cells)
            and len(table[0]) == len(self.cells[0])
        ):
            self.cells = table

    def get_caption(self, language_model: LLM):
        if self.caption is None:
            self.caption = language_model(
                TABLE_CAPTION_PROMPT.render(
                    markdown_content=self.markdown_content,
                    markdown_caption=self.near_chunks,
                )
            )
            logger.debug(f"Caption: {self.caption}")

    async def get_caption_async(self, language_model: AsyncLLM):
        if self.caption is None:
            self.caption = await language_model(
                TABLE_CAPTION_PROMPT.render(
                    markdown_content=self.markdown_content,
                    markdown_caption=self.near_chunks,
                )
            )
            logger.debug(f"Caption: {self.caption}")


@dataclass
class SubSection:
    title: str
    content: str
    medias: list[Media]

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        assert (
            "title" in data and "content" in data
        ), f"'title' and 'content' keys are required in data dictionary but were not found. Input keys: {list(data.keys())}"
        medias = []
        for chunk in data.get("medias", []):
            if (
                chunk.get("type", None) == "table"
                or chunk.get("cells", None) is not None
            ):
                medias.append(Table.from_dict(chunk))
            elif chunk.get("type", None) == "video":
                medias.append(Video.from_dict(chunk))
            else:
                medias.append(Media.from_dict(chunk))
        return cls(
            title=data["title"],
            content=data["content"],
            medias=medias,
        )

    def iter_medias(self):
        yield from self.medias


@dataclass
class Section:
    title: str
    summary: Optional[str]
    subsections: list[SubSection]
    markdown_content: str

    @classmethod
    def from_dict(cls, data: dict[str, Any], markdown_content: str = None):
        assert (
            "title" in data and "subsections" in data
        ), f"'title' and 'subsections' keys are required in data dictionary but were not found. Input keys: {list(data.keys())}"
        subsections = [
            SubSection.from_dict(subsection) for subsection in data["subsections"]
        ]
        assert len(subsections) != 0, "subsections is empty"
        return cls(
            title=data["title"],
            subsections=subsections,
            summary=data.get("summary", None),
            markdown_content=data.get("markdown_content", markdown_content),
        )

    def __contains__(self, key: str):
        for subsection in self.subsections:
            if subsection.title == key:
                return True
        return False

    def __getitem__(self, key: str):
        for subsection in self.subsections:
            if subsection.title == key:
                return subsection
        sim_subsec = max(self.subsections, key=lambda x: edit_distance(x.title, key))
        if edit_distance(sim_subsec.title, key) > 0.8:
            return sim_subsec
        raise KeyError(
            f"subsection not found: {key}, available subsections of {self.title} are: {[subsection.title for subsection in self.subsections]}"
        )

    def iter_medias(self):
        for subsection in self.subsections:
            yield from subsection.iter_medias()

    def validate_medias(self, image_dir: str, require_caption: bool = True):
        for media in self.iter_medias():
            media.path = _resolve_media_path(media.path, image_dir)
            assert (
                media.caption is not None or not require_caption
            ), f"caption is required for media: {media.path}"


def link_medias(
    medias: list[dict],
    rewritten_paragraphs: list[dict[str, Any]],
    max_chunk_size: int = 256,
) -> dict[str, Any]:
    """
    Link media elements to the most relevant paragraphs based on content proximity.

    Args:
        medias: List of media dictionaries (tables, images)
        original_paragraphs: List of original paragraph dictionaries
        rewritten_paragraphs: List of rewritten paragraph dictionaries
        max_chunk_size: Maximum size of text chunk to consider for matching

    Returns:
        The rewritten paragraphs with medias linked to appropriate sections
    """
    # Process each media element
    assert len(rewritten_paragraphs) != 0, "rewritten_paragraphs is empty"
    for media in medias:
        pre_chunk, post_chunk = media["near_chunks"]
        media_context = "\n\n".join(
            chunk.strip() for chunk in [pre_chunk, media["markdown_content"], post_chunk] if chunk.strip()
        )
        if len(media["near_chunks"][0]) < max_chunk_size:
            link_paragraph = rewritten_paragraphs[0]
        else:
            link_paragraph = max(
                rewritten_paragraphs,
                key=lambda x: edit_distance(
                    media_context, x.get("markdown_content", "")
                ),
            )

        if "medias" not in link_paragraph:
            link_paragraph["medias"] = []
        link_paragraph["medias"].append(media)

    return rewritten_paragraphs
