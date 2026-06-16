"""Tests for the LLM-usage ledger (asterism_api.usage) and its endpoints.

The ledger is the backend system of record for token spend: endpoints append
events (token counts only); the UI computes cost from a rate table at display
time. These tests cover the writer/reader round-trip, the monthly rollup, the
read/write endpoints, and that an LLM endpoint records usage when the client
exposes ``last_usage`` (the multi-provider routing seam).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from asterism.oxigraph_client import OxigraphClient, OxigraphConfig
from asterism_step0.llm import LLMCompletion, LLMUsage
from fastapi.testclient import TestClient

from asterism_api import usage as usage_ledger
from asterism_api.main import build_app

from .test_main import _AUTH, _parse_sse, _settings


@pytest.fixture
def healthy_client() -> OxigraphClient:
    """An OxigraphClient whose backend answers /health (ASK) and /store (defined
    locally to avoid the import-shadows-fixture lint in test_main)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/query":
            return httpx.Response(
                200,
                text=json.dumps({"head": {}, "boolean": True}),
                headers={"content-type": "application/sparql-results+json"},
            )
        return httpx.Response(204)

    inner = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")
    return OxigraphClient(OxigraphConfig(base_url="http://test"), client=inner)

# ----------------------------------------------------------------------------
# Ledger module (no app)
# ----------------------------------------------------------------------------


def test_record_then_read_round_trip(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    usage_ledger.record_usage(
        root, "propose", "anthropic", "claude-opus-4-7",
        input_tokens=1000, output_tokens=200, cache_read_tokens=50,
        ts="2026-06-16T00:00:00+00:00",
    )
    usage_ledger.record_usage(
        root, "ask", "openai-compatible", "Qwen3",
        input_tokens=10, output_tokens=5, ts="2026-06-16T01:00:00+00:00",
    )
    events = usage_ledger.read_usage(root)
    assert len(events) == 2
    assert events[0]["feature"] == "propose"
    assert events[0]["input_tokens"] == 1000
    assert events[1]["provider"] == "openai-compatible"
    # Monthly files are partitioned by ts.
    assert (root / usage_ledger.USAGE_DIRNAME / "events-2026-06.jsonl").is_file()


def test_read_empty_when_no_dir(tmp_path: Path) -> None:
    assert usage_ledger.read_usage(tmp_path / "nothing") == []


def test_read_filters_by_since_until(tmp_path: Path) -> None:
    root = tmp_path / "r"
    for hour, feat in [("00", "propose"), ("02", "refine"), ("04", "ask")]:
        usage_ledger.record_usage(
            root, feat, "anthropic", "m", input_tokens=1,
            ts=f"2026-06-16T{hour}:00:00+00:00",
        )
    got = usage_ledger.read_usage(
        root, since="2026-06-16T01:00:00+00:00", until="2026-06-16T03:00:00+00:00"
    )
    assert [e["feature"] for e in got] == ["refine"]


def test_summarize_monthly_groups_and_sums(tmp_path: Path) -> None:
    root = tmp_path / "r"
    usage_ledger.record_usage(
        root, "propose", "anthropic", "m1", input_tokens=100, output_tokens=10,
        ts="2026-06-01T00:00:00+00:00",
    )
    usage_ledger.record_usage(
        root, "propose", "anthropic", "m1", input_tokens=200, output_tokens=20,
        ts="2026-06-02T00:00:00+00:00",
    )
    summary = usage_ledger.summarize_monthly(usage_ledger.read_usage(root))
    assert len(summary) == 1
    row = summary[0]
    assert row["month"] == "2026-06"
    assert row["call_count"] == 2
    assert row["input_tokens"] == 300
    assert row["output_tokens"] == 30
    assert row["total_tokens"] == 330


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------


def test_usage_get_is_readonly_and_empty(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    with TestClient(app) as client:  # no auth header — read-only route
        r = client.get("/api/usage")
        assert r.status_code == 200
        assert r.json() == {"events": [], "monthly": []}


def test_usage_post_is_write_gated_then_records(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    app = build_app(_settings(tmp_path), oxigraph_client=healthy_client, start_watcher=False)
    body = {"feature": "ask", "provider": "openai-compatible", "model_id": "Qwen3",
            "input_tokens": 42, "output_tokens": 7}
    with TestClient(app) as client:
        # Missing token → fail-closed (write-gated route).
        assert client.post("/api/usage", json=body).status_code >= 400
        # With token → recorded and visible via GET.
        r = client.post("/api/usage", json=body, headers=_AUTH)
        assert r.status_code == 200
        assert r.json()["recorded"]["input_tokens"] == 42
        events = client.get("/api/usage").json()["events"]
        assert len(events) == 1
        assert events[0]["feature"] == "ask"
        assert events[0]["model_id"] == "Qwen3"


# ----------------------------------------------------------------------------
# An LLM endpoint records usage when the client exposes last_usage
# ----------------------------------------------------------------------------


class _UsageLLM:
    """A provider-routed client stand-in: returns an LLMCompletion AND sets
    ``last_usage`` + ``model`` like the real clients (the seam the api records)."""

    model = "test-model-x"

    def __init__(self) -> None:
        self.last_usage: LLMUsage | None = None

    def complete(self, system_prompt: str, user_message: str) -> LLMCompletion:
        usage = LLMUsage(input_tokens=120, output_tokens=30, cache_read_tokens=10)
        self.last_usage = usage
        return LLMCompletion("## Proposed schema\n\nMOCK", usage)


def test_propose_records_usage_with_provider_headers(
    tmp_path: Path, healthy_client: OxigraphClient
) -> None:
    captured: dict[str, object] = {}

    def resolver(provider, model, api_base, key):
        captured["coords"] = (provider, model, api_base, key)
        return _UsageLLM()

    app = build_app(
        _settings(tmp_path),
        oxigraph_client=healthy_client,
        start_watcher=False,
        llm_resolver=resolver,
    )
    with TestClient(app, headers=_AUTH) as client:
        r = client.post(
            "/api/propose",
            data={"domain": "demo"},
            files={"files": ("s.csv", b"SID,x\n1,10\n2,11\n", "text/csv")},
            headers={
                "X-API-Key": "sk-user",
                "X-LLM-Provider": "openai-compatible",
                "X-LLM-Model": "Qwen3-Coder",
                "X-LLM-Api-Base": "https://api.ai.sakura.ad.jp/v1",
            },
        )
        assert r.status_code == 202
        job_id = r.json()["job_id"]
        # Drive the job to completion via the SSE stream.
        events = _parse_sse(client.get(f"/api/jobs/{job_id}/stream").text)
        assert "done" in [n for n, _ in events]

    # The provider coords reached the resolver from the headers...
    assert captured["coords"] == (
        "openai-compatible", "Qwen3-Coder", "https://api.ai.sakura.ad.jp/v1", "sk-user",
    )
    # ...and one usage event was recorded under feature=propose with the client's
    # model id (not the header model — the resolved client's .model wins).
    ledger = usage_ledger.read_usage(tmp_path / "registry")
    assert len(ledger) == 1
    ev = ledger[0]
    assert ev["feature"] == "propose"
    assert ev["provider"] == "openai-compatible"
    assert ev["model_id"] == "test-model-x"
    assert ev["input_tokens"] == 120
    assert ev["output_tokens"] == 30
    assert ev["cache_read_tokens"] == 10
