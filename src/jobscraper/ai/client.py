"""Shared local and remote LLM helpers used by scraping and optional analysis workflows."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_OPENAI_BASE_URL   = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL      = "gpt-4o-mini"
DEFAULT_LOCAL_AI_BASE_URL = "http://127.0.0.1:11434"


def openai_config() -> Optional[Dict[str, str]]:
    """Return the configured OpenAI endpoint settings when available."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "api_key": api_key,
        "base_url": os.getenv("OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL).strip().rstrip("/"),
        "model": os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL,
        "organization": os.getenv("OPENAI_ORG_ID", "").strip(),
        "project": os.getenv("OPENAI_PROJECT", "").strip(),
    }


def openai_available() -> bool:
    """Return True when a usable OpenAI API key is configured."""
    return openai_config() is not None


def local_ai_config() -> Dict[str, str]:
    """Return the configured local-AI endpoint settings."""
    base_url = (
        os.getenv("LOCAL_AI_BASE_URL", "").strip()
        or os.getenv("OLLAMA_BASE_URL", "").strip()
        or DEFAULT_LOCAL_AI_BASE_URL
    ).rstrip("/")
    model = os.getenv("LOCAL_AI_MODEL", "").strip() or os.getenv("OLLAMA_MODEL", "").strip()
    provider = os.getenv("LOCAL_AI_PROVIDER", "").strip().lower()
    if not provider:
        provider = "openai_compat" if base_url.endswith("/v1") else "ollama"
    return {
        "base_url": base_url,
        "model": model,
        "provider": provider,
    }


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 120.0,
) -> Dict[str, Any]:
    """Issue one JSON HTTP request and return the parsed body."""
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {exc}") from exc


def extract_json_text(text: str, *, source_label: str) -> Dict[str, Any]:
    """Parse one JSON object from plain text, tolerating fenced code blocks."""
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{source_label} returned invalid JSON: {exc}") from exc


def local_ai_status() -> Dict[str, Any]:
    """Check whether the configured local-AI endpoint is reachable."""
    config   = local_ai_config()
    base_url = config["base_url"]
    provider = config["provider"]
    model    = config["model"]
    try:
        if provider == "openai_compat":
            payload = http_json(f"{base_url}/models", timeout=3.0)
            models  = [str(item.get("id") or "").strip() for item in payload.get("data", []) if str(item.get("id") or "").strip()]
        else:
            payload = http_json(f"{base_url}/api/tags", timeout=3.0)
            models  = [str(item.get("name") or "").strip() for item in payload.get("models", []) if str(item.get("name") or "").strip()]
        selected_model = model or (models[0] if models else "")
        ready          = bool(selected_model) and (not models or selected_model in models)
        if model and models and model not in models:
            return {
                "ready": False,
                "provider": provider,
                "base_url": base_url,
                "model": model,
                "models": models,
                "label": "configured model missing",
                "detail": f"{model} is not listed by the local AI endpoint.",
            }
        if not selected_model:
            return {
                "ready": False,
                "provider": provider,
                "base_url": base_url,
                "model": "",
                "models": models,
                "label": "no model available",
                "detail": "Local AI endpoint responded but no model is configured or installed.",
            }
        return {
            "ready": ready,
            "provider": provider,
            "base_url": base_url,
            "model": selected_model,
            "models": models,
            "label": "ready" if ready else "not ready",
            "detail": f"{provider} | {selected_model}",
        }
    except Exception as exc:
        return {
            "ready": False,
            "provider": provider,
            "base_url": base_url,
            "model": model,
            "models": [],
            "label": "unreachable",
            "detail": str(exc),
        }


def _extract_openai_output_text(payload: Dict[str, Any]) -> str:
    """Collect concatenated output text from the Responses API payload."""
    texts = []
    for item in payload.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = str(content.get("text") or "")
                if text:
                    texts.append(text)
    return "\n".join(texts).strip()


def call_openai_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema_name: str,
    schema: Dict[str, Any],
    source_label: str = "OpenAI",
    timeout: float = 120.0,
    max_output_tokens: int = 24000,
) -> Dict[str, Any]:
    """Call the OpenAI Responses API and decode one strict JSON payload."""
    config = openai_config()
    if not config:
        raise RuntimeError("OpenAI generation is unavailable: OPENAI_API_KEY is not set.")
    request_payload = {
        "model": config["model"],
        "instructions": system_prompt,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": user_prompt}]}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema_name,
                "description": f"{schema_name} response payload.",
                "strict": True,
                "schema": schema,
            }
        },
        "max_output_tokens": max_output_tokens,
    }
    data = json.dumps(request_payload).encode("utf-8")
    request = urllib.request.Request(
        f"{config['base_url']}/responses",
        data=data,
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        method="POST",
    )
    if config.get("organization"):
        request.add_header("OpenAI-Organization", config["organization"])
    if config.get("project"):
        request.add_header("OpenAI-Project", config["project"])
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI request failed: HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc
    text = _extract_openai_output_text(payload)
    if not text:
        raise RuntimeError("OpenAI request returned no output text.")
    return extract_json_text(text, source_label=source_label)


def call_local_ai_json(
    *,
    system_prompt: str,
    user_prompt: str,
    source_label: str = "Local AI",
    timeout: float = 180.0,
) -> Dict[str, Any]:
    """Call the configured local model endpoint and decode one JSON payload."""
    status = local_ai_status()
    if not status.get("ready"):
        raise RuntimeError(
            f"Local AI generation is unavailable: {status.get('label') or 'not ready'} | {status.get('detail') or ''}".strip()
        )
    config   = local_ai_config()
    base_url = config["base_url"]
    model    = str(status.get("model") or config.get("model") or "").strip()
    if config["provider"] == "openai_compat":
        payload = http_json(
            f"{base_url}/chat/completions",
            method="POST",
            payload={
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            },
            timeout=timeout,
        )
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("Local AI returned no choices.")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            text = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        else:
            text = str(content or "")
        if not text:
            raise RuntimeError("Local AI returned an empty message.")
        return extract_json_text(text, source_label=source_label)

    payload = http_json(
        f"{base_url}/api/generate",
        method="POST",
        payload={
            "model": model,
            "system": system_prompt,
            "prompt": user_prompt,
            "format": "json",
            "stream": False,
            "options": {"temperature": 0.1},
        },
        timeout=timeout,
    )
    text = str(payload.get("response") or "").strip()
    if not text:
        raise RuntimeError("Local AI returned no response text.")
    return extract_json_text(text, source_label=source_label)
