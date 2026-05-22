from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path("models.yaml")


def load_models_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or DEFAULT_CONFIG_PATH
    if not config_path.exists():
        return {"models": {}}
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    data.setdefault("models", {})
    return data


def build_llm(model: str, *, temperature: float = 0.0, config: dict[str, Any] | None = None):
    from langchain.chat_models import init_chat_model

    cfg = config or load_models_config()
    aliases = cfg.get("models", {})
    if model in aliases:
        entry = aliases[model]
        provider = entry.get("provider", "openai_compatible")
        if provider == "openai_compatible":
            from langchain_openai import ChatOpenAI

            api_key = entry.get("api_key")
            if not api_key and entry.get("api_key_env"):
                api_key = os.environ.get(entry["api_key_env"])
            if not api_key:
                api_key = "EMPTY"
            kwargs = {
                "model": entry["model"],
                "base_url": entry["base_url"],
                "api_key": api_key,
                "temperature": temperature,
            }
            if "max_tokens" in entry:
                kwargs["max_tokens"] = entry["max_tokens"]
            if "timeout" in entry:
                kwargs["timeout"] = entry["timeout"]
            return ChatOpenAI(**kwargs)
        if provider == "langchain":
            return init_chat_model(entry["model"], temperature=temperature)
        raise ValueError(f"Unknown model provider '{provider}' for alias '{model}'")
    return init_chat_model(model, temperature=temperature)
