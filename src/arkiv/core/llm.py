"""Leichtgewichtige LLM-Abstraktion via direkten HTTP-Calls (kein litellm)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx


@dataclass
class Message:
    content: str


@dataclass
class Choice:
    message: Message


@dataclass
class CompletionResponse:
    choices: list[Choice] = field(default_factory=list)


def _is_local_ollama(api_base: str | None) -> bool:
    if not api_base:
        return False
    parsed = urlparse(api_base if "://" in api_base else f"http://{api_base}")
    return parsed.hostname in ("localhost", "127.0.0.1") and parsed.port == 11434


def _detect_provider(model: str, api_base: str | None) -> str:
    # Modell-Präfixe sind explizite Absichtserklärungen der Aufrufer und
    # schlagen die URL-Heuristik (sonst landen Cloud-Modelle mit
    # Default-base_url beim lokalen Ollama).
    if model.startswith(("huggingface/", "huggingface:")):
        return "huggingface"
    if model.startswith(("ollama_chat/", "ollama/")):
        return "ollama"
    if model.startswith(("claude", "anthropic/")):
        return "anthropic"
    if _is_local_ollama(api_base):
        return "ollama"
    return "openai"


def _call_ollama(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    api_base: str,
) -> CompletionResponse:
    # Strip provider prefix
    for prefix in ("ollama_chat/", "ollama/"):
        if model.startswith(prefix):
            model = model[len(prefix) :]
            break

    url = f"{api_base.rstrip('/')}/api/chat"
    body = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": max_tokens},
    }
    resp = httpx.post(url, json=body, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content: str = data["message"]["content"]
    return CompletionResponse(choices=[Choice(message=Message(content=content))])


def _call_openai(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    api_base: str,
    api_key: str | None,
) -> CompletionResponse:
    base = api_base.rstrip("/")
    endpoint = "chat/completions" if base.endswith("/v1") else "v1/chat/completions"
    url = f"{base}/{endpoint}"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    return CompletionResponse(choices=[Choice(message=Message(content=content))])


def _call_huggingface(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    api_base: str | None,
    api_key: str | None,
) -> CompletionResponse:
    for prefix in ("huggingface/", "huggingface:"):
        if model.startswith(prefix):
            model = model[len(prefix) :]
            break
    base = api_base or "https://router.huggingface.co/v1"
    token = api_key or os.environ.get("HF_TOKEN")
    return _call_openai(model, messages, temperature, max_tokens, timeout, base, token)


def _call_anthropic(
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    timeout: int,
    api_key: str | None,
) -> CompletionResponse:
    for prefix in ("anthropic/",):
        if model.startswith(prefix):
            model = model[len(prefix) :]
            break

    # Anthropic erwartet system separat
    system: str | None = None
    user_messages = []
    for msg in messages:
        if msg.get("role") == "system":
            system = msg["content"]
        else:
            user_messages.append(msg)

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key or "",
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body: dict[str, object] = {
        "model": model,
        "messages": user_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if system:
        body["system"] = system

    resp = httpx.post(url, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    content = data["content"][0]["text"]
    return CompletionResponse(choices=[Choice(message=Message(content=content))])


def completion(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_tokens: int = 1024,
    timeout: int = 30,
    api_base: str | None = None,
    api_key: str | None = None,
) -> CompletionResponse:
    """Drop-in-Ersatz für litellm.completion() via direkten HTTP-Calls."""
    provider = _detect_provider(model, api_base)

    try:
        if provider == "ollama":
            base = api_base or "http://localhost:11434"
            return _call_ollama(model, messages, temperature, max_tokens, timeout, base)
        if provider == "huggingface":
            return _call_huggingface(
                model,
                messages,
                temperature,
                max_tokens,
                timeout,
                api_base,
                api_key,
            )
        if provider == "anthropic":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            return _call_anthropic(model, messages, temperature, max_tokens, timeout, key)
        # openai-kompatibel (OpenAI, vLLM, LM Studio, …)
        # Env-Fallback nur für das echte api.openai.com — ein eigener
        # api_base (vLLM, LM Studio) bekommt den OpenAI-Key nicht ungefragt.
        if api_base is None:
            api_key = api_key or os.environ.get("OPENAI_API_KEY")
        base = api_base or "https://api.openai.com"
        return _call_openai(model, messages, temperature, max_tokens, timeout, base, api_key)

    except httpx.TimeoutException as exc:
        raise TimeoutError(f"LLM-Anfrage Timeout nach {timeout}s: {exc}") from exc
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"LLM-Anfrage fehlgeschlagen (HTTP {exc.response.status_code}): "
            f"{exc.response.text[:500]}"
        ) from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise RuntimeError(f"LLM-Antwort konnte nicht geparst werden: {exc}") from exc
