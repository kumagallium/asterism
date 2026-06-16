"""API usage ledger — the backend system of record for LLM token spend.

Every LLM-invoking endpoint (propose / refine / tool.propose / crosswalk.propose,
plus the demo-agent's ask via ``POST /api/usage``) appends one event here right
after the provider response is observed. Events carry **token counts only** — the
cost is computed in the UI from a user-editable per-model rate table at display
time, so changing a rate re-prices history with no server recompute pass.

Storage is append-only JSONL under the registry root (the shared filesystem
system of record), partitioned by month so old data rolls up cheaply. Appends use
POSIX ``O_APPEND`` (text-mode ``"a"``) which is atomic for sub-PIPE_BUF lines, so
concurrent api workers and the demo-agent POST path are safe without a lock — the
same pattern the watcher uses for ``jobs.jsonl``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

# Underscore prefix so this dir never collides with a dataset id under the
# registry root.
USAGE_DIRNAME = "_usage"

# The features that record usage. Informational (the writer accepts any string);
# kept here so the UI and tests share one vocabulary.
FEATURES = ("propose", "refine", "tool.propose", "crosswalk.propose", "ask")

_TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _month_path(registry_root: Path, month: str) -> Path:
    return Path(registry_root) / USAGE_DIRNAME / f"events-{month}.jsonl"


def record_usage(
    registry_root: Path | str,
    feature: str,
    provider: str,
    model_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    ts: str | None = None,
) -> dict[str, object]:
    """Append one usage event to the monthly JSONL ledger; return the event.

    Token counts only — no cost. ``ts`` defaults to now (UTC ISO-8601); the month
    partition is derived from it.
    """
    event: dict[str, object] = {
        "ts": ts or _now_iso(),
        "feature": feature,
        "provider": provider or "",
        "model_id": model_id or "",
        "input_tokens": int(input_tokens or 0),
        "output_tokens": int(output_tokens or 0),
        "cache_read_tokens": int(cache_read_tokens or 0),
        "cache_write_tokens": int(cache_write_tokens or 0),
    }
    month = str(event["ts"])[:7]  # YYYY-MM
    path = _month_path(Path(registry_root), month)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return event


def read_usage(
    registry_root: Path | str,
    *,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, object]]:
    """Read all events, ascending by ``ts``. ``since`` / ``until`` are ISO strings
    compared lexically (ISO-8601 sorts chronologically)."""
    base = Path(registry_root) / USAGE_DIRNAME
    if not base.is_dir():
        return []
    events: list[dict[str, object]] = []
    for p in sorted(base.glob("events-*.jsonl")):
        try:
            with p.open(encoding="utf-8") as fh:
                for raw in fh:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(ev, dict):
                        continue
                    ts = str(ev.get("ts", ""))
                    if since and ts < since:
                        continue
                    if until and ts > until:
                        continue
                    events.append(ev)
        except OSError:
            continue
    events.sort(key=lambda e: str(e.get("ts", "")))
    return events


def summarize_monthly(events: list[dict[str, object]]) -> list[dict[str, object]]:
    """Roll events up to (month, provider, model_id, feature) token sums.

    The UI shows raw events for the recent window and these summaries for older
    data (the bucket the UI assigns is currency-agnostic; cost is applied on top
    from the rate table)."""
    agg: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for ev in events:
        month = str(ev.get("ts") or "")[:7]
        key = (
            month,
            str(ev.get("provider") or ""),
            str(ev.get("model_id") or ""),
            str(ev.get("feature") or ""),
        )
        s = agg.get(key)
        if s is None:
            s = {
                "month": month,
                "provider": key[1],
                "model_id": key[2],
                "feature": key[3],
                "call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
                "total_tokens": 0,
            }
            agg[key] = s
        s["call_count"] = int(s["call_count"]) + 1  # type: ignore[arg-type]
        total = 0
        for f in _TOKEN_FIELDS:
            v = int(ev.get(f, 0) or 0)
            s[f] = int(s[f]) + v  # type: ignore[arg-type]
            total += v
        s["total_tokens"] = int(s["total_tokens"]) + total  # type: ignore[arg-type]
    return sorted(
        agg.values(),
        key=lambda s: (str(s["month"]), str(s["feature"]), str(s["model_id"])),
    )
