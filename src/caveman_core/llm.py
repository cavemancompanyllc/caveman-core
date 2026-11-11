"""Local-LLM client (Ollama) shared by every plugin that calls a local model.

Stdlib only. Host comes from the ``host`` argument, then ``OLLAMA_HOST``, then
``http://localhost:11434``. ``model`` comes from the argument, then
``CAVEMAN_LLM_MODEL``; callers normally pass their own default.

Ollama is a shared resource across every session on the machine: never unload
models or restart the server from plugin code.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_HOST = "http://localhost:11434"


class LLMError(RuntimeError):
    pass


def host(explicit: str | None = None) -> str:
    h = explicit or os.environ.get("OLLAMA_HOST") or DEFAULT_HOST
    if not h.startswith(("http://", "https://")):
        h = "http://" + h
    return h.rstrip("/")


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise LLMError(f"{url}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"{url}: {e} - is Ollama running?") from e


def default_think(model: str) -> bool | str:
    """Thinking setting that keeps short classification calls fast.

    gpt-oss ignores ``think=False`` and needs an effort level instead, so it gets
    ``"low"``; other thinking models get ``False``.
    """
    return "low" if model.startswith("gpt-oss") else False


def chat(messages: list[dict], model: str | None = None, *, host_url: str | None = None,
         json_format: bool | dict = False, options: dict | None = None, timeout: float = 120.0,
         think: bool | str | None = None) -> str:
    """Send a chat request and return the assistant's text.

    ``json_format=True`` asks Ollama for a JSON object; a dict is passed through
    as a JSON schema (structured outputs). ``think`` is sent only when given;
    pass ``think="auto"`` to use :func:`default_think`.
    """
    model = model or os.environ.get("CAVEMAN_LLM_MODEL")
    if not model:
        raise LLMError("no model given and CAVEMAN_LLM_MODEL is not set")
    body: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if json_format:
        body["format"] = json_format if isinstance(json_format, dict) else "json"
    if options:
        body["options"] = options
    if think == "auto":
        think = default_think(model)
    if think is not None:
        body["think"] = think
    data = _post(f"{host(host_url)}/api/chat", body, timeout)
    try:
        return data["message"]["content"]
    except (KeyError, TypeError) as e:
        raise LLMError(f"unexpected Ollama response: {str(data)[:300]}") from e


def chat_json(messages: list[dict], model: str | None = None, **kw) -> Any:
    text = chat(messages, model, json_format=kw.pop("json_format", True), **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMError(f"model did not return valid JSON: {text[:300]}") from e


def models(host_url: str | None = None, timeout: float = 5.0) -> list[str]:
    req = urllib.request.Request(f"{host(host_url)}/api/tags")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return [m["name"] for m in json.loads(r.read().decode()).get("models", [])]
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"{host(host_url)}: {e} - is Ollama running?") from e
