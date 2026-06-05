#!/usr/bin/env python3
"""End-to-end functionality smoke test for a running HA Finance Intelligence
instance.

Exercises the core flow against the live HTTP API — health, parsers, statement
import (preview + confirm), transactions, categorisation, dashboard, review queue
and the MQTT/AI/OCR service status — then prints a pass/fail summary. Useful as a
post-deploy check or in CI against a freshly-booted container.

It is read-mostly; the only mutation is importing a small sample statement (and
categorising one row), so point it at a TEST / demo / standalone instance, not
one holding data you care about. Use --skip-import to stay fully read-only.

Targets the API directly (e.g. the standalone `docker compose` instance on
:8099, or a dev server). The Home Assistant add-on serves its API only through
ingress (authenticated), so validate the add-on via the UI checklist in
docs/ha-testing.md instead.

Dependency-free (Python 3.11+ stdlib only) so it runs anywhere, including the Pi.

Examples:
  python scripts/functional_test.py
  python scripts/functional_test.py --base-url http://127.0.0.1:8099
  python scripts/functional_test.py --skip-import          # read-only
  python scripts/functional_test.py --sample examples/sample-csv/curve-sample.csv
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BASE = "http://127.0.0.1:8099"
DEFAULT_SAMPLE = "examples/sample-csv/curve-sample.csv"


class ApiError(Exception):
    pass


class Client:
    def __init__(self, base_url: str, timeout: float):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, *, data: bytes | None = None,
                 headers: dict[str, str] | None = None) -> tuple[int, Any]:
        url = f"{self.base}/{path.lstrip('/')}"
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (trusted local URL)
                body = resp.read()
                status = resp.status
        except urllib.error.HTTPError as exc:
            body, status = exc.read(), exc.code
        except urllib.error.URLError as exc:
            raise ApiError(f"cannot reach {url}: {exc.reason}") from exc
        try:
            parsed = json.loads(body) if body else None
        except json.JSONDecodeError:
            parsed = body.decode("utf-8", "replace")
        if status >= 400:
            raise ApiError(f"{method} {path} -> HTTP {status}: {parsed}")
        return status, parsed

    def get(self, path: str) -> Any:
        return self._request("GET", path)[1]

    def post_json(self, path: str, payload: dict | None = None) -> Any:
        data = json.dumps(payload or {}).encode()
        return self._request("POST", path, data=data,
                             headers={"Content-Type": "application/json"})[1]

    def post_file(self, path: str, filename: str, content: bytes, content_type: str,
                  fields: dict[str, str] | None = None) -> Any:
        boundary = "----hafi-functest-boundary"
        parts: list[bytes] = []
        for key, value in (fields or {}).items():
            parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode())
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n".encode()
        )
        body = b"".join(parts) + content + f"\r\n--{boundary}--\r\n".encode()
        return self._request("POST", path, data=body,
                             headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})[1]


class Runner:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn) -> object | None:
        try:
            detail = fn()
            self.results.append((name, True, str(detail) if detail is not None else ""))
            return detail
        except Exception as exc:  # noqa: BLE001 - report any failure, keep going
            self.results.append((name, False, str(exc)))
            return None

    def summary(self) -> int:
        print("\n" + "=" * 60)
        passed = sum(1 for _, ok, _ in self.results if ok)
        for name, ok, detail in self.results:
            mark = "✓" if ok else "✗"
            line = f" {mark} {name}"
            if detail:
                line += f" — {detail}"
            print(line)
        total = len(self.results)
        print("=" * 60)
        print(f" {passed}/{total} checks passed")
        return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Functional smoke test for HA Finance Intelligence")
    parser.add_argument("--base-url", default=DEFAULT_BASE, help=f"API base URL (default {DEFAULT_BASE})")
    parser.add_argument("--sample", default=DEFAULT_SAMPLE, help="sample CSV statement to import")
    parser.add_argument("--skip-import", action="store_true", help="read-only: skip the sample import + categorise")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout (seconds)")
    args = parser.parse_args()

    # The summary uses ✓/✗/·/— glyphs; force UTF-8 so it doesn't crash on a
    # legacy console (e.g. Windows cp1252).
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except ValueError:
            pass

    c = Client(args.base_url, args.timeout)
    r = Runner()
    print(f"Target: {c.base}")

    r.check("health", lambda: _expect_key(c.get("api/health"), "status"))
    r.check("bootstrap owner (/users/me)", lambda: _expect_key(c.get("api/users/me"), "role"))
    r.check("parsers include curve_csv",
            lambda: _expect(any(p.get("parser_id") == "curve_csv" for p in c.get("api/imports/parsers")),
                            "curve_csv parser present"))

    if not args.skip_import:
        _import_flow(c, r, Path(args.sample))

    r.check("transactions list", lambda: f"{_get(c, 'api/transactions').get('total', 0)} total")
    cats = r.check("categories seeded", lambda: _expect(len(c.get("api/categories")) > 0, "categories present"))
    if not args.skip_import and cats:
        _categorise_flow(c, r)

    r.check("dashboard summary", lambda: _expect(isinstance(c.get("api/dashboard/summary"), dict), "summary returned"))
    r.check("review count", lambda: f"{c.get('api/review/count').get('open', '?')} open")
    r.check("services status", lambda: _services_line(c.get("api/settings/services")))

    return r.summary()


def _import_flow(c: Client, r: Runner, sample: Path) -> None:
    if not sample.is_file():
        r.check("import sample statement", lambda: _fail(f"sample not found: {sample}"))
        return

    def do_import() -> str:
        content = sample.read_bytes()
        preview = c.post_file("api/imports/upload", sample.name, content, "text/csv",
                              fields={"parser_id": "curve_csv"})
        import_id = preview["import_id"]
        confirm = c.post_json(f"api/imports/{import_id}/confirm")
        report = confirm.get("report", {})
        return f"import #{import_id}: {report.get('new', 0)} new, {report.get('duplicates', 0)} dup"

    r.check("import sample statement (upload + confirm)", do_import)


def _categorise_flow(c: Client, r: Runner) -> None:
    def do_categorise() -> str:
        uncat = c.get("api/transactions?uncategorised=true&limit=1")
        items = uncat.get("items", [])
        if not items:
            return "nothing uncategorised (skipped)"
        txn_id = items[0]["id"]
        cat_id = c.get("api/categories")[0]["id"]
        res = c.post_json(f"api/transactions/{txn_id}/categorise", {"category_id": cat_id})
        if res.get("category_id") != cat_id:
            raise ApiError("category did not stick")
        return f"categorised txn #{txn_id}"

    r.check("categorise a transaction", do_categorise)


def _services_line(svc: dict) -> str:
    def state(key: str) -> str:
        return "on" if (svc.get(key) or {}).get("enabled") else "off"
    return f"AI {state('ai')} · OCR {state('ocr')} · MQTT {state('mqtt')} · FX {state('fx')}"


def _get(c: Client, path: str) -> dict:
    out = c.get(path)
    if not isinstance(out, dict):
        raise ApiError(f"{path} did not return an object")
    return out


def _expect(condition: bool, detail: str) -> str:
    if not condition:
        raise ApiError(f"expected: {detail}")
    return detail


def _expect_key(obj: object, key: str) -> str:
    if not isinstance(obj, dict) or key not in obj:
        raise ApiError(f"missing key '{key}' in response")
    return f"{key}={obj[key]}"


def _fail(msg: str) -> str:
    raise ApiError(msg)


if __name__ == "__main__":
    sys.exit(main())
