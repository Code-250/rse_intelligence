"""
Unified LLM client for RSE Intelligence.

Provider priority (runtime):
  1. NVIDIA NIM  — highest quality reasoning; requires NVIDIA_NIM_API_KEY
  2. Ollama      — local, free, zero latency; requires `ollama serve`
  3. Groq        — free cloud tier; requires GROQ_API_KEY

All cloud providers use the OpenAI-compatible chat completions API, so a
single _call_openai_compatible() handles both NIM and Groq. Ollama uses its
own native HTTP endpoint (no streaming).

Each provider is tried in order; the first non-empty response wins.
On total failure, None is returned and the caller applies its rule-based
fallback — this module never raises.

Environment variables:
  NVIDIA_NIM_API_KEY   — NIM API key (get from build.nvidia.com)
  NVIDIA_NIM_MODEL     — override default NIM model (optional)
  GROQ_API_KEY         — Groq API key
  GROQ_MODEL           — override default Groq model (optional)
  OLLAMA_MODEL         — Ollama model name (default: llama3.2)
"""

import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Provider configuration ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str
    model_env: str
    default_model: str
    max_tokens: int = 600
    temperature: float = 0.3
    timeout: float = 45.0
    max_retries: int = 2


_NVIDIA_NIM = ProviderConfig(
    name="NVIDIA NIM",
    base_url="https://integrate.api.nvidia.com/v1",
    api_key_env="NVIDIA_NIM_API_KEY",
    model_env="NVIDIA_NIM_MODEL",
    # llama-3.1-nemotron-70b: NVIDIA's RLHF-tuned Llama with strong instruction
    # following — well-suited for structured financial advisory generation.
    default_model="nvidia/llama-3.1-nemotron-70b-instruct",
    max_tokens=600,
    temperature=0.3,
    timeout=45.0,
    max_retries=2,
)

_GROQ = ProviderConfig(
    name="Groq",
    base_url="https://api.groq.com/openai/v1",
    api_key_env="GROQ_API_KEY",
    model_env="GROQ_MODEL",
    default_model="llama-3.3-70b-versatile",
    max_tokens=600,
    temperature=0.3,
    timeout=30.0,
    max_retries=1,
)

# ── Core HTTP helpers ─────────────────────────────────────────────────────────

def _call_openai_compatible(
    config: ProviderConfig,
    system_prompt: str,
    user_prompt: str,
) -> Optional[str]:
    """
    Call any OpenAI-compatible /chat/completions endpoint.
    Uses exponential backoff on rate-limit (429) responses.
    Returns the assistant message content, or None on failure.
    """
    api_key = os.getenv(config.api_key_env)
    if not api_key:
        logger.debug("[%s] %s not set — skipping", config.name, config.api_key_env)
        return None

    model = os.getenv(config.model_env, config.default_model)
    url = f"{config.base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }

    for attempt in range(config.max_retries + 1):
        try:
            resp = requests.post(
                url, headers=headers, json=payload, timeout=config.timeout
            )

            if resp.status_code == 200:
                data = resp.json()
                text = data["choices"][0]["message"]["content"]
                if text and text.strip():
                    logger.info(
                        "[%s] ✓ %d chars via %s", config.name, len(text), model
                    )
                    return text.strip()
                logger.warning("[%s] Empty response body", config.name)
                return None

            if resp.status_code == 429:
                wait = 2 ** attempt
                logger.warning(
                    "[%s] Rate limited (429) — retry %d/%d in %ds",
                    config.name, attempt + 1, config.max_retries, wait,
                )
                time.sleep(wait)
                continue

            # 4xx client errors (bad key, model not found) — don't retry
            if 400 <= resp.status_code < 500:
                logger.error(
                    "[%s] Client error %d: %s",
                    config.name, resp.status_code, resp.text[:200],
                )
                return None

            # 5xx server errors — retry
            logger.warning("[%s] Server error %d — retrying", config.name, resp.status_code)

        except requests.exceptions.Timeout:
            logger.warning("[%s] Timeout on attempt %d", config.name, attempt + 1)
        except requests.exceptions.ConnectionError as e:
            logger.error("[%s] Connection error: %s", config.name, e)
            return None
        except Exception as e:  # noqa: BLE001
            logger.error("[%s] Unexpected error: %s", config.name, e)
            return None

    logger.error("[%s] All %d retries exhausted", config.name, config.max_retries)
    return None


def _call_ollama(system_prompt: str, user_prompt: str) -> Optional[str]:
    """
    Call a locally running Ollama instance.
    Uses the native /api/generate endpoint (not OpenAI-compatible) to avoid
    requiring an API key. Start Ollama with: `ollama serve`
    """
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    try:
        resp = requests.post(
            "http://localhost:11434/api/generate",
            json={
                "model": model,
                "prompt": f"{system_prompt}\n\n{user_prompt}",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 600},
            },
            timeout=120,
        )
        if resp.status_code == 200:
            text = resp.json().get("response", "").strip()
            if text:
                logger.info("[Ollama] ✓ %d chars via %s", len(text), model)
                return text
        logger.warning("[Ollama] HTTP %d", resp.status_code)
    except requests.exceptions.ConnectionError:
        logger.debug("[Ollama] Not running — start with: ollama serve")
    except Exception as e:  # noqa: BLE001
        logger.error("[Ollama] Error: %s", e)
    return None


# ── Public interface ──────────────────────────────────────────────────────────

def generate(system_prompt: str, user_prompt: str) -> Optional[str]:
    """
    Generate a completion using the best available provider.

    Provider waterfall:
      NVIDIA NIM (best quality) → Ollama (local, free) → Groq (free cloud)

    Returns the first successful non-empty response, or None if all providers
    fail. The caller is responsible for applying a rule-based fallback.
    """
    providers = [
        ("NVIDIA NIM", lambda: _call_openai_compatible(_NVIDIA_NIM, system_prompt, user_prompt)),
        ("Ollama",     lambda: _call_ollama(system_prompt, user_prompt)),
        ("Groq",       lambda: _call_openai_compatible(_GROQ, system_prompt, user_prompt)),
    ]

    for name, call in providers:
        logger.info("[llm] Trying %s...", name)
        result = call()
        if result:
            return result
        logger.info("[llm] %s unavailable — trying next provider", name)

    logger.warning("[llm] All providers exhausted — returning None")
    return None
