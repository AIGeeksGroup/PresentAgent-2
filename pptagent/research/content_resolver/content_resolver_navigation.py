from __future__ import annotations

import json
import re

from .content_resolver_models import ContentLinkCandidate, MediaStats, _HtmlAssessment


class ContentResolverNavigationMixin:
    def _select_child_candidate(
        self,
        *,
        page_url: str,
        html_text: str,
        llm_decision: dict[str, object],
        candidates: list[ContentLinkCandidate],
        assessment: _HtmlAssessment,
        media_stats: MediaStats,
        topic: str,
        goal: str,
        summary_hint: str,
    ) -> ContentLinkCandidate | None:
        selected_url = str(llm_decision.get("content_url") or "").strip()
        if selected_url:
            for candidate in candidates:
                if candidate.url == selected_url:
                    return candidate

        needs_better_content = not self._has_enough_continuous_content(assessment)
        needs_better_media = media_stats.total_visual_count == 0 and media_stats.direct_media_url_count == 0
        if not needs_better_content and not needs_better_media:
            return None

        high_value_pattern = re.compile(
            r"(demo|animation|animated|interactive|visual|video|media|explainer|tutorial|guide|notebook|walkthrough)",
            re.IGNORECASE,
        )
        topic_tokens = {token for token in re.findall(r"[a-z0-9]+", topic.lower()) if len(token) >= 4}
        ranked_candidates: list[tuple[tuple[int, int, int], ContentLinkCandidate]] = []
        for candidate in candidates:
            haystack = f"{candidate.url} {candidate.anchor_text}".lower()
            ranked_candidates.append(
                (
                    (
                        1 if high_value_pattern.search(haystack) else 0,
                        sum(1 for token in topic_tokens if token in haystack),
                        len(candidate.anchor_text or ""),
                    ),
                    candidate,
                )
            )
        ranked_candidates.sort(key=lambda item: item[0], reverse=True)
        if ranked_candidates and ranked_candidates[0][0][0] > 0:
            return ranked_candidates[0][1]
        return None

    def _decide_html_next_step(
        self,
        *,
        page_url: str,
        html_text: str,
        assessment: _HtmlAssessment,
        candidates: list[ContentLinkCandidate],
        topic: str,
        goal: str,
        summary_hint: str,
    ) -> dict[str, object]:
        if not self.llm_enabled:
            return {"use_current_page": False, "content_url": None}

        excerpt = (assessment.full_text or "").strip()
        if self.llm_excerpt_chars is not None and len(excerpt) > self.llm_excerpt_chars:
            excerpt = excerpt[: self.llm_excerpt_chars]
        if not excerpt:
            fallback_text = (assessment.best_run_text or "").strip()
            if self.llm_excerpt_chars is not None and len(fallback_text) > self.llm_excerpt_chars:
                fallback_text = fallback_text[: self.llm_excerpt_chars]
            excerpt = fallback_text

        candidate_payload = [
            {
                "url": candidate.url,
                "anchor_text": candidate.anchor_text,
            }
            for candidate in (
                candidates[: self.llm_max_candidates]
                if self.llm_max_candidates is not None
                else candidates
            )
        ]
        prompt = (
            "You are choosing the best content source from a visited webpage.\n"
            "The current page is not a PDF/document, so you must judge whether the current HTML page already contains complete enough content to serve as the final source.\n"
            "You are given the statically parsed page text and all direct child links found on the page.\n"
            "If the current page is not complete enough, select the single direct child URL most likely to contain the full content.\n"
            "Prefer the primary content source, not related links, code repos, author pages, navigation, or footer links.\n"
            "Return JSON only in this format:\n"
            '{"use_current_page": true/false, "content_url": "url or null"}\n\n'
            f"Current URL: {page_url}\n"
            f"Topic: {topic}\n"
            f"Goal: {goal}\n"
            f"Summary hint: {summary_hint}\n"
            f"Parsed HTML text:\n{excerpt}\n\n"
            f"Longest continuous body-text run length: {assessment.best_run_char_count}\n"
            f"Longest continuous body-text run blocks: {assessment.best_run_block_count}\n"
            f"Total parsed text length: {assessment.total_text_length}\n\n"
            f"Candidate links:\n{json.dumps(candidate_payload, ensure_ascii=False, indent=2)}"
        )
        return self._normalize_decision(self._call_llm_decider(prompt))

    def _call_llm_decider(self, prompt: str) -> dict | str | None:
        if not self.llm_model:
            return None
        try:
            from openai import OpenAI
        except Exception:
            return None

        try:
            client = OpenAI(
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                timeout=self.timeout,
            )
            completion = client.chat.completions.create(
                model=self.llm_model,
                messages=[
                    {"role": "system", "content": "You are a precise web-content routing assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
            )
            content = (completion.choices[0].message.content or "").strip()
        except Exception:
            return None

        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            left = content.find("{")
            right = content.rfind("}")
            if left != -1 and right != -1 and left <= right:
                try:
                    return json.loads(content[left : right + 1])
                except json.JSONDecodeError:
                    return None
        return None

    def _normalize_decision(self, raw_decision: dict | str | None) -> dict[str, object]:
        if raw_decision is None:
            return {"use_current_page": False, "content_url": None}
        if isinstance(raw_decision, str):
            selected = raw_decision.strip()
            return {
                "use_current_page": selected.upper() == "USE_CURRENT",
                "content_url": None if selected.upper() == "USE_CURRENT" or not selected else selected,
            }
        if isinstance(raw_decision, dict):
            use_current_page = bool(raw_decision.get("use_current_page", False))
            content_url = raw_decision.get("content_url")
            if isinstance(content_url, str):
                content_url = content_url.strip() or None
            else:
                content_url = None
            return {
                "use_current_page": use_current_page,
                "content_url": content_url,
            }
        return {"use_current_page": False, "content_url": None}
