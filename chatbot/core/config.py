"""Configuration loading for the site chatbot."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class SiteConfig:
    name: str = "My Site"
    start_url: str = "https://example.com"
    max_pages: int = 200
    include_subdomains: bool = False
    crawl_fallback: bool = True
    suggestions: list = field(default_factory=list)  # suggested questions for the widget
    exclude_paths: list = field(default_factory=list)  # path substrings to skip


@dataclass
class ChatConfig:
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = "meta-llama/llama-3.1-8b-instruct"
    context_window: int = 131072
    max_output: int = 700
    temperature: float = 0.2
    extra_headers: dict = field(default_factory=dict)


@dataclass
class GuardConfig:
    enabled: bool = True
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = "meta-llama/llama-guard-4-12b"
    blocked_categories: list = field(
        default_factory=lambda: ["S1", "S2", "S3", "S4", "S7", "S9", "S10", "S11", "S12"]
    )


@dataclass
class Budgets:
    pass_tokens: int = 40000
    warn_tokens: int = 100000
    history_turns: int = 4
    safety_margin_pct: int = 10


@dataclass
class RateLimits:
    requests_per_minute: float = 6
    burst: int = 10
    max_question_chars: int = 2000


@dataclass
class Config:
    site: SiteConfig = field(default_factory=SiteConfig)
    chat: ChatConfig = field(default_factory=ChatConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    budgets: Budgets = field(default_factory=Budgets)
    rate_limits: RateLimits = field(default_factory=RateLimits)
    admin_token_env: str = "ADMIN_TOKEN"
    refusal_message: str = "I can't help with that. I'm here to answer questions about {site}."
    path: Path | None = None  # location of the yaml file

    @property
    def data_dir(self) -> Path:
        return (self.path.parent / "data" / "site") if self.path else Path("data/site")

    def api_key(self, env_name: str) -> str:
        return os.environ.get(env_name, "")

    @property
    def admin_token(self) -> str:
        return os.environ.get(self.admin_token_env, "")


def _merge(dc: Any, data: dict) -> Any:
    """Merge a dict onto a dataclass instance field-by-field."""
    for k, v in (data or {}).items():
        if hasattr(dc, k):
            cur = getattr(dc, k)
            if isinstance(cur, dict) and isinstance(v, dict):
                cur.update(v)
            else:
                setattr(dc, k, v)
    return dc


def load_config(path: str | Path = "config.yaml") -> Config:
    path = Path(path)
    cfg = Config(path=path.resolve())
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        _merge(cfg.site, data.get("site"))
        _merge(cfg.chat, data.get("chat"))
        _merge(cfg.guard, data.get("guard"))
        _merge(cfg.budgets, data.get("budgets"))
        _merge(cfg.rate_limits, data.get("rate_limits"))
        if "admin" in data:
            cfg.admin_token_env = data["admin"].get("token_env", cfg.admin_token_env)
        if "refusal_message" in data:
            cfg.refusal_message = data["refusal_message"]
    return cfg
