#  PresentAgent-2

PresentAgent-2 is a multimodal presentation agent for **query-to-presentation-video generation**.

It extends the original PresentAgent pipeline with:

- **Deep Research** for open-ended query expansion
- **Top-3 HTML candidate selection** from live research reports
- **Multimodal source construction** with text, images, GIFs, and videos
- **Three presentation modes**
  - `single_presentation`
  - `discussion`
  - `interaction`

The main end-to-end entrypoint in this repository is:

```bash
scripts/run_url_to_video_pipeline.py
```

This script can run:

- `query -> DeepResearch -> top-3 HTML selection -> source.md -> refined_doc.json -> pptx -> video`
- or directly `url -> source.md -> refined_doc.json -> pptx -> video`

---

## Quick Start

### Query to Video

```bash
python scripts/run_url_to_video_pipeline.py \
  --question "Please create a discussion-style presentation about PresentAgent-2, focusing on deep research, multimodal search, and the three presentation modes." \
  --deepresearch-root /path/to/DeepResearch \
  --output-root /path/to/output/presentagent2_demo \
  --template-pptx /path/to/build_effective_agents.pptx \
  --notes-mode discussion \
  --num-slides 8 \
  --deepresearch-conda-env deepresearch \
  --max-wait-seconds 900 \
  --poll-interval-seconds 30
```

### URL to Video

```bash
python scripts/run_url_to_video_pipeline.py \
  --url "https://aigeeksgroup.github.io/PresentAgent-2/" \
  --output-root /path/to/output/presentagent2_from_url \
  --template-pptx /path/to/build_effective_agents.pptx \
  --notes-mode discussion \
  --num-slides 8
```

---

## Pipeline

1. **Deep Research**
   - launch the research agent from a user query
   - monitor the live report
   - collect visited webpages
   - maintain the top-3 HTML candidates

2. **Source Construction**
   - convert the selected webpage into `source.md`

3. **Document Construction**
   - convert `source.md` into `refined_doc.json`

4. **Presentation Generation**
   - generate a PPT in one of the supported modes

5. **Video Rendering**
   - convert the PPT into a narrated presentation video

---

## Output Structure

```text
output_root/
├── url_to_source/
│   ├── source.md
│   ├── report.log
│   └── candidates/
├── source_to_document/
│   ├── refined_doc.json
│   ├── document_overview.txt
│   └── media_summary.json
├── document_to_ppt/
│   └── <notes_mode>/
│       └── final_<notes_mode>.pptx
├── ppt_to_video/
│   └── <notes_mode>/
│       └── output.mp4
└── pipeline_summary.json
```

---

## Requirements

You will typically need:

- Python environment for PresentAgent-2
- `ffmpeg`
- `LibreOffice` or `soffice`
- `MegaTTS3` checkpoints under:

```text
presentagent/MegaTTS3/checkpoints/
```

- a working `DeepResearch` environment and its `.env` configuration

---

## Notes

- `--question` mode requires a working `DeepResearch` installation.
- `--url` mode skips DeepResearch and starts from a webpage or PDF URL.
- The repository currently exposes a single main script in `scripts/` to keep the public entrypoint simple.

---

## Paper / Project Links

- Paper: `<paper-link-placeholder>`
- Project page: `https://aigeeksgroup.github.io/PresentAgent-2/`
- Demo video: `<demo-video-placeholder>`
- Dataset: `<dataset-link-placeholder>`

