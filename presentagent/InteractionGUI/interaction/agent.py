"""
PresentAgent — Interactive Q&A with context from source.md.

Architecture: Single ConversableAgent (AutoGen ag2 0.12+) with
auto-summarizing conversation memory and source.md knowledge base.

Core flow:
    User input
      -> build context (memory summary + source.md + current question)
      -> _call_llm_direct()  [DashScope qwen3.5-omni-flash, text + audio]
      -> ConversationMemory.add_round()
      -> auto-summarization if round_threshold exceeded
      -> return reply
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# AutoGen ag2 import — both 'autogen' and 'ag2' resolve to the same package
# ---------------------------------------------------------------------------
try:
    import autogen
    from autogen import ConversableAgent
    AUTOGEN_AVAILABLE = True
except ImportError as exc:
    AUTOGEN_AVAILABLE = False
    print(f"[ERROR] AutoGen (ag2) not installed: {exc}")
    print("[HINT] Run: pip install ag2")
    sys.exit(1)

from openai import OpenAI

import tiktoken
from . import config
from .memory import ConversationMemory, ContextSummarizer, Message
from .document_processor import (
    needs_video_seek,
    find_best_position,
    find_position_by_llm,
    get_cached_index,
    set_cached_index,
    load_cached_index,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Max characters of source.md to include in system prompt (safety guard)
MAX_SOURCE_CHARS = 100_000

# Default context window budget for the target model.
# 90 % of MiniMax-M2.7's 100 K context, leaving headroom for the reply.
DEFAULT_MAX_CONTEXT_TOKENS = 90_000

# Reserve tokens for the model's reply (input/output budget split)
_TOKENS_RESERVED_FOR_REPLY = 1024


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_wav_from_pcm_chunks(output_path: str, base64_chunks: list[str]) -> None:
    """
    Decode base64-encoded PCM audio chunks and write a proper WAV file.

    The DashScope API returns raw PCM samples (16-bit, 24kHz mono) as base64.
    This function assembles the chunks, prepends a proper WAV RIFF header, and
    writes a valid .wav file that audio players can open.

    Args:
        output_path: Path where the .wav file will be saved.
        base64_chunks: List of base64-encoded audio data strings.
    """
    import base64
    import wave

    # Concatenate all PCM chunks
    full_b64 = "".join(base64_chunks)
    pcm_data = base64.b64decode(full_b64)

    # DashScope qwen3.5-omni-flash uses 24kHz 16-bit mono PCM
    SAMPLE_RATE = 24000
    NUM_CHANNELS = 1
    BITS_PER_SAMPLE = 16
    BYTES_PER_SAMPLE = BITS_PER_SAMPLE // 8

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(NUM_CHANNELS)
        wav_file.setsampwidth(BYTES_PER_SAMPLE)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm_data)


def _extract_text_from_value(value, depth=0) -> list[str]:
    """
    Recursively extract all meaningful text strings from a nested JSON value.

    Targets these keys specifically (case-insensitive):
      content, summary, markdown_content, notes, description, text,
      caption, abstract, introduction, conclusion, title

    For list items, recurses into dicts. Stops recursing into nested lists
    beyond depth 2 to avoid degenerate data (e.g. long arrays of primitives).
    """
    STRIP_KEYS = {"image_dir", "path", "url", "src", "href", "media_type",
                  "assets", "url_to_source", "presentation-date"}
    TARGET_KEYS = {"content", "summary", "markdown_content", "notes",
                    "description", "text", "caption", "abstract",
                    "introduction", "conclusion", "title"}

    parts: list[str] = []

    if isinstance(value, str) and value.strip():
        parts.append(value.strip())
    elif isinstance(value, dict):
        for k, v in value.items():
            k_lower = k.lower()
            if k_lower in STRIP_KEYS:
                continue
            if k_lower in TARGET_KEYS:
                if isinstance(v, str) and v.strip():
                    parts.append(v.strip())
                elif isinstance(v, list):
                    parts.extend(_extract_text_from_value(v, depth + 1))
            elif isinstance(v, (dict, list)):
                if depth < 2:
                    parts.extend(_extract_text_from_value(v, depth + 1))
    elif isinstance(value, list) and depth < 2:
        for item in value:
            parts.extend(_extract_text_from_value(item, depth + 1))

    return parts


def _load_json(path: Path) -> tuple[str, str | None]:
    """
    Load and parse a JSON source file.

    Recursively extracts meaningful text from nested structures,
    targeting fields such as: content, summary, markdown_content,
    notes, description, text, caption, abstract, introduction,
    conclusion, title.

    Returns (combined_text, resolved_path).
    """
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        print(f"[WARN] Failed to read {path.name}: {exc}")
        return "", None

    parts = _extract_text_from_value(data)
    if not parts:
        print(f"[WARN] No extractable text found in {path.name}.")
        return "", str(path.resolve())

    return "\n\n".join(parts), str(path.resolve())


def load_source_md(path: str | None = None) -> tuple[str, str | None]:
    """
    Load the content of a source knowledge-base file (.md or .json).

    Args:
        path: Optional explicit path. Defaults to config.get_source_md_path().
              If not found, auto-searches in source/ directories.

    Returns:
        A tuple of (content, actual_path) where actual_path is the resolved
        file path, or (empty_string, None) if not found.
    """
    import json as _json

    if path is None:
        path = config.get_source_md_path()

    file_path = Path(path)
    if not file_path.exists():
        # Auto-search in source/ directories — prefer .md over .json
        project_root = Path(__file__).parent.parent
        candidates: list[Path] = []
        candidates.extend(project_root.glob("source/*/source.md"))
        candidates.extend(project_root.glob("source/*/slide_notes.json"))
        for candidate in candidates:
            file_path = candidate
            print(f"[INFO] Auto-found source at: {file_path.resolve()}")
            break
        else:
            print(f"[WARN] Source file not found: {Path(path).resolve()}")
            return "", None

    try:
        suffix = file_path.suffix.lower()
        if suffix == ".json":
            content, resolved = _load_json(file_path)
        else:
            content = file_path.read_text(encoding="utf-8")
            resolved = str(file_path.resolve())
    except Exception as exc:
        print(f"[WARN] Failed to read {file_path.name}: {exc}")
        return "", None

    if not content.strip():
        print(f"[WARN] {file_path.name} is empty.")
        return "", resolved

    # Hard cap to prevent system prompt overflow
    if len(content) > MAX_SOURCE_CHARS:
        content = content[:MAX_SOURCE_CHARS] + "\n\n[... content truncated ...]"

    return content, resolved


def build_system_prompt(source_content: str, memory_summary: str = "", source_label: str = "source.md") -> str:
    """
    Assemble the system prompt each turn.

    The prompt always includes:
      1. Role instructions
      2. The source knowledge base (if available)
      3. A compressed summary of earlier conversation (if available)

    Args:
        source_content: Raw text of the source file.
        memory_summary: Compressed summary from ConversationMemory.summary.
        source_label: Human-readable label for the source (e.g. "source.md", "slide_notes.json").

    Returns:
        The fully formatted system prompt string.
    """
    parts = []

    # --- Role & behaviour ---
    parts.append(textwrap.dedent("""
        You are a helpful assistant that speaks naturally and directly.
        Answer questions in a conversational way, as if you're explaining topics to a friend.
        Be concise but informative. Never start with phrases like "Based on the document" or "The document does not mention".

        IMPORTANT — RESPONSE FORMAT (CRITICAL):
        Your response will be converted to speech for text-to-speech synthesis.
        ALL MARKDOWN FORMATTING IS STRICTLY FORBIDDEN.

        FORBIDDEN PATTERNS (do not use any of these):
        - Markdown headings: ###, ##, #, or any line starting with #
        - Bold text: **text**, __text__
        - Italic text: *text*, _text_
        - Strikethrough: ~~text~~
        - Code blocks: ``` or `code`
        - LaTeX/Math: dollar signs, parentheses, or any math notation
        - Tables: | ... | ... | or any pipe characters
        - ASCII art, boxes, or visual separators
        - Emojis or special Unicode symbols

        ALLOWED FORMATS:
        - Plain paragraphs in natural conversational language
        - Simple bullet points using "-" or "*" (no bold/italic inside bullets)
        - Numbers in parentheses or "First, Second, Third" for lists

        Write as if you are speaking to someone in a podcast or radio interview.
        Be conversational, clear, and avoid任何 formatted text.
    """).strip())

    # --- Document content ---
    source_label = "source.md" if source_label == "md" else source_label
    if source_content:
        parts.append(
            f"--- DOCUMENT CONTENT ({source_label}) ---\n"
            f"{source_content}\n"
            f"--- END OF DOCUMENT ---"
        )
    else:
        parts.append(
            "[NOTE] No knowledge base is available. "
            "Answer from general knowledge if needed."
        )

    # --- Conversation summary (from earlier rounds) ---
    if memory_summary:
        parts.append(
            f"--- PRIOR CONVERSATION SUMMARY ---\n"
            f"{memory_summary}\n"
            f"--- END OF SUMMARY ---"
        )

    return "\n\n".join(parts)


def print_welcome() -> None:
    print("=== PresentAgent (AutoGen ag2) ===")
    print("Ask questions about the document. Type 'exit' or 'quit' to end.")
    print("Commands: 'summary' to view memory summary, 'reset' to clear memory.\n")


# ---------------------------------------------------------------------------
# Token counter
# ---------------------------------------------------------------------------
# Singleton tiktoken encoder (cl100k_base covers GPT-4 / Claude / MiniMax tokens)

_encoder: tiktoken.Encoding | None = None


def _get_encoder() -> tiktoken.Encoding:
    global _encoder
    if _encoder is None:
        _encoder = tiktoken.get_encoding("cl100k_base")
    return _encoder


def count_tokens(text: str) -> int:
    """Return the approximate token count of a string."""
    return len(_get_encoder().encode(text))


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """
    Return the approximate token count of a list of message dicts.

    Adds a 4-token overhead per message to account for role / content
    framing, matching the tiktoken counting convention used by OpenAI.
    """
    encoder = _get_encoder()
    total = 0
    for msg in messages:
        total += 4  # overhead per message
        total += len(encoder.encode(msg.get("content", "")))
        total += len(encoder.encode(msg.get("role", "")))
    return total


# ---------------------------------------------------------------------------
# PresentAgent
# ---------------------------------------------------------------------------

class PresentAgent:
    """
    Single-agent interactive Q&A system.

    Uses one ConversableAgent (no groupchat, no UserProxyAgent) that
    sends messages to itself via initiate_chat — the canonical AutoGen
    single-agent pattern.
    """

    def __init__(
        self,
        source_md_path: str | None = None,
        round_threshold: int = 10,
        carryover_rounds: int = 2,
        temperature: float | None = None,
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    ):
        """
        Initialize PresentAgent.

        Args:
            source_md_path: Optional override for the source.md path.
            round_threshold: Rounds to keep before triggering summarization.
            carryover_rounds: Recent rounds to keep untouched after summarization.
            temperature: Override LLM temperature (uses config default if None).
            max_context_tokens: Maximum input tokens allowed before the auto-reset
                                is triggered. Set to None to disable the guard.
        """
        self.source_content, resolved_path = load_source_md(source_md_path)
        self.max_context_tokens = max_context_tokens
        self._last_audio_path: str | None = None
        self._last_video_position: float | None = None
        self._source_md_path = resolved_path

        # Derive a human-readable label for the source (e.g. "source.md", "slide_notes.json")
        if resolved_path:
            self._source_label = Path(resolved_path).name
        else:
            self._source_label = "source.md"

        # Try to load cached sentence index for video positioning
        self._sentence_index = load_cached_index()

        # If no cached index and source.md exists, build one
        if self._sentence_index is None and self.source_content:
            from .document_processor import DocumentProcessor, save_cached_index
            try:
                processor = DocumentProcessor()
                doc_path = resolved_path or source_md_path or config.get_source_md_path()
                self._sentence_index = processor.build_index(self.source_content, doc_path)
                save_cached_index(self._sentence_index)
                print(f"[INFO] Built sentence index with {len(self._sentence_index.sentences)} sentences")
            except Exception as e:
                print(f"[WARN] Failed to build sentence index: {e}")
                self._sentence_index = None

        # Build initial system prompt (will be refreshed each turn)
        self.system_prompt = build_system_prompt(self.source_content, "", self._source_label)

        # --- LLM config ---
        llm_config = config.get_llm_config()
        if temperature is not None:
            llm_config["temperature"] = temperature

        # --- Memory ---
        self.memory = ConversationMemory(
            system_prompt=self.system_prompt,
            round_threshold=round_threshold,
            carryover_rounds=carryover_rounds,
        )

        # ContextSummarizer uses the same LLM as the main agent
        self._adapter = _LLMAdapter(llm_config)
        self.summarizer = ContextSummarizer(adapter=self._adapter)
        self.memory.set_summarizer(self._summarize_wrapper)

        # --- AutoGen ConversableAgent (single agent, no groupchat) ---
        self.assistant = ConversableAgent(
            name="assistant",
            system_message=self.system_prompt,
            llm_config=llm_config,
            human_input_mode="NEVER",          # Fully autonomous
            code_execution_config=False,       # No code execution
        )

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _summarize_wrapper(self, system_prompt: str, messages: list[Message]) -> str:
        """Bridge between ConversationMemory and ContextSummarizer."""
        return self.summarizer.summarize_conversation(system_prompt, messages)

    def _refresh_system_message(self) -> None:
        """
        Refresh the assistant's system message with current
        source_content + memory summary.

        Called before every chat turn to ensure the agent always sees
        the latest knowledge base and conversation summary.
        """
        self.system_prompt = build_system_prompt(
            self.source_content,
            self.memory.summary,
            self._source_label,
        )
        self.assistant.update_system_message(self.system_prompt)

    def _call_llm_direct(self, messages: list[dict[str, Any]]) -> tuple[str, str | None]:
        """
        Call the LLM via DashScope (OpenAI-compatible) with streaming + audio output.

        Uses qwen3.5-omni-flash with modalities=["text", "audio"] to get both
        text response and synthesized speech in a single API call.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.

        Returns:
            A tuple (text_reply, audio_path) where audio_path is the path to
            the saved WAV file, or None if audio synthesis failed.
        """
        import base64
        import time

        text_parts = []

        # Prepend system message to ensure formatting rules are applied
        llm_messages = [{"role": "system", "content": self.system_prompt}]
        llm_messages.extend(
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("content")
        )

        # Create output folder for this round
        output_dir = config.get_tts_output_dir()
        round_id = int(time.time() * 1000)
        round_dir = os.path.join(output_dir, f"round_{round_id}")
        os.makedirs(round_dir, exist_ok=True)

        try:
            client = OpenAI(
                api_key=config.get_api_key(),
                base_url=config.get_provider_config().get("base_url"),
            )

            completion = client.chat.completions.create(
                model=config.get_model(),
                messages=llm_messages,
                modalities=["text", "audio"],
                audio={"voice": config.get_tts_voice(), "format": "wav"},
                max_tokens=1024,
                stream=True,
                stream_options={"include_usage": True},
            )

            audio_index = 0
            last_audio_path = None
            all_audio_parts = []

            for chunk in completion:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text_parts.append(delta.content)
                    audio_dict = getattr(delta, "audio", None)
                    if isinstance(audio_dict, dict) and audio_dict.get("data"):
                        all_audio_parts.append(audio_dict["data"])

            # Write all accumulated audio parts as a single valid WAV file
            if all_audio_parts:
                audio_path = os.path.join(round_dir, f"reply_{audio_index:03d}.wav")
                _write_wav_from_pcm_chunks(audio_path, all_audio_parts)
                last_audio_path = audio_path

            return "".join(text_parts), last_audio_path

        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[ERROR] LLM call failed: {exc}")
            return "", None

    def _build_llm_messages(
        self, user_input: str
    ) -> list[dict[str, Any]]:
        """
        Build the message list to send to the LLM.

        Injects the conversation history (summary + carryover rounds) as
        prior messages, followed by the current user turn.
        """
        messages: list[dict[str, Any]] = []

        # Prior conversation messages
        for msg in self.memory.get_messages_for_llm():
            messages.append({"role": msg.role, "content": msg.content})

        # Current user turn
        messages.append({"role": "user", "content": user_input})
        return messages

    # -------------------------------------------------------------------------
    # Public chat API
    # -------------------------------------------------------------------------

    def chat(self, user_input: str) -> str:
        """
        Process a user question and return the assistant's answer.

        This is the main entry point for a single turn.

        Flow:
            1. Refresh system message (source.md + memory summary)
            2. Check token budget; auto-reset memory if exceeded
            3. Build message history (summary + recent rounds + current input)
            4. Call DashScope via _call_llm_direct; receives (text, audio_path).
            5. Record round in memory (triggers auto-summarization).
            6. Return clean reply.

        Args:
            user_input: The user's question.

        Returns:
            The assistant's answer as a string.
        """
        self._refresh_system_message()

        if self.max_context_tokens is not None:
            self._check_and_reset_if_over_token_limit(user_input)

        messages = self._build_llm_messages(user_input)

        reply, audio_path = self._call_llm_direct(messages)

        self.memory.add_round(user_input, reply)

        if audio_path:
            self.memory.set_last_audio_path(audio_path)

        return reply

    def chat_with_audio(self, user_input: str) -> tuple[str, str, float | None]:
        """
        Same as `chat()`, but also returns the audio path from the LLM.

        The audio is already synthesized and saved by _call_llm_direct,
        so this just retrieves the stored path.

        Returns:
            A tuple (reply_text, audio_path, video_position) where audio_path
            may be None if audio synthesis failed, and video_position is a
            ratio (0.0-1.0) indicating where the video should seek to, or None.
        """
        self._refresh_system_message()

        if self.max_context_tokens is not None:
            self._check_and_reset_if_over_token_limit(user_input)

        messages = self._build_llm_messages(user_input)

        reply, audio_path = self._call_llm_direct(messages)

        self.memory.add_round(user_input, reply)

        if audio_path:
            self.memory.set_last_audio_path(audio_path)

        # Calculate video position for relevant questions
        # Use LLM-based position finding (more reliable than sentence embeddings)
        # Apply 10% margin on both ends to avoid edge cases (skip intro/outro)
        video_position = None
        if self.source_content and len(self.source_content) > 100:
            try:
                position = find_position_by_llm(
                    question=user_input,
                    source_content=self.source_content,
                    api_key=config.get_api_key(),
                    base_url=config.get_provider_config().get("base_url"),
                    model=config.get_model(),
                )
                if position is not None:
                    # Clamp position to 10%-90% range to avoid edge cases
                    video_position = max(0.10, min(0.90, position))
                    self._last_video_position = video_position
                    print(f"[INFO] Video position set to {video_position:.3f} for question: {user_input[:50]}...")
            except Exception as e:
                print(f"[WARN] LLM position lookup failed: {e}")
                # Fallback to cached index if available
                index = self._sentence_index or get_cached_index()
                if index and index.sentences:
                    _, video_position = find_best_position(user_input, index)
                    # Clamp position to 10%-90% range to avoid edge cases
                    video_position = max(0.10, min(0.90, video_position)) if video_position is not None else None
                    self._last_video_position = video_position

        return reply, audio_path, video_position

    def get_video_position(self) -> float | None:
        """Return the video position ratio (0.0-1.0) for the last question, or None."""
        return self._last_video_position

    def set_sentence_index(self, index) -> None:
        """Set the sentence index for video positioning."""
        self._sentence_index = index
        if index:
            set_cached_index(index)

    def get_last_reply_audio(self, output_dir: str | None = None) -> str | None:
        """
        Return the audio path for the last assistant reply.

        Returns:
            The audio file path stored by _call_llm_direct, or None.
        """
        return self.memory.get_last_audio_path()

    def _check_and_reset_if_over_token_limit(self, user_input: str) -> None:
        """
        Estimate total input tokens and reset memory if over budget.

        The estimate covers:
          - system prompt (role + behaviour + source.md + current summary)
          - conversation history (carryover rounds)
          - current user input

        If the total exceeds ``max_context_tokens``, the memory is
        automatically cleared and a warning is printed.
        """
        budget = self.max_context_tokens - _TOKENS_RESERVED_FOR_REPLY

        system_tokens = count_tokens(self.system_prompt)
        history_tokens = count_messages_tokens(
            [{"role": m.role, "content": m.content}
             for m in self.memory.get_messages_for_llm()]
        )
        input_tokens = count_tokens(user_input)

        total = system_tokens + history_tokens + input_tokens

        if total > budget:
            print(
                f"[WARN] Context token estimate ({total}) exceeds budget ({budget}). "
                "Auto-resetting memory."
            )
            self.memory.reset_memory()

    def _extract_reply(self, chat_result) -> str:
        """
        Pull the assistant's text reply from a ChatResult object.

        Strategy:
            1. Use chat_result.summary if available (AutoGen's own summary).
            2. Fall back to scanning chat_history for the last assistant message.

        Args:
            chat_result: The ChatResult returned by initiate_chat.

        Returns:
            The assistant's reply text, or an empty string.
        """
        # AutoGen's summary is the most reliable extraction point
        if hasattr(chat_result, 'summary') and chat_result.summary:
            return str(chat_result.summary)

        # Fallback: last assistant message in chat_history
        if hasattr(chat_result, 'chat_history') and chat_result.chat_history:
            for msg in reversed(chat_result.chat_history):
                if (
                    isinstance(msg, dict)
                    and msg.get("role") == "assistant"
                    and msg.get("content")
                    and len(msg["content"]) > 10
                ):
                    return str(msg["content"])

        return ""

    def get_summary(self) -> str:
        """Return the current conversation memory summary."""
        if self.memory.summary:
            return f"[Memory Summary]\n{self.memory.summary}"
        return "[No summary yet — conversation just started.]"

    def reset_memory(self) -> None:
        """Clear all conversation rounds and summaries."""
        self.memory.reset_memory()
        print("[INFO] Memory reset.")


# ---------------------------------------------------------------------------
# _LLMAdapter
# ---------------------------------------------------------------------------
# Thin adapter so ContextSummarizer can call the LLM without depending
# on the full PresentAgent / ConversableAgent machinery.

class _LLMAdapter:
    """
    Wraps an AutoGen llm_config dict and exposes a .chat(messages) interface.

    This lets ContextSummarizer reuse the same model endpoint that the main
    ConversableAgent uses, without creating a second agent.
    """

    def __init__(self, llm_config: dict[str, Any]):
        self.llm_config = llm_config

    def chat(self, messages: list[dict[str, Any]]) -> str:
        """
        Send a messages list to the LLM and return the text response.

        Uses autogen.OpenAIWrapper.create() directly for simplicity.
        """
        try:
            client = autogen.OpenAIWrapper(**self.llm_config)
            response = client.create(messages=messages)
            # Extract text from the first choice
            return response.choices[0].message.content or ""
        except Exception as exc:
            print(f"[WARN] LLM call failed in summarizer: {exc}")
            raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print_welcome()

    try:
        agent = PresentAgent()
    except ValueError as exc:
        print(f"[ERROR] Configuration error: {exc}")
        sys.exit(1)

    round_num = 0
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Goodbye]")
            break

        if not user_input:
            continue

        # --- Built-in commands ---
        if user_input.lower() in ("exit", "quit", "q"):
            print("[Goodbye]")
            break

        if user_input.lower() == "summary":
            print(f"\n{agent.get_summary()}\n")
            continue

        if user_input.lower() == "reset":
            agent.reset_memory()
            continue

        # --- Normal Q&A turn ---
        round_num += 1
        reply = agent.chat(user_input)
        print(f"\nAgent (round {round_num}): {reply}\n")


if __name__ == "__main__":
    main()
