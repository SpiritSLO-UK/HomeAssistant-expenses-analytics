"""AI provider abstraction (spec §22.2).

A tiny, sync, pluggable interface. ``OpenAICompatibleProvider`` speaks the
``/chat/completions`` shape that covers Ollama, LM Studio, llama.cpp, Home
Assistant's LLM integrations and the major cloud APIs — so "local" vs "cloud" is
just a different base URL / key, not different code. ``NoAIProvider`` is the
default and does nothing.

Providers return plain dicts; the gateway (``ai_service``) handles privacy
gating, redaction, auditing and mapping results back to the database. Providers
never touch the DB.
"""

from __future__ import annotations

import json
import re

from app.logging import get_logger

logger = get_logger("app.ai")


class AIError(RuntimeError):
    """A provider call failed (network, bad response, etc.)."""


class AIProvider:
    name = "base"

    def available(self) -> bool:
        return False

    def classify_transaction(
        self, description: str, amount: str, currency: str, candidate_categories: list[str]
    ) -> dict:
        """Return ``{"category": <name|None>, "confidence": 0..1, "rationale": str}``."""
        raise NotImplementedError


class NoAIProvider(AIProvider):
    name = "none"

    def available(self) -> bool:
        return False

    def classify_transaction(self, *args, **kwargs) -> dict:  # pragma: no cover
        raise AIError("AI is disabled")


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of an LLM response (tolerating code fences
    or surrounding prose)."""
    text = text.strip()
    # Strip code fences in two simple passes (one anchored pattern each) rather than
    # an anchored alternation, which reads ambiguously.
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    raise AIError("Model did not return valid JSON")


class OpenAICompatibleProvider(AIProvider):
    """Calls an OpenAI-style ``/chat/completions`` endpoint (local or cloud)."""

    name = "openai_compatible"

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def _chat(self, system: str, user: str) -> str:
        import httpx

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise AIError(f"AI request failed: {exc}") from exc

    def classify_transaction(
        self, description: str, amount: str, currency: str, candidate_categories: list[str]
    ) -> dict:
        system = (
            "You categorise bank transactions. Choose the single best category "
            "from the provided list. Respond ONLY with JSON: "
            '{"category": "<exact category name or null>", "confidence": <0..1>, '
            '"rationale": "<short reason>"}.'
        )
        user = json.dumps(
            {
                "description": description,
                "amount": amount,
                "currency": currency,
                "candidate_categories": candidate_categories,
            }
        )
        raw = self._chat(system, user)
        parsed = _extract_json(raw)
        category = parsed.get("category")
        if isinstance(category, str) and category.strip().lower() in {"null", "none", ""}:
            category = None
        try:
            confidence = float(parsed.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return {
            "category": category,
            "confidence": max(0.0, min(1.0, confidence)),
            "rationale": str(parsed.get("rationale", ""))[:500],
        }
