from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from pptagent.research.adapter import DeepResearchAdapter
from pptagent.research.content_resolver import ContentResolver, ResolvedContent


class FakeResponse:
    def __init__(
        self,
        url: str,
        content: bytes,
        *,
        text: str = "",
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.url = url
        self.content = content
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]) -> None:
        self.responses = responses
        self.headers: dict[str, str] = {}

    def get(self, url: str, **_: object) -> FakeResponse:
        if url not in self.responses:
            raise RuntimeError(f"unexpected url: {url}")
        return self.responses[url]


def test_content_resolver_accepts_direct_pdf(tmp_path: Path):
    resolver = ContentResolver(
        session=FakeSession(
            {
                "https://example.com/paper.pdf": FakeResponse(
                    "https://example.com/paper.pdf",
                    b"%PDF-1.7" + (b"x" * 70000),
                    headers={"Content-Type": "application/pdf"},
                )
            }
        )
    )

    result = resolver.resolve(
        url="https://example.com/paper.pdf",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "pdf"
    assert Path(result.local_path).exists()


def test_content_resolver_writes_source_markdown_for_html(tmp_path: Path):
    html = """
    <html>
      <head><title>Flow Matching Guide</title></head>
      <body>
        <article>
          <p>Flow matching learns a continuous vector field that transports a simple source distribution to a target data distribution.</p>
          <p>This page provides enough connected explanation to act as presentation-ready source material after html extraction.</p>
          <p>It should be written into a stable source.md file so the downstream V1 pipeline can consume it like parsed PDF markdown.</p>
        </article>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=80,
        min_substantial_blocks=2,
        min_block_chars=30,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/html": FakeResponse(
                    "https://example.com/html",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/html",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert Path(result.document_path).name == "source.md"
    assert Path(result.document_path).exists()


def test_content_resolver_source_markdown_filters_boilerplate_and_keeps_structure(tmp_path: Path):
    html = """
    <html>
      <head><title>Flow Matching Guide</title></head>
      <body>
        <nav>ICLR Blogposts 2025 Toggle navigation</nav>
        <article>
          <p>Conditional flow matching is introduced through an intuitive explanation and probabilistic formulation.</p>
          <h2>Introduction to Generative Modelling with Normalizing Flows</h2>
          <h2>Continuous Normalizing Flows</h2>
          <h2>Conditional Flow Matching</h2>
          <h2>From Conditional to Unconditional Velocity</h2>
          <p>Flow matching learns a continuous vector field that transports a simple source distribution to a target data distribution.</p>
          <p>It is suitable as presentation-ready source material because the article contains a coherent narrative with explanatory visuals.</p>
          \\[
          \\theta^* = \\arg\\min_\\theta \\mathcal{L}(\\theta)
          \\]
          <figure>
            <img src="/assets/chart.png" alt="flow chart" />
            <figcaption>Main explanatory chart.</figcaption>
          </figure>
          <video src="/assets/demo.mp4"></video>
          <p>Click here for details about Real NVP</p>
          <p>$$ \\def\\u{u(x,t)} \\def\\p{p_t(x)} \\newcommand{\\cL}{\\mathcal{L}} $$</p>
        </article>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=80,
        min_substantial_blocks=2,
        min_block_chars=30,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/structured-html": FakeResponse(
                    "https://example.com/structured-html",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/assets/chart.png": FakeResponse(
                    "https://example.com/assets/chart.png",
                    b"png-binary",
                    headers={"Content-Type": "image/png"},
                ),
                "https://example.com/assets/demo.mp4": FakeResponse(
                    "https://example.com/assets/demo.mp4",
                    b"mp4-binary",
                    headers={"Content-Type": "video/mp4"},
                ),
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/structured-html",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    saved_text = Path(result.document_path).read_text(encoding="utf-8")
    assert "Toggle navigation" not in saved_text
    assert "Click here for details" not in saved_text
    assert "\\def\\u" not in saved_text
    assert "## Continuous Normalizing Flows" not in saved_text
    assert "\\theta^* = \\arg\\min_\\theta \\mathcal{L}(\\theta)" in saved_text
    assert "![Main explanatory chart.](assets/" in saved_text
    assert "<video src=\"assets/" in saved_text
    assert "Flow matching learns a continuous vector field" in saved_text
    assert all(candidate.local_path for candidate in result.media_candidates)
    assert all(Path(candidate.local_path).exists() for candidate in result.media_candidates)


def test_content_resolver_accepts_small_pdf_without_size_threshold(tmp_path: Path):
    resolver = ContentResolver(
        session=FakeSession(
            {
                "https://example.com/tiny.pdf": FakeResponse(
                    "https://example.com/tiny.pdf",
                    b"%PDF-1.7tiny",
                    headers={"Content-Type": "application/pdf"},
                )
            }
        )
    )

    result = resolver.resolve(
        url="https://example.com/tiny.pdf",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "pdf"
    assert Path(result.local_path).exists()


def test_content_resolver_accepts_html_with_enough_continuous_text(tmp_path: Path):
    html = """
    <html>
      <head><title>Flow Matching Tutorial</title></head>
      <body>
        <article>
          <p>Flow matching is a generative modeling framework that learns a vector field transporting a simple source distribution to a target data distribution. This tutorial introduces the idea through a continuous transport view rather than a disconnected list of tricks.</p>
          <p>The core method trains a neural vector field against target velocities along conditional probability paths. This gives a direct and presentation-friendly explanation of how the model learns to move samples over continuous time.</p>
          <p>In practice the method is used across image generation, video synthesis, speech, and scientific modeling. The article keeps building one connected story instead of acting like a paper abstract or a link hub.</p>
          <p>Because the explanation is long and continuous, the current page itself should be treated as the final content source without chasing child links.</p>
        </article>
        <a href="/paper.pdf">PDF</a>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=400,
        min_substantial_blocks=3,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/tutorial": FakeResponse(
                    "https://example.com/tutorial",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/tutorial",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert result.final_url == "https://example.com/tutorial"
    assert result.extraction_method == "direct"
    saved_text = Path(result.local_path).read_text(encoding="utf-8")
    assert "presentation-friendly explanation" in saved_text


def test_content_resolver_preserves_full_html_when_explanatory_motion_media_exists(tmp_path: Path):
    repeated = " ".join(
        "This animated explanation shows how probability mass moves under flow matching."
        for _ in range(18)
    )
    html = """
    <html>
      <head><title>Visual Flow Matching Guide</title></head>
      <body>
        <article>
          <p>This visual tutorial explains flow matching with an animated walkthrough that shows how noise is transformed into samples over time. The page is meant for explanation rather than just metadata.</p>
          <p>The narrative keeps introducing the idea step by step, with practical intuition and a long continuous explanation that can serve as presentation material.</p>
          <p>REPEATED_TEXT</p>
          <img src="/assets/flow-demo.gif" alt="animated flow matching demo" />
        </article>
      </body>
    </html>
    """.replace("REPEATED_TEXT", repeated)
    resolver = ContentResolver(
        session=FakeSession(
            {
                "https://example.com/visual-guide": FakeResponse(
                    "https://example.com/visual-guide",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        )
    )

    result = resolver.resolve(
        url="https://example.com/visual-guide",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert result.local_path.endswith(".html")
    assert result.document_path.endswith(".md")
    assert result.media_stats.gif_count == 1
    assert result.media_stats.figure_count == 0
    assert len(result.media_candidates) == 1
    saved_html = Path(result.local_path).read_text(encoding="utf-8")
    assert "/assets/flow-demo.gif" in saved_html


def test_content_resolver_extracts_media_candidates_and_stats(tmp_path: Path):
    html = """
    <html>
      <head><title>Visual Guide</title></head>
      <body>
        <figure>
          <img src="/assets/flow.gif" alt="flow gif" />
          <figcaption>Animated transport demo</figcaption>
        </figure>
        <video src="/videos/overview.mp4"></video>
        <img src="/images/chart.png" alt="summary chart" />
        <article>
          <p>This page has enough continuous explanatory text to count as complete content for presentation generation.</p>
          <p>The article explains how samples move under the learned vector field and why the animation clarifies the process.</p>
          <p>It also includes a compact chart and a short video overview to support the explanation.</p>
        </article>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=120,
        min_substantial_blocks=2,
        min_block_chars=40,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/visual-guide": FakeResponse(
                    "https://example.com/visual-guide",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/visual-guide",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.document_path.endswith(".md")
    assert result.media_stats.gif_count == 1
    assert result.media_stats.video_count == 1
    assert result.media_stats.figure_count == 1
    assert result.media_stats.image_count == 1
    assert result.media_stats.total_visual_count == 3
    assert len(result.media_candidates) == 3
    assert result.media_candidates[0].figure_caption == "Animated transport demo"


def test_content_resolver_falls_back_to_first_pdf_child_when_html_is_short(tmp_path: Path):
    landing_html = """
    <html>
      <body>
        <div>Flow Matching Guide</div>
        <p>Short abstract only.</p>
        <a href="/downloads/flow-matching-report.pdf">Download the paper</a>
        <a href="/related/other-paper">Related paper</a>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=400,
        min_substantial_blocks=3,
        session=FakeSession(
            {
                "https://example.com/landing": FakeResponse(
                    "https://example.com/landing",
                    landing_html.encode("utf-8"),
                    text=landing_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/downloads/flow-matching-report.pdf": FakeResponse(
                    "https://example.com/downloads/flow-matching-report.pdf",
                    b"%PDF-1.7" + (b"x" * 70000),
                    headers={"Content-Type": "application/pdf"},
                ),
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/landing",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "pdf"
    assert result.final_url == "https://example.com/downloads/flow-matching-report.pdf"
    assert result.extraction_method == "child_link"


def test_content_resolver_can_choose_best_media_source_from_url_list(tmp_path: Path):
    heavy_html = """
    <html>
      <body>
        <figure><img src="/assets/a.gif" alt="a" /><figcaption>GIF A</figcaption></figure>
        <figure><img src="/assets/b.gif" alt="b" /></figure>
        <video src="/videos/overview.mp4"></video>
        <article>
          <p>This visual tutorial explains flow matching with multiple media assets.</p>
          <p>The text is long enough to qualify as valid presentation source material.</p>
        </article>
      </body>
    </html>
    """
    light_html = """
    <html>
      <body>
        <img src="/images/chart.png" alt="chart" />
        <article>
          <p>This page is valid but has much less media than the visual tutorial.</p>
          <p>The explanation still forms a coherent article.</p>
        </article>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=80,
        min_substantial_blocks=2,
        min_block_chars=30,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/light": FakeResponse(
                    "https://example.com/light",
                    light_html.encode("utf-8"),
                    text=light_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/heavy": FakeResponse(
                    "https://example.com/heavy",
                    heavy_html.encode("utf-8"),
                    text=heavy_html,
                    headers={"Content-Type": "text/html"},
                ),
            }
        ),
    )

    result = resolver.resolve_best_media_source(
        urls=["https://example.com/light", "https://example.com/heavy"],
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.source_url == "https://example.com/heavy"
    assert result.media_stats.gif_count == 2
    assert result.media_stats.video_count == 1
    assert result.media_stats.figure_count == 2


def test_content_resolver_can_stop_early_when_motion_media_threshold_is_met(tmp_path: Path):
    threshold_html = """
    <html>
      <body>
        <img src="/assets/a.gif" alt="a" />
        <img src="/assets/b.gif" alt="b" />
        <img src="/assets/c.gif" alt="c" />
        <video src="/videos/1.mp4"></video>
        <video src="/videos/2.mp4"></video>
        <video src="/videos/3.mp4"></video>
        <article>
          <p>This visual page contains enough explanation and many motion assets.</p>
          <p>The content is valid and should trigger early stop once the threshold is reached.</p>
        </article>
      </body>
    </html>
    """
    later_html = """
    <html>
      <body>
        <figure><img src="/assets/chart.png" alt="chart" /></figure>
        <article>
          <p>This page appears later and should never be chosen if early stop works.</p>
          <p>Its content is valid but has weaker motion support.</p>
        </article>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=80,
        min_substantial_blocks=2,
        min_block_chars=30,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/threshold": FakeResponse(
                    "https://example.com/threshold",
                    threshold_html.encode("utf-8"),
                    text=threshold_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/later": FakeResponse(
                    "https://example.com/later",
                    later_html.encode("utf-8"),
                    text=later_html,
                    headers={"Content-Type": "text/html"},
                ),
            }
        ),
    )

    result = resolver.resolve_best_media_source(
        urls=["https://example.com/threshold", "https://example.com/later"],
        output_dir=str(tmp_path),
        topic="flow matching",
        min_motion_media_count=6,
    )

    assert result.success is True
    assert result.source_url == "https://example.com/threshold"
    assert result.media_stats.gif_count == 3
    assert result.media_stats.video_count == 3


def test_content_resolver_keeps_all_candidates_so_late_pdf_is_still_found(tmp_path: Path):
    noisy_links = "\n".join(
        f'<a href="/other/{index}">Other {index}</a>'
        for index in range(25)
    )
    landing_html = f"""
    <html>
      <body>
        <p>Short abstract only.</p>
        {noisy_links}
        <a href="/downloads/final-paper.pdf">Download the paper</a>
      </body>
    </html>
    """
    resolver = ContentResolver(
        min_text_chars=400,
        min_substantial_blocks=3,
        session=FakeSession(
            {
                "https://example.com/landing": FakeResponse(
                    "https://example.com/landing",
                    landing_html.encode("utf-8"),
                    text=landing_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/downloads/final-paper.pdf": FakeResponse(
                    "https://example.com/downloads/final-paper.pdf",
                    b"%PDF-1.7tiny",
                    headers={"Content-Type": "application/pdf"},
                ),
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/landing",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "pdf"
    assert result.final_url == "https://example.com/downloads/final-paper.pdf"


def test_content_resolver_can_use_selector_hook_for_non_pdf_child(tmp_path: Path):
    landing_html = """
    <html>
      <body>
        <p>Short landing page.</p>
        <a href="/go/full-guide">Read more</a>
        <a href="/related">Related</a>
      </body>
    </html>
    """
    full_html = """
    <html>
      <head><title>Full Guide</title></head>
      <body>
        <article>
          <p>Flow matching explains generative transport with a continuous vector field that maps a simple source distribution to a target distribution.</p>
          <p>The training objective directly regresses the velocity field along conditional paths, which makes the method easier to explain than a sparse landing page.</p>
          <p>This full guide includes enough connected exposition and applications to work as complete presentation input.</p>
        </article>
      </body>
    </html>
    """

    def selector(**kwargs):
        for candidate in kwargs["candidates"]:
            if candidate["url"].endswith("/go/full-guide"):
                return candidate["url"]
        return None

    resolver = ContentResolver(
        min_text_chars=200,
        min_substantial_blocks=2,
        min_block_chars=80,
        llm_enabled=False,
        child_link_selector=selector,
        session=FakeSession(
            {
                "https://example.com/landing": FakeResponse(
                    "https://example.com/landing",
                    landing_html.encode("utf-8"),
                    text=landing_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/go/full-guide": FakeResponse(
                    "https://example.com/go/full-guide",
                    full_html.encode("utf-8"),
                    text=full_html,
                    headers={"Content-Type": "text/html"},
                ),
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/landing",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert result.final_url == "https://example.com/go/full-guide"
    assert result.extraction_method == "child_link"


def test_content_resolver_can_keep_current_html_with_enough_content_and_no_llm(tmp_path: Path):
    html = """
    <html>
      <head><title>Borderline Tutorial</title></head>
      <body>
        <article>
          <p>Flow matching learns a transport field between a source distribution and a target distribution.</p>
          <p>The article explains the training objective and continuous-time generation process in connected prose.</p>
          <p>It is slightly shorter than the default threshold but still clearly behaves like a full tutorial page.</p>
        </article>
        <a href="/paper.pdf">PDF</a>
      </body>
    </html>
    """

    resolver = ContentResolver(
        min_text_chars=120,
        min_substantial_blocks=2,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/borderline": FakeResponse(
                    "https://example.com/borderline",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/borderline",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert result.final_url == "https://example.com/borderline"


def test_content_resolver_child_link_selector_can_choose_non_pdf_child(tmp_path: Path):
    landing_html = """
    <html>
      <body>
        <p>Short landing page.</p>
        <a href="/go/full-guide">Read more</a>
        <a href="/paper.pdf">PDF</a>
      </body>
    </html>
    """
    full_html = """
    <html>
      <head><title>Chosen Guide</title></head>
      <body>
        <article>
          <p>Flow matching is explained as continuous transport between probability distributions, with enough exposition to make the current HTML page read like a complete tutorial rather than a short landing page.</p>
          <p>The guide expands on the velocity field objective, conditional paths, inference procedure, and downstream applications, so the text now forms a coherent and substantial body of presentation-ready content.</p>
          <p>This page is clearly the intended full-content destination because it keeps elaborating the method instead of acting as a navigation hub or metadata page.</p>
        </article>
      </body>
    </html>
    """

    def selector(page_url: str, **_: object):
        if page_url == "https://example.com/landing":
            return "https://example.com/go/full-guide"
        return None

    resolver = ContentResolver(
        min_text_chars=200,
        min_substantial_blocks=2,
        min_block_chars=80,
        child_link_selector=selector,
        llm_enabled=False,
        session=FakeSession(
            {
                "https://example.com/landing": FakeResponse(
                    "https://example.com/landing",
                    landing_html.encode("utf-8"),
                    text=landing_html,
                    headers={"Content-Type": "text/html"},
                ),
                "https://example.com/go/full-guide": FakeResponse(
                    "https://example.com/go/full-guide",
                    full_html.encode("utf-8"),
                    text=full_html,
                    headers={"Content-Type": "text/html"},
                ),
            }
        ),
    )

    result = resolver.resolve(
        url="https://example.com/landing",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.content_type == "html"
    assert result.final_url == "https://example.com/go/full-guide"


def test_content_resolver_llm_prompt_receives_full_parsed_html(tmp_path: Path):
    repeated = " ".join(f"segment-{i:03d}" for i in range(1200))
    html = f"""
    <html>
      <head><title>Long HTML</title></head>
      <body>
        <article>
          <p>{repeated}</p>
        </article>
      </body>
    </html>
    """

    captured: dict[str, str] = {}

    resolver = ContentResolver(
        llm_enabled=True,
        llm_excerpt_chars=None,
        session=FakeSession(
            {
                "https://example.com/long-html": FakeResponse(
                    "https://example.com/long-html",
                    html.encode("utf-8"),
                    text=html,
                    headers={"Content-Type": "text/html"},
                )
            }
        ),
    )

    def fake_call_llm_decider(prompt: str):
        captured["prompt"] = prompt
        return {"use_current_page": True, "content_url": None}

    resolver._call_llm_decider = fake_call_llm_decider  # type: ignore[method-assign]

    result = resolver.resolve(
        url="https://example.com/long-html",
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert "segment-1199" in captured["prompt"]


def test_adapter_resolves_first_complete_content_in_visit_order(tmp_path: Path):
    payload = {
        "question": "flow matching",
        "messages": [
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/short"],"goal":"read short page"}}\n</tool_call>',
            },
            {
                "role": "user",
                "content": '<tool_response>\nThe useful information in https://example.com/short for user goal read short page as follows: \n\nEvidence in page: \nshort\n\nSummary: \nshort summary\n\n</tool_response>',
            },
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/complete"],"goal":"read full page"}}\n</tool_call>',
            },
            {
                "role": "user",
                "content": '<tool_response>\nThe useful information in https://example.com/complete for user goal read full page as follows: \n\nEvidence in page: \nfull page\n\nSummary: \ncomplete summary\n\n</tool_response>',
            },
        ],
    }

    complete_html = """
    <html><body>
      <article>
        <p>Flow matching learns a transport field that moves a simple source distribution to the target data distribution in continuous time.</p>
        <p>The method is trained by regressing the model onto target velocities defined by conditional paths, which makes it suitable for clear explanation in a presentation.</p>
        <p>Applications span image, video, and speech generation and provide enough connected material for a full presentation narrative.</p>
      </article>
    </body></html>
    """
    adapter = DeepResearchAdapter(
        content_resolver=ContentResolver(
            min_text_chars=200,
            min_substantial_blocks=2,
            min_block_chars=80,
            llm_enabled=False,
            session=FakeSession(
                {
                    "https://example.com/short": FakeResponse(
                        "https://example.com/short",
                        b"<html><body><p>tiny</p><a href='/paper.pdf'>pdf</a></body></html>",
                        text="<html><body><p>tiny</p><a href='/paper.pdf'>pdf</a></body></html>",
                        headers={"Content-Type": "text/html"},
                    ),
                    "https://example.com/complete": FakeResponse(
                        "https://example.com/complete",
                        complete_html.encode("utf-8"),
                        text=complete_html,
                        headers={"Content-Type": "text/html"},
                    ),
                }
            ),
        )
    )

    result = adapter.resolve_first_complete_content_from_payload(
        payload=payload,
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.source_url == "https://example.com/complete"


def test_adapter_prefers_motion_html_with_enough_total_text(tmp_path: Path):
    payload = {
        "question": "flow matching",
        "messages": [
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/plain"],"goal":"read plain page"}}\n</tool_call>',
            },
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/visual"],"goal":"read visual page"}}\n</tool_call>',
            },
        ],
    }

    repeated = " ".join(
        "This animated explanation shows how probability mass moves under flow matching in a presentation-friendly way."
        for _ in range(170)
    )
    plain_html = """
    <html><body><article>
      <p>Flow matching learns a transport field that moves a simple source distribution to the target data distribution in continuous time.</p>
      <p>The method is trained by regressing the model onto target velocities defined by conditional paths.</p>
      <p>This page is complete enough but has no motion media.</p>
    </article></body></html>
    """
    visual_html = f"""
    <html><body><article>
      <p>Visual guide to flow matching with an animated walkthrough.</p>
      <p>{repeated}</p>
      <img src="/assets/flow-demo.gif" alt="animated flow matching demo" />
    </article></body></html>
    """
    adapter = DeepResearchAdapter(
        preferred_motion_min_total_text_length=15000,
        content_resolver=ContentResolver(
            min_text_chars=200,
            min_substantial_blocks=2,
            min_block_chars=80,
            llm_enabled=False,
            session=FakeSession(
                {
                    "https://example.com/plain": FakeResponse(
                        "https://example.com/plain",
                        plain_html.encode("utf-8"),
                        text=plain_html,
                        headers={"Content-Type": "text/html"},
                    ),
                    "https://example.com/visual": FakeResponse(
                        "https://example.com/visual",
                        visual_html.encode("utf-8"),
                        text=visual_html,
                        headers={"Content-Type": "text/html"},
                    ),
                }
            ),
        ),
    )

    result = adapter.resolve_first_complete_content_from_payload(
        payload=payload,
        output_dir=str(tmp_path),
        topic="flow matching",
    )

    assert result.success is True
    assert result.source_url == "https://example.com/visual"
    assert result.content_type == "html"
    assert result.has_explanatory_motion_media is True
    assert result.total_text_length >= 15000


def test_adapter_resolve_best_media_content_live_returns_best_motion_result(tmp_path: Path):
    result_path = tmp_path / "iter1.jsonl"
    result_path.write_text("", encoding="utf-8")

    low_motion_html = """
    <html><body><article>
      <p>Flow matching guide with one animated explainer.</p>
      <img src="/assets/demo.gif" alt="single flow animation" />
    </article></body></html>
    """
    high_motion_html = """
    <html><body><article>
      <p>Flow matching guide with multiple motion assets.</p>
      <img src="/assets/demo-a.gif" alt="first flow animation" />
      <img src="/assets/demo-b.gif" alt="second flow animation" />
      <video src="/assets/demo.mp4"></video>
    </article></body></html>
    """
    adapter = DeepResearchAdapter(
        content_resolver=ContentResolver(
            min_text_chars=10,
            min_substantial_blocks=1,
            min_block_chars=5,
            llm_enabled=False,
            session=FakeSession(
                {
                    "https://example.com/low-motion": FakeResponse(
                        "https://example.com/low-motion",
                        low_motion_html.encode("utf-8"),
                        text=low_motion_html,
                        headers={"Content-Type": "text/html"},
                    ),
                    "https://example.com/high-motion": FakeResponse(
                        "https://example.com/high-motion",
                        high_motion_html.encode("utf-8"),
                        text=high_motion_html,
                        headers={"Content-Type": "text/html"},
                    ),
                }
            ),
        )
    )

    first_payload = {
        "question": "flow matching",
        "messages": [
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/low-motion"],"goal":"read first page"}}\n</tool_call>',
            }
        ],
    }
    second_payload = {
        "question": "flow matching",
        "messages": [
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/high-motion"],"goal":"read second page"}}\n</tool_call>',
            }
        ],
    }

    def writer() -> None:
        time.sleep(0.02)
        with open(result_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(first_payload, ensure_ascii=False) + "\n")
        time.sleep(0.02)
        with open(result_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(second_payload, ensure_ascii=False) + "\n")

    worker = threading.Thread(target=writer)
    worker.start()
    try:
        result = adapter.resolve_best_media_content_live(
            result_path=str(result_path),
            output_dir=str(tmp_path),
            topic="flow matching",
            max_wait_seconds=0.15,
            poll_interval_seconds=0.01,
        )
    finally:
        worker.join()

    assert result.success is True
    assert result.source_url == "https://example.com/high-motion"
    assert result.media_stats.gif_count + result.media_stats.video_count == 3


def test_adapter_resolve_best_media_content_live_can_stop_early_on_motion_threshold(tmp_path: Path):
    result_path = tmp_path / "iter2.jsonl"
    payload = {
        "question": "flow matching",
        "messages": [
            {
                "role": "assistant",
                "content": '<tool_call>\n{"name":"visit","arguments":{"url":["https://example.com/threshold"],"goal":"read animated page"}}\n</tool_call>',
            }
        ],
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    threshold_html = """
    <html><body><article>
      <p>Animated flow matching overview.</p>
      <img src="/assets/a.gif" alt="animation a" />
      <img src="/assets/b.gif" alt="animation b" />
    </article></body></html>
    """
    adapter = DeepResearchAdapter(
        content_resolver=ContentResolver(
            min_text_chars=10,
            min_substantial_blocks=1,
            min_block_chars=5,
            llm_enabled=False,
            session=FakeSession(
                {
                    "https://example.com/threshold": FakeResponse(
                        "https://example.com/threshold",
                        threshold_html.encode("utf-8"),
                        text=threshold_html,
                        headers={"Content-Type": "text/html"},
                    )
                }
            ),
        )
    )

    started_at = time.monotonic()
    result = adapter.resolve_best_media_content_live(
        result_path=str(result_path),
        output_dir=str(tmp_path),
        topic="flow matching",
        max_wait_seconds=1.0,
        poll_interval_seconds=0.2,
        min_motion_media_count=2,
    )
    elapsed = time.monotonic() - started_at

    assert result.success is True
    assert result.source_url == "https://example.com/threshold"
    assert elapsed < 0.5


def test_adapter_prepare_deepresearch_eval_data_writes_jsonl(tmp_path: Path):
    deepresearch_root = tmp_path / "DeepResearch"
    (deepresearch_root / "inference").mkdir(parents=True, exist_ok=True)
    (deepresearch_root / ".env").write_text("DATASET=eval_data/flow_matching.jsonl\n", encoding="utf-8")
    adapter = DeepResearchAdapter()

    dataset_path = adapter.prepare_deepresearch_eval_data(
        question="Explain diffusion models",
        deepresearch_root=str(deepresearch_root),
    )

    path = Path(dataset_path)
    assert path.exists()
    assert path.parent == deepresearch_root / "inference" / "eval_data"
    payload = json.loads(path.read_text(encoding="utf-8").strip())
    assert payload == {"question": "Explain diffusion models", "answer": ""}


def test_adapter_run_deepresearch_and_resolve_best_media_content_live_wires_launch_and_monitor(
    tmp_path: Path,
):
    class FakeProcess:
        def __init__(self) -> None:
            self.pid = 1234
            self._terminated = False

        def poll(self) -> None:
            return None if not self._terminated else 0

        def terminate(self) -> None:
            self._terminated = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            self._terminated = True

    adapter = DeepResearchAdapter()
    launch_calls: dict[str, str] = {}
    monitor_calls: dict[str, object] = {}
    fake_process = FakeProcess()

    def fake_launch(**kwargs: object):
        launch_calls.update(kwargs)  # type: ignore[arg-type]
        report_handle = open(tmp_path / "fake.report", "a", encoding="utf-8")
        return fake_process, report_handle

    def fake_monitor(**kwargs: object) -> ResolvedContent:
        monitor_calls.update(kwargs)
        return ResolvedContent(
            source_url="https://example.com/final",
            final_url="https://example.com/final",
            success=True,
            content_type="html",
        )

    adapter.launch_deepresearch = fake_launch  # type: ignore[method-assign]
    adapter.resolve_best_media_content_live = fake_monitor  # type: ignore[method-assign]

    report_path = tmp_path / "live.report"
    deepresearch_root = tmp_path / "DeepResearch"
    (deepresearch_root / "inference").mkdir(parents=True, exist_ok=True)
    (deepresearch_root / ".env").write_text("DATASET=eval_data/flow_matching.jsonl\n", encoding="utf-8")
    result = adapter.run_deepresearch_and_resolve_best_media_content_live(
        question="Explain diffusion models",
        deepresearch_root=str(deepresearch_root),
        output_dir=str(tmp_path / "resolver_cache"),
        report_path=str(report_path),
        conda_env="react_infer_env",
        conda_executable="conda",
        max_wait_seconds=12.0,
        poll_interval_seconds=0.5,
        min_motion_media_count=1,
    )

    assert result.success is True
    assert Path(str(launch_calls["dataset_path"])).exists()
    assert launch_calls["report_path"] == str(report_path)
    assert launch_calls["conda_env"] == "react_infer_env"
    assert launch_calls["conda_executable"] == "conda"
    assert monitor_calls["result_path"] == str(report_path)
    assert monitor_calls["output_dir"] == str(tmp_path / "resolver_cache")
    assert monitor_calls["topic"] == "Explain diffusion models"
    assert monitor_calls["max_wait_seconds"] == 12.0
    assert monitor_calls["poll_interval_seconds"] == 0.5
    assert monitor_calls["min_motion_media_count"] == 1
    assert fake_process._terminated is True
