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
import time
from urllib.parse import urlsplit

from app.logging import get_logger

logger = get_logger("app.ai")

# The endpoint must be a real ``http(s)://`` URL. A scheme-less ("x/v1"),
# empty, or unsupported-scheme ("ftp://...") value would otherwise sail past
# ``available()`` and fail confusingly only at call time.
_VALID_SCHEMES = frozenset({"http", "https"})


def _is_valid_http_url(url: str) -> bool:
    """True only for a well-formed ``http``/``https`` URL with a host."""
    if not url:
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in _VALID_SCHEMES and bool(parts.netloc)

# Retry policy for transient upstream failures (429/5xx, cold-start, connect
# drops). Small and bounded so a flaky moment recovers without hammering the
# provider or blocking the request for long.
_RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 0.5


class AIError(RuntimeError):
    """A provider call failed (network, bad response, etc.)."""


class _TransientAIError(AIError):
    """A retryable failure (429/5xx, timeout, connect drop). Internal — callers
    only ever see the plain ``AIError`` raised once retries are exhausted."""


class AIProvider:
    name = "base"

    def available(self) -> bool:
        return False

    def classify_transaction(
        self, description: str, amount: str, currency: str, candidate_categories: list[str]
    ) -> dict:
        """Return ``{"category": <name|None>, "confidence": 0..1, "rationale": str}``."""
        raise NotImplementedError

    def extract_from_image(self, image_b64: str, mime: str, *, system: str, instruction: str) -> dict:
        """Send an image to a vision model and return the parsed JSON it produces.
        Used only for the opt-in AI image-extraction fallback (spec §22)."""
        raise NotImplementedError


class NoAIProvider(AIProvider):
    name = "none"

    def available(self) -> bool:
        return False

    def classify_transaction(self, *args, **kwargs) -> dict:  # pragma: no cover
        raise AIError("AI is disabled")

    def extract_from_image(self, *args, **kwargs) -> dict:  # pragma: no cover
        raise AIError("AI is disabled")


def _extract_json(text: str) -> dict:
    """Pull the JSON object out of an LLM response (tolerating code fences or
    surrounding prose).

    Prefers a direct parse of the stripped body. If that fails, decodes the
    FIRST balanced object with ``json.JSONDecoder.raw_decode`` — which respects
    string literals and stops at the object's closing brace, so prose after it
    or a second object later in the text can't corrupt the parse. This replaces
    a greedy ``\\{.*\\}`` span that ran from the first ``{`` to the last ``}``.
    """
    text = text.strip()
    # Strip code fences in two simple passes (one anchored pattern each) rather than
    # an anchored alternation, which reads ambiguously.
    text = re.sub(r"^```(?:json)?", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    raise AIError("Model did not return valid JSON")


def _error_message(error) -> str:
    """Best-effort human message from an OpenAI-style error object/value."""
    if isinstance(error, dict):
        return str(error.get("message") or error)
    return str(error)


def _content_from_response(resp) -> str:
    """Extract the assistant message content from a chat-completions response.

    A provider that returns HTTP 200 with an ``{"error": ...}`` body (some
    gateways and local servers do this on a bad request) is surfaced as a clear
    ``AIError`` carrying the provider's message, rather than the confusing
    ``KeyError: 'choices'`` the naive ``resp.json()["choices"]`` access raised.
    """
    try:
        data = resp.json()
    except ValueError as exc:
        raise AIError(f"AI request returned invalid JSON: {exc}") from exc
    if isinstance(data, dict) and data.get("error"):
        raise AIError(f"AI provider error: {_error_message(data['error'])}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"AI response had no message content: {exc}") from exc


class OpenAICompatibleProvider(AIProvider):
    """Calls an OpenAI-style ``/chat/completions`` endpoint (local or cloud)."""

    name = "openai_compatible"

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout: float = 30.0,
        require_public_host: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        # SSRF guard (CR-SEC-3): when set (cloud AI modes) the endpoint must
        # resolve to a public host, so a private/internal URL can't be used to
        # proxy internal requests or leak the API key. Off for local_llm, where
        # the endpoint is legitimately on localhost/LAN.
        self.require_public_host = require_public_host

    def available(self) -> bool:
        if not self.model:
            return False
        if not _is_valid_http_url(self.base_url):
            logger.warning(
                "AI provider unavailable: endpoint %r is not a valid http(s):// URL",
                self.base_url,
            )
            return False
        return True

    def _complete(self, messages: list[dict]) -> str:
        """POST a chat-completions request and return the message content.

        Transient upstream failures (HTTP 429/5xx, timeouts, connect drops —
        e.g. a cold-starting local model) are retried a few times with a short
        backoff; a non-transient error surfaces immediately.

        SSRF guard (CR-SEC-3): in cloud modes the connect pins the endpoint's
        validated public IP (see :meth:`_post_pinned`), which also refuses a
        non-public / rebinding host before any socket opens.
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {"model": self.model, "messages": messages, "temperature": 0, "stream": False}
        url = f"{self.base_url}/chat/completions"

        last: _TransientAIError | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                return self._request_once(url, headers, body)
            except _TransientAIError as exc:
                last = exc
                if attempt + 1 < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_BASE * (2**attempt))
        raise AIError(f"AI request failed after {_MAX_ATTEMPTS} attempts: {last}") from last

    def _request_once(self, url: str, headers: dict, body: dict) -> str:
        """One HTTP attempt. Returns the message content, raises
        ``_TransientAIError`` for retryable failures and ``AIError`` for
        permanent ones."""
        import httpx

        try:
            # follow_redirects stays False (httpx default, set explicitly) so a
            # redirect can't bounce the request — and the API key — to another host.
            with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
                if self.require_public_host:
                    resp = self._post_pinned(client, url, headers, body)
                else:
                    resp = client.post(url, headers=headers, json=body)
        except httpx.TransportError as exc:  # timeouts, connect/read drops
            raise _TransientAIError(f"AI request failed: {exc}") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"AI request failed: {exc}") from exc

        if resp.status_code in _RETRY_STATUS:
            raise _TransientAIError(f"AI request failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise AIError(f"AI request failed: HTTP {resp.status_code}")
        return _content_from_response(resp)

    def _post_pinned(self, client, url: str, headers: dict, body: dict):
        """Cloud-mode POST that closes the DNS-rebind TOCTOU gap: resolve and
        validate the endpoint's address ONCE, then dial that pinned IP directly
        instead of letting httpx re-resolve the hostname at connect time.

        The socket connects to the validated public IP, but the ORIGINAL hostname
        is preserved for TLS — the ``sni_hostname`` extension drives both the SNI
        and the certificate-verification hostname (httpcore's ``server_hostname``),
        so cert validation stays enabled against the real name — and for the
        ``Host`` header, so the server still routes the request. A host that no
        longer resolves to only-public addresses (a rebind) yields no pin and is
        refused before any socket opens.
        """
        import httpx

        from app.services import net_guard

        pinned_ip = net_guard.pinned_ip_for_url(url)
        if pinned_ip is None:
            raise AIError(
                "AI endpoint must be a public host in a cloud privacy mode "
                "(the configured URL resolves to a private/loopback/unresolvable "
                "address) — refusing to send the request."
            )
        original = httpx.URL(url)
        connect_url = original.copy_with(host=pinned_ip)
        pinned_headers = {**headers, "Host": original.netloc.decode("ascii")}
        return client.post(
            connect_url,
            headers=pinned_headers,
            json=body,
            extensions={"sni_hostname": original.raw_host.decode("ascii")},
        )

    def _chat(self, system: str, user: str) -> str:
        return self._complete(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )

    def extract_from_image(self, image_b64: str, mime: str, *, system: str, instruction: str) -> dict:
        """Send an image (data URL) to the vision model and parse its JSON reply."""
        raw = self._complete(
            [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                    ],
                },
            ]
        )
        return _extract_json(raw)

    def classify_transaction(
        self, description: str, amount: str, currency: str, candidate_categories: list[str]
    ) -> dict:
        system = (
            "You categorise bank transactions. Choose the single best category "
            "from the provided list. If the merchant/description clearly indicates "
            "the country where the money was spent, also return its ISO-3166-1 "
            "alpha-2 code (e.g. GB, US, ES); otherwise use null. Also return a clean, "
            "human-friendly merchant/vendor name (e.g. 'Tesco', 'Amazon') stripped of "
            "card-processing noise, store numbers and locations; null if unclear. "
            'Respond ONLY with JSON: {"category": "<exact category name or null>", '
            '"confidence": <0..1>, "rationale": "<short reason>", '
            '"country": "<ISO-3166-1 alpha-2 or null>", "vendor": "<clean name or null>"}.'
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
            "country": parsed.get("country"),
            "vendor": parsed.get("vendor"),
        }
