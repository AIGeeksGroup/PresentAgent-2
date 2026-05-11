"""
Configuration module for PresentAgent.

Loads LLM provider settings from environment variables.
Supports DashScope (qwen3.5-omni-flash) via OpenAI-compatible API.
Loads .env file automatically if present.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Load .env file automatically
_dotenv_path = Path(__file__).parent.parent / ".env"
if _dotenv_path.exists():
    with open(_dotenv_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# ---------------------------------------------------------------------------
# Environment variable keys
# ---------------------------------------------------------------------------
ENV_AUTH_TOKEN = "ANTHROPIC_AUTH_TOKEN"
ENV_BASE_URL = "ANTHROPIC_BASE_URL"
ENV_MODEL = "ANTHROPIC_MODEL"
ENV_PROVIDER = "ANTHROPIC_PROVIDER"
ENV_TEMPERATURE = "ANTHROPIC_TEMPERATURE"
ENV_SOURCE_MD_PATH = "SOURCE_MD_PATH"
ENV_TTS_OUTPUT_DIR = "TTS_OUTPUT_DIR"
ENV_SENTENCE_MODEL = "SENTENCE_MODEL"
ENV_CACHE_DIR = "CACHE_DIR"
ENV_TTS_VOICE = "TTS_VOICE"

# ---------------------------------------------------------------------------
# Provider defaults
# ---------------------------------------------------------------------------
DEFAULT_PROVIDER = "dashscope"
DEFAULT_MODEL = "qwen3.5-omni-flash"
DEFAULT_TEMPERATURE = 0.7
DEFAULT_SOURCE_MD_PATH = "./source.md"
DEFAULT_TTS_OUTPUT_DIR = "./tts_output"
DEFAULT_SENTENCE_MODEL = "all-MiniLM-L6-v2"
DEFAULT_CACHE_DIR = "./.cache"
DEFAULT_TTS_VOICE = "Ethan"


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_float(key: str, default: float) -> float:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_source_md_path() -> str:
    """Return the path to the source.md knowledge base file."""
    return _env(ENV_SOURCE_MD_PATH, DEFAULT_SOURCE_MD_PATH)


def get_current_provider() -> str:
    """Return the LLM provider name (e.g. 'dashscope')."""
    return _env(ENV_PROVIDER, DEFAULT_PROVIDER)


def get_model(provider: str | None = None) -> str:
    """Return the model name for the given provider."""
    if provider is None:
        provider = get_current_provider()
    return _env(ENV_MODEL, DEFAULT_MODEL)


def get_api_key(provider: str | None = None) -> str:
    """Return the API key for the given provider."""
    if provider is None:
        provider = get_current_provider()
    if provider == "anthropic":
        key = _env(ENV_AUTH_TOKEN, "")
        if not key:
            raise ValueError(
                f"Environment variable {ENV_AUTH_TOKEN} is not set. "
                "Please set your API key."
            )
        return key
    return _env(ENV_AUTH_TOKEN, "")


def get_provider_config(provider: str | None = None) -> dict[str, Any]:
    """Return provider-specific configuration (base_url, api_type, etc.)."""
    if provider is None:
        provider = get_current_provider()
    if provider == "anthropic":
        return {
            "base_url": _env(ENV_BASE_URL, "https://api.minimaxi.com/anthropic"),
            "api_type": "anthropic",
        }
    if provider == "dashscope":
        return {
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_type": "openai",
        }
    return {
        "base_url": _env(ENV_BASE_URL, ""),
        "api_type": provider,
    }


def get_temperature() -> float:
    """Return the LLM sampling temperature."""
    return _env_float(ENV_TEMPERATURE, DEFAULT_TEMPERATURE)


def get_tts_output_dir() -> str:
    """Return the directory where synthesized audio files are saved."""
    return _env(ENV_TTS_OUTPUT_DIR, DEFAULT_TTS_OUTPUT_DIR)


def get_sentence_model() -> str:
    """Return the sentence transformer model name for embeddings."""
    return _env(ENV_SENTENCE_MODEL, DEFAULT_SENTENCE_MODEL)


def get_cache_dir() -> str:
    """Return the cache directory path."""
    return _env(ENV_CACHE_DIR, DEFAULT_CACHE_DIR)


def get_tts_voice() -> str:
    """Return the TTS voice name (e.g. 'Ethan')."""
    return _env(ENV_TTS_VOICE, DEFAULT_TTS_VOICE)


def get_llm_config(provider: str | None = None) -> dict[str, Any]:
    """
    Build and return the llm_config dict for AutoGen.

    Structure follows AutoGen's ConversableAgent.llm_config expectations:
        {
            "config_list": [...],   # list of model endpoint configs
            "temperature": float,
            ...
        }
    """
    if provider is None:
        provider = get_current_provider()

    api_key = get_api_key(provider)
    provider_cfg = get_provider_config(provider)
    model = get_model(provider)
    temperature = get_temperature()

    return {
        "config_list": [
            {
                "model": model,
                "api_key": api_key,
                "base_url": provider_cfg["base_url"],
                "api_type": provider_cfg["api_type"],
            }
        ],
        "temperature": temperature,
    }
