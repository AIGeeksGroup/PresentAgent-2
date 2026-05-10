from test.conftest import test_config

import pytest
from PIL import Image

from pptagent.document import Document, OutlineItem, SubSection, Video
from pptagent.document.document import to_paragraphs


@pytest.mark.llm
def test_document():
    with open(f"{test_config.document}/source.md") as f:
        markdown_content = f.read()
    cutoff = markdown_content.find("## When (and when not) to use agents")
    image_dir = test_config.document
    doc = Document.from_markdown(
        markdown_content[:cutoff],
        test_config.language_model.to_sync(),
        test_config.vision_model.to_sync(),
        image_dir,
    )
    doc.get_overview(include_summary=True)
    doc.metainfo


@pytest.mark.asyncio
@pytest.mark.llm
async def test_document_async():
    with open(f"{test_config.document}/source.md") as f:
        markdown_content = f.read()
    cutoff = markdown_content.find("## When (and when not) to use agents")
    image_dir = test_config.document
    await Document.from_markdown_async(
        markdown_content[:cutoff],
        test_config.language_model,
        test_config.vision_model,
        image_dir,
    )


def test_document_from_dict():
    document = Document.from_dict(
        test_config.get_document_json(),
        test_config.document,
        True,
    )
    document.get_overview(include_summary=True)
    document.metainfo
    document.retrieve({"Building effective agents": ["What are agents?"]})


def test_outline_retrieve():
    document = Document.from_dict(
        test_config.get_document_json(),
        test_config.document,
        False,
    )
    outline = test_config.get_outline()
    for outline_item in outline:
        item = OutlineItem.from_dict(outline_item)
        print(item.retrieve(0, document))


def test_to_paragraphs_detects_video_blocks():
    markdown_content = "\n\n".join(
        [
            "# Title",
            "Intro paragraph.",
            '<video src="assets/demo.mp4"></video>',
            "Outro paragraph.",
        ]
    )

    medias = to_paragraphs(markdown_content)

    assert len(medias) == 1
    assert medias[0]["type"] == "video"
    assert "Intro paragraph." in medias[0]["near_chunks"][0]
    assert "Outro paragraph." in medias[0]["near_chunks"][1]


def test_subsection_supports_video_media(tmp_path):
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    video_path = assets_dir / "demo.mp4"
    video_path.write_bytes(b"fake-video")

    subsection = SubSection.from_dict(
        {
            "title": "Video subsection",
            "content": "Body",
            "medias": [
                {
                    "type": "video",
                    "markdown_content": '<video src="assets/demo.mp4"></video>',
                    "near_chunks": ("Before", "After"),
                }
            ],
        }
    )

    assert len(subsection.medias) == 1
    assert isinstance(subsection.medias[0], Video)
    subsection.medias[0].parse(None, str(tmp_path))
    assert subsection.medias[0].path == str(video_path)


def test_outline_retrieve_splits_images_and_videos(tmp_path):
    image_path = tmp_path / "figure.png"
    Image.new("RGB", (1, 1), color="white").save(image_path)
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake-video")

    document = Document.from_dict(
        {
            "metadata": {"title": "Demo"},
            "sections": [
                {
                    "title": "Section A",
                    "summary": "Summary",
                    "subsections": [
                        {
                            "title": "Subsection A",
                            "content": "Body text",
                            "medias": [
                                {
                                    "type": "image",
                                    "markdown_content": "![figure](figure.png)",
                                    "near_chunks": ("Before", "After"),
                                    "path": str(image_path),
                                    "caption": "Figure caption",
                                },
                                {
                                    "type": "video",
                                    "markdown_content": '<video src="demo.mp4"></video>',
                                    "near_chunks": ("Before", "After"),
                                    "path": str(video_path),
                                    "caption": "Video caption",
                                },
                            ],
                        }
                    ],
                    "markdown_content": "## Section A\n\nBody text",
                }
            ],
        },
        str(tmp_path),
        False,
    )

    item = OutlineItem.from_dict(
        {
            "purpose": "Explain concept",
            "section": "Section A",
            "indexs": {"Section A": ["Subsection A"]},
            "images": ["Figure caption"],
        }
    )
    header, content, images, videos = item.retrieve(0, document)

    assert "Slide-1" in header
    assert "Body text" in content
    assert len(images) == 1
    assert "figure.png" in images[0]
    assert len(videos) == 1
    assert "demo.mp4" in videos[0]
