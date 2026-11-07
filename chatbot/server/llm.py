"""OpenAI-compatible chat completions client (streaming + one-shot). Provider-agnostic."""
from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx


class LLMError(RuntimeError):
    pass


class OpenAICompatClient:
    def __init__(self, base_url: str, api_key: str, extra_headers: dict | None = None,
                 timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.extra_headers = extra_headers or {}
        self.timeout = timeout

    def _headers(self) -> dict:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        h.update({k: v for k, v in self.extra_headers.items() if v})
        return h

    async def stream_chat(self, model: str, messages: list[dict], max_tokens: int,
                          temperature: float) -> AsyncIterator[str]:
        """Yield content deltas; raises LLMError on transport/API failure."""
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature, "stream": True}
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload, headers=self._headers()) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode(errors="replace")[:300]
                        raise LLMError(f"upstream {r.status_code}: {body}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            return
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content")
                        if delta:
                            yield delta
        except httpx.HTTPError as e:
            raise LLMError(f"transport error: {e}") from e

    async def complete(self, model: str, messages: list[dict], max_tokens: int = 16,
                       temperature: float = 0.0) -> str:
        """Non-streaming completion (guard classifier)."""
        payload = {"model": model, "messages": messages, "max_tokens": max_tokens,
                   "temperature": temperature}
        url = f"{self.base_url}/chat/completions"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                r = await client.post(url, json=payload, headers=self._headers())
        except httpx.HTTPError as e:
            raise LLMError(f"transport error: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"upstream {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"unexpected response shape: {e}") from e
