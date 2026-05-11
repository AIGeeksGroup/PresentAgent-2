"""
Memory management module for PresentAgent.

Provides:
  - Message: dataclass for a single role/content message
  - ConversationMemory: round-based memory with auto-summarization
  - ContextSummarizer: LLM-based conversation compressor
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from typing import Callable

from . import config


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """A single message in the conversation history."""
    role: str       # "user" | "assistant" | "system"
    content: str


# ---------------------------------------------------------------------------
# ContextSummarizer
# ---------------------------------------------------------------------------

class ContextSummarizer:
    """
    Compresses a list of messages into a concise summary using the LLM.

    The summarizer calls the underlying LLM adapter (from config) with a
    special system prompt instructing it to produce a brief summary of the
    conversation so far. This keeps older rounds from consuming token budget.
    """

    def __init__(self, adapter=None):
        """
        Args:
            adapter: an LLM adapter that exposes a .chat(messages) -> str method.
                     If None, summarization falls back to a rule-based approach.
        """
        self.adapter = adapter

    def _rule_based_summary(self, messages: list[Message]) -> str:
        """
        Fallback summarizer when no LLM adapter is available.
        Extracts key topics and questions from the message history.
        """
        if not messages:
            return ""

        lines = []
        for m in messages:
            prefix = "User" if m.role == "user" else "Assistant"
            # Truncate each message to 200 chars to keep summary short
            snippet = m.content[:200].replace("\n", " ").strip()
            lines.append(f"- {prefix}: {snippet}")

        return (
            "Conversation so far (summary of recent messages):\n"
            + "\n".join(lines)
        )

    def _build_summary_prompt(
        self, system_prompt: str, messages: list[Message]
    ) -> list[dict]:
        """Build the messages list for the summarization LLM call."""
        history_text = []
        for m in messages:
            prefix = "User" if m.role == "user" else "Assistant"
            history_text.append(f"{prefix}: {m.content}")

        history_block = "\n\n".join(history_text)

        summary_instruction = textwrap.dedent(f"""
        You are a conversation summarizer. Given the conversation below,
        produce a concise summary (3-5 sentences max) that captures:
        1. The main topic or question discussed
        2. Key information or answers provided
        3. Any outstanding questions or unresolved points

        Be extremely concise. The summary will be prepended to the next
        conversation turn to provide context.

        CONVERSATION:
        {history_block}

        SUMMARY:
        """).strip()

        return [
            {"role": "system", "content": system_prompt[:2000]},
            {"role": "user", "content": summary_instruction},
        ]

    def summarize_conversation(
        self, system_prompt: str, messages: list[Message]
    ) -> str:
        """
        Produce a concise summary of the conversation.

        Args:
            system_prompt: The agent's system prompt (used as context for the summary)
            messages: List of Message objects representing the full conversation

        Returns:
            A short string summary of the conversation.
        """
        if not messages:
            return ""

        # If no adapter, fall back to rule-based summarization
        if self.adapter is None:
            return self._rule_based_summary(messages)

        try:
            llm_messages = self._build_summary_prompt(system_prompt, messages)
            summary = self.adapter.chat(llm_messages)
            return summary if summary else self._rule_based_summary(messages)
        except Exception:
            # If LLM summarization fails, fall back gracefully
            return self._rule_based_summary(messages)


# ---------------------------------------------------------------------------
# ConversationMemory
# ---------------------------------------------------------------------------

class ConversationMemory:
    """
    Round-based conversation memory with automatic summarization.

    Stores user/assistant rounds and manages a compressed summary of older
    rounds to prevent token overflow. When the number of stored rounds
    exceeds ``round_threshold``, older rounds are summarized and replaced
    with a single summary entry.

    Attributes:
        rounds: List of (user_input, assistant_reply) tuples for recent turns.
        summary: A compressed string summarizing all older (summarized) rounds.
        system_prompt: The agent's system prompt (preserved for context).
    """

    def __init__(
        self,
        system_prompt: str,
        round_threshold: int = 10,
        carryover_rounds: int = 2,
    ):
        """
        Args:
            system_prompt: The agent's system prompt (used in summarization calls).
            round_threshold: Number of rounds to retain before triggering
                            summarization of older rounds.
            carryover_rounds: How many of the most recent rounds to keep
                              untouched (not summarized) alongside the summary.
        """
        self.system_prompt = system_prompt
        self.round_threshold = round_threshold
        self.carryover_rounds = carryover_rounds

        self.rounds: list[tuple[str, str]] = []   # (user, assistant)
        self.summary: str = ""                     # compressed older history
        self._last_audio_path: str | None = None  # path of last audio

        self._summarizer: ContextSummarizer | None = None
        self._summarize_fn: Callable[[str, list[Message]], str] | None = None

    def set_summarizer(self, fn: Callable[[str, list[Message]], str]) -> None:
        """
        Register a summarization callback.

        Args:
            fn: A callable that accepts (system_prompt, list[Message]) and
                returns a summary string.
        """
        self._summarize_fn = fn

    def _build_message_list(self) -> list[Message]:
        """
        Reconstruct a flat message list from rounds + summary.

        The summary (if any) is represented as a single "system" message
        prepended to the list, followed by the carryover rounds.
        """
        messages: list[Message] = []

        if self.summary:
            messages.append(Message(role="system", content=self.summary))

        for user_msg, assistant_msg in self.rounds:
            messages.append(Message(role="user", content=user_msg))
            messages.append(Message(role="assistant", content=assistant_msg))

        return messages

    def get_messages_for_llm(self) -> list[Message]:
        """
        Return the full message history as a list of Message objects.

        This is what gets passed to the LLM each turn.
        """
        return self._build_message_list()

    def add_round(self, user_input: str, assistant_reply: str) -> None:
        """
        Record a single conversation round.

        After adding, if the number of rounds exceeds ``round_threshold``,
        older rounds are summarized and replaced with a compact summary.

        Args:
            user_input: The user's message.
            assistant_reply: The assistant's reply.
        """
        self.rounds.append((user_input, assistant_reply))

        if len(self.rounds) > self.round_threshold:
            self._compress()

    def _compress(self) -> None:
        """
        Compress older rounds into a summary.

        Keeps the most recent ``carryover_rounds`` untouched;
        summarizes everything before that into ``self.summary``.
        """
        if len(self.rounds) <= self.carryover_rounds:
            return

        rounds_to_summarize = self.rounds[: -self.carryover_rounds]
        retained_rounds = self.rounds[-self.carryover_rounds :]

        # Build flat message list for the summarizer
        messages_for_summary: list[Message] = []
        for user_msg, assistant_msg in rounds_to_summarize:
            messages_for_summary.append(Message(role="user", content=user_msg))
            messages_for_summary.append(Message(role="assistant", content=assistant_msg))

        if self._summarize_fn is not None and messages_for_summary:
            try:
                new_summary = self._summarize_fn(self.system_prompt, messages_for_summary)
                # Prepend new summary to any existing summary
                if self.summary:
                    self.summary = (
                        "Previous summary:\n" + self.summary + "\n\n" + new_summary
                    )
                else:
                    self.summary = new_summary
            except Exception:
                # Summarization failed; keep rounds as-is (they will still be sent)
                pass

        # Keep only the carryover rounds
        self.rounds = retained_rounds

    def set_last_audio_path(self, path: str | None) -> None:
        self._last_audio_path = path

    def get_last_audio_path(self) -> str | None:
        return self._last_audio_path

    def reset_memory(self) -> None:
        """Clear all stored rounds and summaries."""
        self.rounds = []
        self.summary = ""
        self._last_audio_path = None

    @property
    def memory_size(self) -> int:
        """Return the approximate number of stored rounds."""
        return len(self.rounds)
