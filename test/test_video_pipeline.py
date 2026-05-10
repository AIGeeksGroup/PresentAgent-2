from types import SimpleNamespace

from PIL import Image

from pptagent.document import Document
from pptagent.apis import replace_image, replace_video
from pptagent.presentation import Layout


def test_layout_validate_accepts_video_asset(tmp_path):
    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"fake-video")

    layout = Layout.from_dict(
        "Video Layout",
        {
            "template_id": 1,
            "slides": [1],
            "content_schema": {
                "visual": {
                    "type": "image",
                    "data": ["placeholder.png"],
                    "description": "Visual slot",
                }
            },
        },
    )
    editor_output = {"visual": {"data": [str(video_path)]}}

    layout.validate(editor_output, str(tmp_path))

    assert editor_output["visual"]["data"][0] == str(video_path)


def test_replace_image_delegates_to_replace_video(monkeypatch):
    calls = []

    def fake_replace_video(slide, img_id, video_path):
        calls.append((slide, img_id, video_path))

    monkeypatch.setattr("pptagent.apis.replace_video", fake_replace_video)

    slide = SimpleNamespace()
    replace_image(slide, None, 3, "demo.mp4")

    assert calls == [(slide, 3, "demo.mp4")]


def test_replace_image_embeds_gif_as_animated_picture_by_default(monkeypatch, tmp_path):
    gif_path = tmp_path / "demo.gif"
    Image.new("RGB", (2, 2), color="white").save(gif_path, format="GIF")
    calls = []

    class FakePicture:
        pass

    shape = FakePicture()

    monkeypatch.setattr("pptagent.apis.Picture", FakePicture)
    monkeypatch.setattr("pptagent.apis.element_index", lambda slide, img_id: shape)
    monkeypatch.setattr(
        "pptagent.apis._replace_picture_asset",
        lambda target_shape, image_path: calls.append((target_shape, image_path)),
    )

    replace_image(SimpleNamespace(), None, 3, str(gif_path))

    assert calls == [(shape, str(gif_path))]


def test_document_treats_gif_as_video_and_filters_author_media(tmp_path):
    gif_path = tmp_path / "flow.gif"
    gif_path.write_bytes(b"fake-gif")
    avatar_path = tmp_path / "avatar.png"
    avatar_path.write_bytes(b"fake-image")
    decorative_path = tmp_path / "smoke.png"
    decorative_path.write_bytes(b"fake-image")

    document = Document.from_dict(
        {
            "metadata": {"title": "Demo"},
            "sections": [
                {
                    "title": "Intro",
                    "summary": "summary",
                    "markdown_content": "",
                    "subsections": [
                        {
                            "title": "Author",
                            "content": "metadata",
                            "medias": [
                                {
                                    "markdown_content": "![Author avatar](avatar.png)",
                                    "near_chunks": ["", ""],
                                    "path": str(avatar_path),
                                    "caption": "Icon: Author avatar portrait",
                                    "media_type": "image",
                                },
                                {
                                    "markdown_content": "![Abstract smoke](smoke.png)",
                                    "near_chunks": ["", ""],
                                    "path": str(decorative_path),
                                    "caption": "Picture: Purple smoke plume against a black background.",
                                    "media_type": "image",
                                },
                                {
                                    "markdown_content": "![Flow](flow.gif)",
                                    "near_chunks": ["", ""],
                                    "path": str(gif_path),
                                    "caption": "Animated flow trajectory",
                                    "media_type": "image",
                                },
                            ],
                        }
                    ],
                }
            ],
        },
        str(tmp_path),
        require_caption=False,
    )

    assert [media.path for media in document.iter_medias("video")] == [str(gif_path)]
    overview = document.get_overview()
    assert "Author avatar" not in overview
    assert "Purple smoke plume" not in overview
    assert "Animated flow trajectory" in overview


def test_replace_video_falls_back_to_gif_image_when_ffmpeg_missing(
    monkeypatch, tmp_path
):
    gif_path = tmp_path / "demo.gif"
    Image.new("RGB", (2, 2), color="white").save(gif_path, format="GIF")
    calls = []

    class FakePicture:
        pass

    shape = FakePicture()

    monkeypatch.setattr("pptagent.apis.Picture", FakePicture)
    monkeypatch.setattr("pptagent.apis.element_index", lambda slide, img_id: shape)
    monkeypatch.setattr(
        "pptagent.apis._replace_picture_asset",
        lambda target_shape, image_path: calls.append((target_shape, image_path)),
    )
    monkeypatch.setattr("pptagent.apis.shutil.which", lambda name: None)

    replace_video(SimpleNamespace(), 3, str(gif_path))

    assert calls == [(shape, str(gif_path))]
