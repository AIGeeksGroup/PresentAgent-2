import json

from pptagent.research import DeepResearchAdapter, ResearchDossier


def test_deepresearch_raw_result_to_dossier(tmp_path):
    result_path = tmp_path / "iter1.jsonl"
    payload = {
        "question": "talk about flow matching",
        "prediction": "# Core Idea\nFlow matching learns a vector field.\n- It can be explained as transport.\n\n# Evidence\nSee the linked paper and demo.",
        "termination": "answer",
        "messages": [
            {
                "role": "user",
                "content": '<tool_response>\n1. [Flow Matching Paper](https://arxiv.org/abs/2210.02747)\n2. [Demo Repo](https://github.com/example/flow-matching-demo)\n3. [Animation](https://x.com/example/status/123)\n</tool_response>',
            }
        ],
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")

    adapter = DeepResearchAdapter()
    dossier = adapter.load_raw_result(str(result_path))

    assert dossier.topic == "talk about flow matching"
    assert dossier.summary.startswith("# Core Idea")
    assert len(dossier.sources) == 3
    assert dossier.sources[0].source_type == "paper"
    assert dossier.sources[1].source_type == "repo"
    assert len(dossier.media_candidates) == 1
    assert dossier.best_outline is not None
    assert dossier.best_outline.sections[0].title == "Core Idea"


def test_research_dossier_to_document(tmp_path):
    dossier = ResearchDossier.from_dict(
        {
            "topic": "talk about flow matching",
            "summary": "Flow matching is a generative modeling method.",
            "sources": [
                {
                    "title": "Flow Matching Paper",
                    "url": "https://arxiv.org/abs/2210.02747",
                    "snippet": "Original paper",
                    "source_type": "paper",
                }
            ],
            "outline_candidates": [
                {
                    "title": "Flow Matching Talk",
                    "sections": [
                        {
                            "title": "Background",
                            "summary": "Why transport-based generation matters.",
                            "bullet_points": [
                                "It reframes generation as vector-field learning.",
                                "It provides an intuitive geometric story.",
                            ],
                        }
                    ],
                }
            ],
            "media_candidates": [],
            "metadata": {"mode": "topic"},
        }
    )

    adapter = DeepResearchAdapter()
    document = adapter.dossier_to_document(dossier, str(tmp_path))

    assert document.metadata["title"] == "talk about flow matching"
    assert document.sections[0].title == "Background"
    assert document.sections[0].subsections[0].title == "Background Summary"
    assert document.sections[-1].title == "Sources"
    assert "Flow Matching Paper" in document.get_overview()
