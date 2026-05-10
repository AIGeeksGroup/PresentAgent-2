# Single Presentation Eval

This document describes the evaluation protocol implemented by [`scripts/run_single_presentation_eval.py`](./run_single_presentation_eval.py). It is written for two purposes:

1. to help future models or contributors understand the current evaluation design;
2. to serve as a paper-oriented reference when writing the evaluation section for our presentation agent.

## Goal

The goal of this evaluation is to measure the quality of a generated presentation produced by our presentation agent.

The protocol is intentionally close to the `PresentEval` design in the paper *PresentAgent: Multimodal Agent for Presentation Video Generation*, with one explicit extension:

- we keep `Objective Quiz Evaluation`;
- we keep `Subjective Scoring`;
- we add `Multimodal Search Quality` as an extra metric group.

In other words, this is a `PresentEval-style` protocol adapted to our project, where media retrieval and media usage quality are first-class evaluation targets.

## Evaluation Target

Each sample is a generated single-presentation package associated with one source paper or document.

The evaluation target is the generated presentation output, not the retrieval pipeline alone and not the source document alone.

Concretely, for each sample we evaluate:

- the presentation's ability to communicate the source content;
- the subjective quality of the presentation as a presentation;
- the quality of multimodal media search and integration.

## Required Inputs Per Sample

For each sample, the current script expects:

- `single_presentation/final_single_presentation.pptx`
- `single_presentation_video/output.mp4`
- `single_presentation_video/slide_notes.json`
- `single_presentation_video/notes_assets/frame_*.jpg` or `frame_*.png`
- `url_to_source/source.md`
- `source_to_document/refined_doc.json`

The script discovers samples from two roots:

- `--ppt-root`: generated presentation directories
- `--bundle-root`: aligned source-document bundle directories

## Important Implementation Note

Although the evaluated object is conceptually a `presentation video`, the current implementation does **not** directly feed the full `mp4` into the judge model.

Instead, the current judge evidence is:

- flattened presentation notes from `slide_notes.json`
- sampled slide frames from `notes_assets/frame_*.jpg` or `.png`
- source context extracted from `refined_doc.json`

So the present implementation should be described accurately as:

`presentation-level evaluation using presentation notes plus sampled slide frames as evidence`

rather than:

`full end-to-end video understanding evaluation`

This distinction matters in the paper. We should not overclaim that the current protocol fully evaluates temporal video experience, fine-grained audiovisual synchronization, or voice naturalness from raw audio.

## Metric Groups

The protocol has three metric groups.

### 1. Objective Quiz Evaluation

This metric group measures whether the presentation successfully conveys the source content.

For each sample, the evaluator first creates exactly 5 multiple-choice questions. The intended coverage is:

- topic recognition
- structural understanding
- core mechanism, comparison, or limitation
- key result or capability
- takeaway

The evaluator then answers those 5 questions using only the presentation evidence:

- sampled slide frames
- speaking notes / transcript

The outputs are:

- per-question predicted answer
- per-question gold answer
- correctness flag
- `quiz_score_raw`
- `quiz_score_norm`

Interpretation:

- higher quiz score means the presentation better communicates the important source information;
- this is our objective comprehension metric.

### 2. Subjective Scoring

This metric group follows the high-level spirit of `PresentEval` in the paper, but is slightly adapted for our presentation-agent setting.

The current three subjective dimensions are:

- `content_quality_score`
- `visual_quality_score`
- `comprehension_clarity_score`

Definitions:

- `content_quality_score`: whether the presentation faithfully covers the important source content, remains accurate, and surfaces the main ideas.
- `visual_quality_score`: whether the slides are readable, visually coherent, and presentation-like.
- `comprehension_clarity_score`: whether a viewer can follow the explanation easily, with coherent flow and understandable presentation logic.

Each score is on a `1-5` scale.

The script computes:

- `subjective_score`: mean of the three subjective dimensions
- `presentation_quality_score`: same value as `subjective_score` for compatibility and readability

Interpretation:

- this metric group captures overall presentation quality in a paper-aligned way;
- it is intentionally closer to `PresentEval` than to internal engineering-only diagnostics.

### 3. Multimodal Search Quality

This is the explicit extension beyond the original paper design.

We treat multimodal retrieval and multimodal media usage as a distinct capability of our presentation agent, so it deserves its own metric group.

The four dimensions are:

- `media_relevance_score`
- `media_helpfulness_score`
- `media_diversity_score`
- `media_integration_score`

Definitions:

- `media_relevance_score`: whether the selected media are actually relevant to the topic and key ideas.
- `media_helpfulness_score`: whether the media genuinely improve understanding rather than acting as decoration.
- `media_diversity_score`: whether the selected media provide complementary evidence instead of repetitive visuals.
- `media_integration_score`: whether the media are naturally integrated into the slide storyline and narration flow.

Each score is on a `1-5` scale.

The script computes:

- `multimodal_search_quality_score`: mean of the four dimensions

Interpretation:

- this metric isolates the quality of multimodal search and media use, which is a core differentiator of our system.

## Overall Score

The current script uses:

- `0.4 * objective quiz`
- `0.4 * subjective presentation quality`
- `0.2 * multimodal search quality`

Formally:

```text
overall_score =
    0.4 * quiz_score_norm
  + 0.4 * (subjective_score / 5)
  + 0.2 * (multimodal_search_quality_score / 5)
```

This weighting reflects the idea that:

- objective understanding matters most;
- subjective presentation quality matters equally strongly;
- multimodal search quality is important, but is an auxiliary enhancement rather than the sole target.

## Output Files

The script writes:

- `single_presentation_eval_manifest.json`
- `single_presentation_quiz.json`
- `single_presentation_results.json`
- `single_presentation_results.csv`
- one per-sample file: `<paper_dir>__eval.json`

Per-sample outputs include:

- sample id
- subjective scores
- multimodal search scores
- quiz answers and quiz score
- overall score

## Recommended Paper Framing

When writing the paper, describe this evaluation as:

`a PresentEval-style evaluation framework with three components: objective quiz evaluation, subjective presentation scoring, and multimodal search quality assessment.`

That wording is accurate and aligned with our design.

Recommended emphasis:

- The protocol is inspired by the original `PresentEval` design.
- We preserve the quiz-based comprehension evaluation.
- We preserve paper-style subjective scoring over presentation quality.
- We extend the protocol with an additional multimodal search metric group because our system explicitly performs multimodal media retrieval and integration.

## What This Evaluation Measures Well

This protocol is well suited for measuring:

- whether the presentation communicates the source document accurately;
- whether the slide deck looks and reads like a good presentation;
- whether retrieved media are relevant, useful, diverse, and well integrated.

## What This Evaluation Does Not Fully Measure

The current implementation is weaker at measuring:

- full temporal video experience
- exact slide-to-audio synchronization quality
- raw voice naturalness or prosody
- transition smoothness
- animation timing

These limitations should be stated honestly if the evaluation is described in a paper.

## Suggested Wording For The Paper

If a concise evaluation paragraph is needed, the following description is close to the current implementation:

> We evaluate each generated presentation with a PresentEval-style protocol. First, we perform objective quiz evaluation by constructing five multiple-choice questions per sample and answering them using the generated presentation evidence, yielding a comprehension score. Second, we conduct subjective scoring over content quality, visual quality, and comprehension clarity on a 1-5 scale. Finally, because our system explicitly performs multimodal media retrieval and integration, we add a multimodal search quality metric covering media relevance, helpfulness, diversity, and integration. In the current implementation, evaluation evidence consists of presentation notes and sampled slide frames derived from the generated presentation package, together with source-document context from the refined document representation.

## Running The Script

Example:

```powershell
.\.venv-presentagent\Scripts\python.exe scripts/run_single_presentation_eval.py `
  --ppt-root presentation_top20_ppts_new `
  --bundle-root paper_url_to_source_document_batch/presentation_top20_pipeline_bundle `
  --output-dir runs/single_presentation_eval `
  --api-key YOUR_KEY
```

Optional:

- `--limit N` to run a small subset first
- `--api-base` to switch to a different OpenAI-compatible endpoint
- `--text-model` and `--vl-model` to control the judging models

## Current Status

This protocol is good enough for a practical first evaluation pass and for paper drafting, but it should still be described as a `presentation-evidence-based evaluation` rather than a full native video understanding benchmark.
