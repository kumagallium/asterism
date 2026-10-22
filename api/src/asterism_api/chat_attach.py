"""📎 でデータ — 添えた表をかんたんの段に順に通し、返事 1 枚に畳む（ui-redesign PR 11c・§19.2）。

かんたんの検査と公開の経路は**そのまま**呼ぶだけ（``/api/staging``・``/api/inspect``・
``/api/design/column-meanings``・``/api/vocab/fit``・``/api/propose/skeleton/assemble``・
``/validate``・``/api/propose/continue``・``/api/materialize``・``/api/datasets/{id}/source``・
``/ingest``・``/refine``・``/promote`` …）。自分のルートを ``httpx.ASGITransport`` の scripted
client で叩くので、かんたんの段の論理は無改修のまま同じ結果になる。この走者は LLM を直接呼ばない
（AI に渡る値は、かんたんの既存の呼び出し〔列の意味・設計・直し〕が渡すものと同じだけ）。

* :func:`run_attach` — ① 受け取る → ② 読む → ③ 意味と結ぶ候補 → ④ ID → ⑤ 骨格 → ⑥ 設計 →
  ⑦ 取り込み。④ の ID 候補が 2 つ以上なら**そこで止め**、返事に小さな選択（``notes[].choice``）を
  残す。人が選ぶと :func:`run_attach` が ``resume`` つきで ④ から続く。終わったら話の手番を
  書き換えて保存する。
* :func:`confirm_attach` — ✓ ＝ 公開。結ぶと決めた列を ``upper.json`` に
  （``drafted_by: machine``）・``promote``・公開のあとの「名前が一致した関係」を機械の手番に・
  問いの提案。
* :func:`confirm_suggestion` / :func:`revert_suggestion` — 機械の手番の ✓
  （``/api/sky/suggestions/apply``）と戻す（``/dismiss``）。

手番の形（11a の拡張）: ``state`` に ``running``（走者が動いている間）・``attachment``
（``{dataset_id, staging_id, name, rows, kinds, id_column, meanings}``）・``reply.bindings``
（``[{column, label, term, term_label, relation, decided, subject}]``）。走者の再開用の控えは
``turn["attach_run"]``。
"""

# ruff: noqa: RUF001
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import httpx
from asterism import chat_sessions, shared_vocab
from asterism.class_schema import class_labels
from asterism.mapping_ir_read import read_mapping_ir

from asterism_api import chat_turns, registry
from asterism_api.upper_routes import dump_upper, load_upper, merge_upper, resolve_property_ref

if TYPE_CHECKING:
    from fastapi import FastAPI

    from asterism_api.main import Settings

__all__ = [
    "TABLE_EXTS",
    "add_questions",
    "confirm_attach",
    "confirm_suggestion",
    "discard_dataset",
    "id_candidates",
    "interrupted_reply",
    "is_table_name",
    "proposed_datasets",
    "revert_suggestion",
    "run_attach",
    "suggest_questions",
]

logger = logging.getLogger(__name__)

#: 添えられる表の拡張子（文書は 10b〔11d 以降〕）
TABLE_EXTS = (".csv", ".tsv", ".xlsx", ".xls", ".json")
_MAX_FIT_COLUMNS = 60
_MAX_MEANINGS_SHOWN = 8
_MAX_SHAPE_NOTES = 5
#: かんたんの設計で「文書まわりの罠」とされるもの（KantanWizard の DOC_ONLY_TRAPS と同じ）
_DOC_ONLY_TRAPS = frozenset({"T5", "T6", "T7", "T10"})
_LLM_HEADERS = ("x-api-key", "x-llm-provider", "x-llm-model", "x-llm-api-base", "x-llm-max-tokens")

# ---- 言葉 ------------------------------------------------------------------------------------

_T: dict[str, dict[str, str]] = {
    "ja": {
        "s1": "表を受け取っています",
        "s2": "表を読んでいます",
        "s3": "列の意味を読み取っています",
        "s4": "ID にする列を決めています",
        "s5": "データの形を組み立てています",
        "s6": "設計を作っています",
        "s7": "取り込んでいます",
        "fix": "設計を直しています",
        "intro": (
            "次の検証エラーを修正して §RML を作り直してください"
            "（許可された Tier 0 関数だけを使う・存在しない列を参照しない）:"
        ),
        "choice_id": "ID にする列",
        "read": "表「{name}」を読みました。ID にする列を選んでください。",
        "reply": "表「{name}」を読みました（{rows} 行・1 行＝{kind}）。ID＝{id}。"
        "列の意味: {meanings}。「公開」を押すと公開します。",
        "reply_nomeaning": "表「{name}」を読みました（{rows} 行・1 行＝{kind}）。ID＝{id}。"
        "「公開」を押すと公開します。",
        "more": "ほか {n} 列",
        "sentence": "列「{column}」は「{term}」と同じもの",
        "row_kind": "{stem} の 1 行",
        "attach_failed": "表を読み取れませんでした。形式を確かめて、もう一度添えてください。",
        "interrupted": "読み取りが途中で止まりました。もう一度添えてください",
        "ai_failed": (
            "AI が列の意味／設計を作れませんでした。設定 › AI を確かめて、もう一度添えてください"
        ),
        "id_provisional": "{id}（機械の仮置き）",
        "id_provisional_note": (
            "ID は機械が仮に置きました。重なる行があれば別の列を ID にしてください"
        ),
        "leftover": "取り込んだデータを片づけられませんでした",
        "ingest_stop": "取り込みの途中で止まりました。表の中身を確かめて、もう一度添えてください。",
        "unsupported": "文書はまだ添えられません（表を添えてください）",
        "dup": "ID が重なっています（{n} 行が同じ ID になります）。",
        "missing": "ID の列「{c}」が表にありません。",
        "measure": "ID にした列は測定値のようです。",
        "id_auto": "自動",
        "others": "ほか",
        "sug_text": "名前が一致した関係が {n} 本あります",
        "sug_sentence": "「{s}」は「{t}」と同じもの",
        "sug_sub": "「{s}」は「{t}」に含まれるもの",
        "sug_gone": "すでに結ばれているか、結べなくなりました",
        "need_choice": "先に ID にする列を選んでください",
        "commit_error": "公開できませんでした。もう一度試してください。",
    },
    "en": {
        "s1": "Receiving the table",
        "s2": "Reading the table",
        "s3": "Reading what each column means",
        "s4": "Choosing the ID column",
        "s5": "Assembling the shape of the data",
        "s6": "Building the design",
        "s7": "Loading the data",
        "fix": "Fixing the design",
        "intro": (
            "Fix the following validation errors and rebuild §RML (use only the allowed "
            "Tier 0 functions; do not reference columns that don't exist):"
        ),
        "choice_id": "Column to use as ID",
        "read": 'Read the table "{name}". Please choose the column to use as ID.',
        "reply": 'Read the table "{name}" ({rows} rows, one row = {kind}). ID = {id}. '
        "Column meanings: {meanings}. Press Publish to publish.",
        "reply_nomeaning": 'Read the table "{name}" ({rows} rows, one row = {kind}). '
        "ID = {id}. Press Publish to publish.",
        "more": "and {n} more",
        "sentence": 'Column "{column}" is the same thing as "{term}"',
        "row_kind": "Row of {stem}",
        "attach_failed": "Could not read the table. Check its format and attach it again.",
        "interrupted": "Reading stopped partway. Please attach it again",
        "ai_failed": (
            "The AI could not build the column meanings / design. "
            "Check Settings > AI and attach it again"
        ),
        "id_provisional": "{id} (placed by the machine for now)",
        "id_provisional_note": (
            "The ID was placed by the machine for now. "
            "If rows overlap, choose another column as the ID"
        ),
        "leftover": "Could not clean up the data that was loaded",
        "ingest_stop": "Loading stopped partway. Check the table and attach it again.",
        "unsupported": "Documents can't be attached yet (please attach a table)",
        "dup": "IDs overlap ({n} rows share an ID).",
        "missing": 'The ID column "{c}" is not in the table.',
        "measure": "The ID column looks like a measured value.",
        "id_auto": "automatic",
        "others": "others",
        "sug_text": "{n} relation(s) matched by name",
        "sug_sentence": '"{s}" is the same thing as "{t}"',
        "sug_sub": '"{s}" is part of "{t}"',
        "sug_gone": "Already linked, or no longer possible to link",
        "need_choice": "Please choose the ID column first",
        "commit_error": "Could not publish. Please try again.",
    },
}


def _lg(lang: str | None) -> str:
    return "en" if (lang or "").lower().startswith("en") else "ja"


def text(lang: str, key: str, **kw: Any) -> str:
    return _T[_lg(lang)][key].format(**kw)


# ---- 表かどうか・名前 --------------------------------------------------------------------------


def is_table_name(name: str) -> bool:
    return name.lower().endswith(TABLE_EXTS)


def _stem(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return (base[:dot] if dot > 0 else base).strip() or "data"


# ---- ④ の ID の候補（judgments.ts の規則をサーバに写したもの） ---------------------------------


def is_measurement(examples: list[str]) -> bool:
    """測定値（小数を含む数値だけの列）— judgments.ts の ``isMeasurement`` と同じ。"""
    vals = [e for e in examples if e.strip() != ""]
    if not vals:
        return False

    def number(e: str) -> bool:
        try:
            float(e.strip() or "nan")
        except ValueError:
            return False
        return e.strip().lower() not in ("nan", "inf", "-inf", "+inf", "infinity", "-infinity")

    return all(number(e) for e in vals) and any(re.search(r"[.eE]", e) for e in vals)


def id_candidates(rows: list[dict], source: str) -> list[str]:
    """そのファイルで「番号」になれる列 — 取り込む前置き列（``origin == "preamble"``）で、
    測定値でない。judgments.ts の ``numberCandidates`` と同じ
    （チャットでは「取り込まない」列は無い）。"""
    return [
        r["column"]
        for r in rows
        if r["source"] == source and r["origin"] == "preamble" and not is_measurement(r["examples"])
    ]


def _json_header(res: httpx.Response, name: str) -> Any:
    raw = res.headers.get(name)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {}


def _preamble_value(pc: dict) -> str:
    """前置きの 1 行の値（KantanWizard の ``preambleValue``）。"""
    t = str(pc.get("text") or "")
    if not pc.get("named"):
        return t
    name = str(pc.get("name") or "")
    at = t.find(name)
    if at < 0:
        return t
    rest = re.sub(r"^\s*[:=]\s*", "", t[at + len(name) :]).strip()
    return rest or t


def column_rows(res: httpx.Response) -> list[dict]:
    """``/api/inspect`` の見出し（samples・preamble・dialects）から、判断に要る列の事実を組む。
    前置きの列は、読み方（dialect）が前置きを取り込むときだけ入る
    （KantanWizard の ``meaningRows``）。"""
    samples = _json_header(res, "X-Asterism-Samples")
    preamble = _json_header(res, "X-Asterism-Preamble")
    dialects = _json_header(res, "X-Asterism-Dialects")
    rows: list[dict] = []
    for source, cols in samples.items():
        dialect = dialects.get(source) or {}
        if (dialect.get("preamble") or "drop") != "drop":
            for pc in preamble.get(source) or []:
                rows.append(
                    {
                        "source": source,
                        "column": str(pc.get("name") or ""),
                        "origin": "preamble",
                        "examples": [_preamble_value(pc)],
                    }
                )
        for column, examples in (cols or {}).items():
            rows.append(
                {
                    "source": source,
                    "column": column,
                    "origin": "table",
                    "examples": [str(e) for e in examples],
                }
            )
    return rows


# ---- scripted client ---------------------------------------------------------------------------


class AttachError(Exception):
    """走者が止まった理由（``kind``: ``failed`` | ``ingest_stop``）。``detail`` はログだけ。"""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail


class _Ctx:
    def __init__(
        self,
        app: FastAPI,
        cfg: Settings,
        http: httpx.AsyncClient,
        lang: str,
        emit: Callable[..., None],
    ) -> None:
        self.app, self.cfg, self.http, self.lang, self.emit = app, cfg, http, lang, emit

    def stage(self, n: int, key: str | None = None) -> None:
        self.emit(stage=n, message=text(self.lang, key or f"s{n}"))


def llm_headers_of(headers: Any) -> dict[str, str]:
    """要求の LLM ヘッダ（キー・プロバイダ・モデル・base・max_tokens）だけを写す。"""
    return {k: v for k, v in ((h, headers.get(h)) for h in _LLM_HEADERS) if v}


def make_http(app: FastAPI, token: str | None, llm_headers: dict[str, str]) -> httpx.AsyncClient:
    """自分のルートを叩く client。Host は loopback の名前にする（local.py の門を通る）。"""
    headers = dict(llm_headers)
    if token:
        headers["X-Asterism-Token"] = token
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://127.0.0.1",
        headers=headers,
        timeout=httpx.Timeout(600, connect=10),
    )


def _detail(res: httpx.Response) -> Any:
    try:
        return res.json().get("detail")
    except Exception:
        return res.text[:300]


async def _req(
    ctx: _Ctx,
    method: str,
    path: str,
    *,
    ok: tuple[int, ...] = (200, 201, 202),
    **kw: Any,
) -> httpx.Response:
    res = await ctx.http.request(method, path, **kw)
    if res.status_code not in ok:
        raise AttachError("failed", f"{method} {path} -> {res.status_code}: {_detail(res)}")
    return res


async def _job_result(ctx: _Ctx, job_id: str, *, ai: bool = False) -> Any:
    """``app.state.jobs`` のジョブの終わりを待って結果を返す（失敗は ``AttachError``）。
    ``ai=True`` は AI が作る段（列の意味・設計・直し）— 失敗は ``ai_failed`` と言い分ける。"""
    kind = "ai_failed" if ai else "failed"
    job = ctx.app.state.jobs.get(job_id)
    if job is None or job.task is None:
        raise AttachError(kind, f"unknown job {job_id}")
    await job.task
    if job.status != "done":
        raise AttachError(kind, f"job {job_id} {job.status}: {job.error}")
    return job.result


def _issues_of(res: httpx.Response) -> list[str] | None:
    d = _detail(res)
    if isinstance(d, dict) and isinstance(d.get("issues"), list) and d["issues"]:
        return [str(i) for i in d["issues"]]
    return None


# ---- 段 ----------------------------------------------------------------------------------------


async def _stage_place_and_read(ctx: _Ctx, files: list[tuple[str, bytes]], st: dict) -> None:
    """① 置く → ② 読む → ③ 意味と結ぶ候補。"""
    ctx.stage(1)
    upload = [
        ("files", (name, data, mimetypes.guess_type(name)[0] or "application/octet-stream"))
        for name, data in files
    ]
    res = await _req(ctx, "POST", "/api/staging", files=upload)
    st["staging_id"] = res.json()["staging_id"]
    st["sources"] = list(res.json().get("sources") or [])
    # 保存された名前（canonical）→ 人が付けた名前
    try:
        from asterism_api import staging

        sdir, _paths = staging.load(ctx.cfg.registry_root, st["staging_id"])
        meta = json.loads((sdir / "meta.json").read_text("utf-8"))
        placed = {str(k): str(v) for k, v in (meta.get("names") or {}).items()}
    except Exception:
        placed = {}
    only = files[0][0] if len(files) == 1 else None
    st["shown"] = {s: placed.get(s) or only or s for s in st["sources"]}

    ctx.stage(2)
    res = await _req(ctx, "POST", "/api/inspect", data={"staging_id": st["staging_id"]})
    st["rows"] = column_rows(res)

    ctx.stage(3)
    res = await _req(
        ctx,
        "POST",
        "/api/design/column-meanings",
        data={"staging_id": st["staging_id"], "domain": "", "language": _lg(ctx.lang)},
    )
    out = await _job_result(ctx, res.json()["job_id"], ai=True)
    st["meanings"] = [
        {k: m[k] for k in ("source", "column", "label", "unit") if m.get(k)}
        for m in (out.get("meanings") or [])
        if m.get("source") and m.get("column")
    ]
    st["bindings"] = await _find_bindings(ctx, st["meanings"])


async def _find_bindings(ctx: _Ctx, meanings: list[dict]) -> list[dict]:
    """列ごとに ``/api/vocab/fit``（完全一致だけ）。項目（property）に当たった列だけ、
    「結ぶ」を既定にして返す。"""
    out: list[dict] = []
    for m in meanings[:_MAX_FIT_COLUMNS]:
        label = str(m.get("label") or "").strip()
        if not label:
            continue
        try:
            res = await _req(
                ctx, "GET", "/api/vocab/fit", params={"label": label, "column": m["column"]}
            )
            candidates = res.json().get("candidates") or []
        except AttachError:
            continue  # 候補が読めない列は、結ぶ提案を出さないだけ
        hit = next((c for c in candidates if c.get("term_kind") == "property"), None)
        if hit is None:
            continue
        out.append(
            {
                "source": m["source"],
                "column": m["column"],
                "label": label,
                "term": hit["term"],
                "term_label": hit.get("label") or hit["term"],
                "relation": "eq",
                "decided": "bind",
            }
        )
    return out


def _numbers(st: dict) -> tuple[dict[str, str], list[str]]:
    """④ — 番号になる列（ファイルごと）。
    返り = ``(選んだ/ただ 1 つの番号, 選択が要るファイル)``。"""
    picks: dict[str, str] = dict(st.get("picks") or {})
    numbers: dict[str, str] = {}
    ask: list[str] = []
    for source in st["sources"]:
        cands = id_candidates(st["rows"], source)
        if picks.get(source) in cands:
            numbers[source] = picks[source]
        elif len(cands) == 1:
            numbers[source] = cands[0]
        elif len(cands) >= 2:
            ask.append(source)
    return numbers, ask


def _meaning_label(st: dict, source: str, column: str) -> str:
    for m in st["meanings"]:
        if m["source"] == source and m["column"] == column:
            return str(m.get("label") or "")
    return ""


def _shape_notes(annotations: dict, lang: str) -> list[dict]:
    """骨格の検査（``annotations``）の気になる点を、平易な文で（最大 5）。"""
    out: list[dict] = []
    for ann in (annotations.get("maps") or {}).values():
        if not isinstance(ann, dict):
            continue
        if ann.get("is_unique") is False:
            n = int(ann.get("colliding_rows") or 0)
            out.append(chat_turns.note("shape", text(lang, "dup", n=n)))
        for c in ann.get("missing_columns") or []:
            out.append(chat_turns.note("shape", text(lang, "missing", c=c)))
        if ann.get("key_measurement_caution"):
            out.append(chat_turns.note("shape", text(lang, "measure")))
    seen: set[str] = set()
    uniq = [n for n in out if not (n["text"] in seen or seen.add(n["text"]))]
    return uniq[:_MAX_SHAPE_NOTES]


async def _stage_skeleton_to_ingest(ctx: _Ctx, st: dict) -> list[dict]:
    """④ ID → ⑤ 骨格・検査・設計・保存・取り込み。返り = 形の気になる点（notes）。"""
    ctx.stage(4)
    numbers, _ask = _numbers(st)
    stem = st["name"]
    sid = st["staging_id"]
    linkable = [{"source": s, "column": c} for s, c in numbers.items()]
    labels = [
        {"source": s, "column": c, "label": lab}
        for s, c in numbers.items()
        if (lab := _meaning_label(st, s, c))
    ]
    row_labels = {
        s: text(ctx.lang, "row_kind", stem=_stem(st["shown"].get(s) or s)) for s in st["sources"]
    }
    ctx.stage(5)
    res = await _req(
        ctx,
        "POST",
        "/api/propose/skeleton/assemble",
        data={
            "staging_id": sid,
            "linkable": json.dumps(linkable, ensure_ascii=False),
            "card_keys": json.dumps(st.get("picks") or {}, ensure_ascii=False),
            "excluded": "[]",
            "dataset_name": stem,
            "labels": json.dumps(labels, ensure_ascii=False),
            "row_labels": json.dumps(row_labels, ensure_ascii=False),
            "subject_bases": "{}",
        },
    )
    assembled = res.json()
    skeleton = assembled["skeleton"]
    provisional = (assembled.get("metadata") or {}).get("provisional_card_keys") or {}
    st["id_column"] = _id_column(assembled, numbers, st)
    st["id_provisional"] = _provisional_ids(st["id_column"], provisional, numbers)
    res = await _req(
        ctx,
        "POST",
        "/api/propose/skeleton/validate",
        data={"staging_id": sid, "skeleton": json.dumps(skeleton, ensure_ascii=False)},
    )
    shape = _shape_notes(res.json().get("annotations") or {}, ctx.lang)
    if st["id_provisional"]:
        shape.append(chat_turns.note("shape", text(ctx.lang, "id_provisional_note")))

    ctx.stage(6)
    data = {
        "staging_id": sid,
        "skeleton": json.dumps(skeleton, ensure_ascii=False),
        "deterministic_rules": "1",
        "domain": "",
        "language": _lg(ctx.lang),
    }
    if st["meanings"]:
        data["column_meanings"] = json.dumps(st["meanings"], ensure_ascii=False)
    res = await _req(ctx, "POST", "/api/propose/continue", data=data)
    proposal_md = (await _job_result(ctx, res.json()["job_id"], ai=True))["proposal_md"]
    await _save_and_ingest(ctx, st, proposal_md)
    return shape


def _id_column(assembled: dict, numbers: dict[str, str], st: dict) -> str:
    """返事の「ID＝…」— 選んだ/ただ 1 つの番号の列、無ければ骨格の主語のキーの列。"""
    cols = [c for c in numbers.values() if c]
    if not cols:
        for m in (assembled.get("skeleton") or {}).get("maps") or []:
            tpl = str((m.get("subject") or {}).get("template") or "")
            cols += re.findall(r"\{([^{}]+)\}", tpl)
            break
    return "、".join(dict.fromkeys(cols)) or text(st["lang"], "id_auto")


def _provisional_ids(id_column: str, provisional: dict, numbers: dict[str, str]) -> bool:
    """返事の ID の列が、機械の仮置き（``provisional_card_keys``）に当たるか。
    人が選んだ・番号に決まった列（``numbers``）は仮置きでない。"""
    cols = {str(c) for c in provisional.values() if c}
    chosen = {c for c in numbers.values() if c}
    ids = [c for c in id_column.split("、") if c]
    return any(c in cols and c not in chosen for c in ids)


def _materialize_problems(res: dict) -> list[str]:
    """保存の結果が「止める」ものか（KantanWizard の ``hardStop`` と同じ）。
    止めるなら直す材料の行を返す。"""
    fails = [t for t in res.get("traps") or [] if t.get("status") == "fail"]
    hard = [t for t in fails if t.get("id") not in _DOC_ONLY_TRAPS]
    issues = [str(i) for i in res.get("validation_issues") or []]
    stop = (
        not res.get("complete", True)
        or bool(issues)
        or bool(hard)
        or (res.get("exit_code", 0) != 0 and not fails)
    )
    if not stop:
        return []
    lines = [] if res.get("complete", True) else ["incomplete design output (truncated)"]
    for t in fails:
        head = f"{t['id']} {t['name']}: {t['detail']}"
        fix = str(t.get("fix") or "")
        lines.append(f"{head}\n  ↳ " + fix.replace("\n", "\n    ") if fix else head)
    lines += [str(w) for w in res.get("warnings") or []]
    lines += issues
    return lines or ["design did not pass the checks"]


async def _materialize(ctx: _Ctx, st: dict, md: str) -> tuple[dict, list[str]]:
    body: dict[str, Any] = {
        "proposal_md": md,
        "dataset_name": st["name"],
        "staging_id": st["staging_id"],
        "handles": [],
    }
    if st.get("dataset_id"):
        body["dataset_id"] = st["dataset_id"]
    elif st["meanings"]:
        body["column_meanings"] = st["meanings"]
    res = (await _req(ctx, "POST", "/api/materialize", json=body)).json()
    if res.get("dataset", {}).get("id"):
        st["dataset_id"] = res["dataset"]["id"]
    return res, _materialize_problems(res)


async def _refine(ctx: _Ctx, st: dict, md: str, lines: list[str]) -> str:
    """設計を AI に直してもらう（かんたんの「AI に直してもらう」と同じ本文）。"""
    comment = text(ctx.lang, "intro") + "\n" + "\n".join(f"- {line}" for line in lines)
    body: dict[str, Any] = {
        "schema_md": md,
        "comments": [comment],
        "language": _lg(ctx.lang),
        "staging_id": st["staging_id"],
    }
    if st.get("dataset_id"):
        body["dataset_id"] = st["dataset_id"]
    res = await _req(ctx, "POST", "/api/refine", json=body)
    out = await _job_result(ctx, res.json()["job_id"], ai=True)
    if out.get("complete") is False:
        raise AttachError("ingest_stop", "refine truncated")
    return str(out.get("effective_schema_md") or out.get("refined_md") or md)


async def _save_and_ingest(ctx: _Ctx, st: dict, md: str) -> None:
    """⑤ 保存 → ⑦ 取り込み。取り込みの 422（設計の問題）は 1 回だけ直して取り込み直す。"""
    refined = False
    attached = False
    ctx.stage(6)
    while True:
        _res, problems = await _materialize(ctx, st, md)
        if problems:
            if refined:
                raise AttachError("ingest_stop", "; ".join(problems)[:300])
            ctx.stage(6, "fix")
            md, refined = await _refine(ctx, st, md, problems), True
            continue
        if not attached:
            await _req(
                ctx,
                "POST",
                f"/api/datasets/{st['dataset_id']}/source",
                data={"staging_id": st["staging_id"]},
            )
            attached = True
        ctx.stage(7)
        res = await ctx.http.post(f"/api/datasets/{st['dataset_id']}/ingest")
        if res.status_code == 422 and (issues := _issues_of(res)):
            if refined:
                raise AttachError("ingest_stop", "; ".join(issues)[:300])
            ctx.stage(6, "fix")
            md, refined = await _refine(ctx, st, md, issues), True
            continue
        if res.status_code != 202:
            raise AttachError("ingest_stop", f"ingest -> {res.status_code}: {_detail(res)}")
        try:
            await _job_result(ctx, res.json()["job_id"])
        except AttachError as exc:
            raise AttachError("ingest_stop", exc.detail) from exc
        return


def _authored_names(root: Any, dataset_id: str) -> dict[str, str]:
    """設計の IR が種類に付けた名前（``subject.label``）— main.py の ``_kind_labels`` と同じ。"""
    from asterism_step0.mapping_ir import BUILTIN_PREFIXES, parse_mapping_ir

    data = registry.load_dataset(root, dataset_id)
    yaml_text = ((data or {}).get("artifacts") or {}).get("mapping.yaml") or ""
    if not yaml_text.strip():
        return {}
    try:
        ir = parse_mapping_ir(yaml_text)
    except Exception:
        return {}
    prefixes = dict(BUILTIN_PREFIXES) | dict(ir.prefixes)

    def expand(term: str) -> str:
        prefix, sep, rest = term.partition(":")
        return prefixes[prefix] + rest if sep and prefix in prefixes else term

    out: dict[str, str] = {}
    for tm in ir.maps:
        label = (tm.subject.label or "").strip()
        if label:
            for c in tm.subject.classes:
                out.setdefault(expand(c), label)
    return out


async def _summary(ctx: _Ctx, st: dict) -> dict:
    """⑥ 数 — ``draft-stats`` から行数と種類（名前つき）。名前は設計の IR の名前が先、
    無ければ ``class_labels``。"""
    res = await _req(ctx, "GET", f"/api/datasets/{st['dataset_id']}/draft-stats")
    stats = res.json()
    rows = sum(int(v) for v in (stats.get("source_rows") or {}).values())
    iris = {str(c["iri"]) for c in stats.get("classes") or []}
    authored = _authored_names(ctx.cfg.registry_root, st["dataset_id"])
    names: dict[str, str] = {}
    rest = {i for i in iris if i not in authored}
    if rest:
        try:
            names = await class_labels(ctx.app.state.client, ctx.cfg.registry_root, rest)
        except Exception:
            logger.warning("attach: class labels unavailable", exc_info=True)
    kinds = [
        {
            "iri": c["iri"],
            "label": authored.get(c["iri"]) or names.get(c["iri"]) or c.get("curie") or c["iri"],
            "count": c["n"],
        }
        for c in stats.get("classes") or []
    ]
    return {"rows": rows, "kinds": kinds}


def _resolve_bindings(st: dict, root: Any) -> list[dict]:
    """設計のあと、結ぶ候補の列を ``property:<map>/<列>`` に引き直す（引けない列は出さない）。"""
    from asterism_api.consult_graph_routes import _property_subject

    data = registry.load_dataset(root, st["dataset_id"])
    try:
        view = read_mapping_ir(((data or {}).get("artifacts") or {}).get("mapping.yaml") or "")
    except ValueError:
        return []
    out: list[dict] = []
    for b in st["bindings"]:
        subject = _property_subject(view, b["column"])
        if subject:
            out.append({**b, "subject": subject})
    return out


# ---- 返事 --------------------------------------------------------------------------------------


def _bind_sentence(lang: str, b: dict) -> str:
    return text(lang, "sentence", column=b["column"], term=b["term_label"])


def _public_binding(b: dict) -> dict:
    keys = ("column", "label", "term", "term_label", "relation", "decided", "subject")
    return {k: b[k] for k in keys if k in b}


def _final_reply(ctx: _Ctx, st: dict, summary: dict, shape: list[dict]) -> tuple[dict, dict]:
    lang = ctx.lang
    biggest = max(summary["kinds"], key=lambda k: int(k["count"] or 0), default=None)
    kind = biggest["label"] if biggest else "—"
    meanings = [
        f"{m['label']}（{m['column']}）" for m in st["meanings"] if m.get("label")
    ]
    shown = meanings[:_MAX_MEANINGS_SHOWN]
    if len(meanings) > _MAX_MEANINGS_SHOWN:
        shown.append(text(lang, "more", n=len(meanings) - _MAX_MEANINGS_SHOWN))
    sep = ", " if _lg(lang) == "en" else "、"
    id_text = (
        text(lang, "id_provisional", id=st["id_column"])
        if st.get("id_provisional")
        else st["id_column"]
    )
    common = {"name": st["name"], "rows": summary["rows"], "kind": kind, "id": id_text}
    body = (
        text(lang, "reply", meanings=sep.join(shown), **common)
        if shown
        else text(lang, "reply_nomeaning", **common)
    )
    bindings = [_public_binding(b) for b in st["bindings_resolved"]]
    reply = chat_turns._reply(
        body,
        sentences=[_bind_sentence(lang, b) for b in bindings],
        bindings=bindings,
        notes=shape,
    )
    attachment = {
        "dataset_id": st["dataset_id"],
        "staging_id": st["staging_id"],
        "name": st["name"],
        "rows": summary["rows"],
        "kinds": summary["kinds"],
        "id_column": st["id_column"],
        "meanings": [m for m in st["meanings"] if m.get("label")],
    }
    return reply, attachment


def _choice_reply(ctx: _Ctx, st: dict, ask: list[str]) -> dict:
    notes = []
    for source in ask:
        options = [
            {"value": c, "label": _meaning_label(st, source, c) or c}
            for c in id_candidates(st["rows"], source)
        ]
        notes.append(
            chat_turns.note(
                "choice_id",
                "",
                {
                    "label": text(ctx.lang, "choice_id"),
                    "source": source,
                    "options": options,
                    "default": options[0]["value"],
                },
            )
        )
    return chat_turns._reply(text(ctx.lang, "read", name=st["name"]), notes=notes)


# ---- 走者 --------------------------------------------------------------------------------------


async def run_attach(
    app: FastAPI,
    cfg: Settings,
    session_id: str,
    turn_id: str,
    files: list[tuple[str, bytes]] | None,
    *,
    lang: str,
    llm_headers: dict[str, str],
    token: str | None,
    emit: Callable[..., None],
    resume: dict | None = None,
) -> dict:
    """添えた表をかんたんの段に通し、手番を書き換えて保存する。
    返り = ``{"state": …}``（ジョブの結果）。

    ``resume = {"note_index", "value"}`` なら、控え（``turn["attach_run"]``）から ④ を続ける。"""
    lang = _lg(lang)
    http = make_http(app, token, llm_headers)
    ctx = _Ctx(app, cfg, http, lang, emit)
    lock: asyncio.Lock = app.state.themes_lock
    root = cfg.registry_root
    st: dict = {}
    attachment: dict | None = None
    cancelled: BaseException | None = None
    try:
        async with lock:
            session = chat_sessions.load_session(root, session_id)
            turn = _find(session, turn_id)
            st = dict(turn.get("attach_run") or {})
            if resume is not None:
                choice = turn["reply"]["notes"][int(resume["note_index"])]["choice"]
                st.setdefault("picks", {})[choice["source"]] = str(resume["value"])
        if resume is None:
            assert files
            st = {
                "lang": lang,
                "name": _stem(files[0][0]) + (f" {text(lang, 'others')}" if len(files) > 1 else ""),
                "picks": {},
            }
            await _stage_place_and_read(ctx, files, st)
        _numbers_picked, ask = _numbers(st)
        if ask:
            reply, state = _choice_reply(ctx, st, ask), "proposed"
        else:
            shape = await _stage_skeleton_to_ingest(ctx, st)
            st["bindings_resolved"] = _resolve_bindings(st, root)
            summary = await _summary(ctx, st)
            reply, attachment = _final_reply(ctx, st, summary, shape)
            state = "proposed"
            st.pop("rows", None)  # 例の値は、選び終わったあとは持たない
    except AttachError as exc:
        logger.warning("attach stopped (%s): %s", exc.kind, exc.detail)
        if exc.kind == "ai_failed":
            note = chat_turns.note("ai_failed", text(lang, "ai_failed"))
        else:
            stop = "ingest_stop" if exc.kind == "ingest_stop" else "attach_failed"
            note = chat_turns.note(stop, text(lang, stop))
        reply, state = chat_turns._reply("", notes=[note]), "none"
    except BaseException as exc:  # CancelledError も — 手番が running のまま固まらないように
        if isinstance(exc, Exception):
            logger.warning("attach failed", exc_info=True)
            key = "attach_failed"
        else:
            logger.warning("attach cancelled (%s)", type(exc).__name__)
            key = "interrupted"
            cancelled = exc
        reply = chat_turns._reply("", notes=[chat_turns.note(key, text(lang, key))])
        state = "none"
    finally:
        await http.aclose()
    if attachment is None and st.get("dataset_id"):  # 残さない手番の取り込み済みデータは片づける
        if not await discard_dataset(app, cfg, st["dataset_id"], token=token):
            reply["notes"].append(chat_turns.note("info", text(lang, "leftover")))
        st.pop("dataset_id", None)
    async with lock:
        session = chat_sessions.load_session(root, session_id)
        if session is None:  # 話が捨てられた
            if attachment is not None:
                await discard_dataset(app, cfg, attachment["dataset_id"], token=token)
            if cancelled is not None:
                raise cancelled
            return {"state": "none"}
        turn = _find(session, turn_id)
        turn["reply"] = reply
        turn["state"] = state
        if attachment is not None:
            turn["attachment"] = attachment
        turn["attach_run"] = st or turn.get("attach_run")
        turn.pop("job_id", None)
        chat_sessions.save_session(root, session)
    if cancelled is not None:
        raise cancelled
    return {"state": state}


async def discard_dataset(
    app: FastAPI, cfg: Settings, dataset_id: str, *, token: str | None
) -> bool:
    """取り込み済みで未公開のデータセットを消す（``DELETE /api/datasets/{id}``）。
    公開済みは触らない。消せたら ``True``。"""
    data = registry.load_dataset(cfg.registry_root, dataset_id)
    if data is None:
        return True
    if (data.get("meta") or {}).get("promoted"):
        return True
    http = make_http(app, token, {})
    try:
        res = await http.delete(f"/api/datasets/{dataset_id}")
        return res.status_code in (200, 202, 204, 404)
    except Exception:
        logger.warning("attach: discard failed", exc_info=True)
        return False
    finally:
        await http.aclose()


def interrupted_reply(lang: str) -> dict:
    """止まったまま残った手番（再起動・取り消し）の返事。"""
    return chat_turns._reply(
        "", notes=[chat_turns.note("attach_failed", text(lang, "interrupted"))]
    )


def _find(session: dict | None, turn_id: str) -> dict:
    for t in (session or {}).get("turns") or []:
        if t.get("id") == turn_id:
            return t
    raise AttachError("failed", "turn gone")


# ---- ✓ ＝ 公開 ---------------------------------------------------------------------------------


async def confirm_attach(
    app: FastAPI,
    cfg: Settings,
    session: dict,
    turn: dict,
    *,
    lang: str,
    decisions: dict[str, str] | None,
    llm_headers: dict[str, str],
    token: str | None,
    at: str,
) -> tuple[dict, dict | None, dict | None]:
    """✓ — 結ぶ列を ``upper.json`` に・``promote``・機械の手番。呼び手が ``themes_lock`` を持つ
    （問いの提案は AI の往復が長いのでここに含めない — :func:`suggest_questions`）。
    返り = ``(手番, commit | None, followup | None)``。
    失敗は手番の ``commit_error``（``commit=None``）。"""
    lang = _lg(lang)
    att = turn.get("attachment") or {}
    dataset_id = att.get("dataset_id")
    if not dataset_id:
        raise chat_turns.TurnRefused(409, text(lang, "need_choice"))
    reply = turn["reply"]
    notes = [n for n in reply.get("notes") or [] if n.get("kind") != "commit_error"]
    reply["notes"] = notes
    for b in reply.get("bindings") or []:
        d = (decisions or {}).get(b["column"])
        if d in ("bind", "skip"):
            b["decided"] = d
    http = make_http(app, token, llm_headers)
    ctx = _Ctx(app, cfg, http, lang, lambda **_: None)
    try:
        _write_upper(cfg, dataset_id, reply.get("bindings") or [], turn["batch"], at)
        res = await http.post(f"/api/datasets/{dataset_id}/publish-names")
        if res.status_code not in (200, 409):  # 409 = まだ公開した名前が無い（初めての公開）
            raise AttachError("failed", f"publish-names -> {res.status_code}: {_detail(res)}")
        await _req(ctx, "POST", f"/api/datasets/{dataset_id}/promote")
        data = registry.load_dataset(cfg.registry_root, dataset_id) or {}
        commit = {
            "dataset_id": dataset_id,
            "published_at": (data.get("meta") or {}).get("promoted_at") or at,
        }
        followup = await _followup(ctx, session, turn, dataset_id, at)
    except Exception as exc:
        logger.warning("attach publish failed: %s", exc, exc_info=True)
        await http.aclose()
        reply["notes"] = [*notes, chat_turns.note("commit_error", text(lang, "commit_error"))]
        return turn, None, None
    await http.aclose()
    turn["state"] = "confirmed"
    turn["before"] = None
    turn["commit"] = commit
    return turn, commit, followup


def _predicate_iri(data: dict, ref: str) -> str | None:
    try:
        view = read_mapping_ir(((data.get("artifacts") or {}).get("mapping.yaml")) or "")
    except ValueError:
        return None
    return resolve_property_ref(view, ref)


def _write_upper(cfg: Settings, dataset_id: str, bindings: list[dict], batch: str, at: str) -> None:
    data = registry.load_dataset(cfg.registry_root, dataset_id)
    if data is None:
        raise AttachError("failed", "dataset gone")
    existing = load_upper(data.get("artifacts") or {})
    dismissed = shared_vocab.load_dismissed(cfg.registry_root)
    have = {(e["subject"], e["term"], e["relation"]) for e in existing}
    incoming: list[dict] = []
    for b in bindings:
        if b.get("decided") == "skip" and b.get("subject"):
            # 結ばないと決めた列は、公開のあとの「結べそうな関係」にも出さない（候補の主語は
            # 述語の IRI なので、その形でも外す）
            subjects = [b["subject"]]
            iri = _predicate_iri(data, b["subject"])
            if iri:
                subjects.append(iri)
            for subject in subjects:
                for relation in (None, "equivalentProperty"):
                    with contextlib.suppress(ValueError):
                        shared_vocab.dismiss_fit(
                            cfg.registry_root, subject, b["term"], at=at, relation=relation
                        )
            continue
        if b.get("decided") != "bind" or not b.get("subject"):
            continue
        key = (b["subject"], b["term"], "equivalentProperty")
        if key in have or shared_vocab.is_dismissed(dismissed, b["subject"], b["term"]):
            continue
        have.add(key)
        incoming.append(
            {
                "subject": b["subject"],
                "term": b["term"],
                "relation": "equivalentProperty",
                "drafted_by": "machine",
                "batch": batch[:100],
                "reason": "名前の完全一致",
            }
        )
    if incoming:
        merged = merge_upper(existing, [*existing, *incoming])
        registry.write_artifact(cfg.registry_root, dataset_id, "upper.json", dump_upper(merged))


async def _followup(ctx: _Ctx, session: dict, turn: dict, dataset_id: str, at: str) -> dict | None:
    """公開のあと、このデータセットで機械が数えた「結べそうな関係」があれば機械の手番にする。"""
    try:
        res = await _req(ctx, "GET", "/api/sky/suggestions")
    except AttachError:
        return None
    mine = [s for s in res.json().get("suggestions") or [] if s.get("dataset_id") == dataset_id]
    if not mine:
        return None
    lang = ctx.lang
    sentences = []
    for s in mine:
        key = "sug_sentence" if str(s["relation"]).startswith("equivalent") else "sug_sub"
        sentences.append(text(lang, key, s=s["subject_label"], t=s["term_label"]))
    reply = chat_turns._reply(text(lang, "sug_text", n=len(mine)), sentences=sentences)
    reply["suggestions"] = [
        {"subject": s["subject"], "term": s["term"], "relation": s["relation"]} for s in mine
    ]
    return chat_turns._append_turn(
        session, at=at, user=None, reply=reply, state="proposed", before=None, by="machine"
    )


async def suggest_questions(
    app: FastAPI,
    cfg: Settings,
    dataset_id: str,
    *,
    lang: str,
    llm_headers: dict[str, str],
    token: str | None,
) -> list[dict]:
    """10a — 公開のあとの問いの提案（AI の失敗・未設定は空）。**ロックの外**で呼ぶ。"""
    lang = _lg(lang)
    http = make_http(app, token, llm_headers)
    try:
        res = await http.post(
            "/api/consult/suggest-questions",
            json={"screen": "dataset", "context": {"dataset_id": dataset_id}, "lang": lang},
        )
        if res.status_code != 200:
            return []
        rows = res.json().get("suggestions") or []
        return [s for s in rows if isinstance(s, dict) and s.get("question")]
    except Exception:
        logger.warning("attach: question suggestions failed", exc_info=True)
        return []
    finally:
        await http.aclose()


def add_questions(turn: dict, questions: list[dict]) -> None:
    """手番の返事に問いの提案を書き足す（呼び手が ``themes_lock`` を持つ）。"""
    reply = turn["reply"]
    reply["questions"] = [*(reply.get("questions") or []), *(q["question"] for q in questions)]
    reply["question_suggestions"] = questions


async def confirm_suggestion(
    app: FastAPI,
    cfg: Settings,
    turn: dict,
    *,
    lang: str,
    token: str | None,
) -> dict | None:
    """機械の手番の ✓ — ``/api/sky/suggestions/apply``。結べたら ``confirmed``、
    結べなければ一言を残して閉じる。"""
    lang = _lg(lang)
    http = make_http(app, token, {})
    try:
        res = await http.post(
            "/api/sky/suggestions/apply",
            json={"items": turn["reply"]["suggestions"], "batch": turn["batch"][:100]},
        )
    finally:
        await http.aclose()
    if res.status_code != 200:
        raise chat_turns.TurnRefused(502, text(lang, "commit_error"))
    applied = int(res.json().get("applied") or 0)
    if applied:
        turn["state"] = "confirmed"
        turn["commit"] = {"applied": applied}
        return turn["commit"]
    turn["state"] = "none"
    turn["reply"]["notes"] = [chat_turns.note("info", text(lang, "sug_gone"))]
    return None


async def revert_suggestion(app: FastAPI, turn: dict, *, token: str | None) -> bool:
    """機械の手番の戻す — 外して次から出さない（``/api/sky/suggestions/dismiss``）。
    返り = 成否。失敗（通信・``status`` が 4xx/5xx）なら手番は戻さない（もう一度押せる）。"""
    http = make_http(app, token, {})
    try:
        resp = await http.post(
            "/api/sky/suggestions/dismiss", json={"items": turn["reply"]["suggestions"]}
        )
        ok = resp.status_code < 400
    except Exception:
        logger.warning("chat revert: dismiss request failed", exc_info=True)
        ok = False
    finally:
        await http.aclose()
    if not ok:
        return False
    turn["state"] = "reverted"
    turn["before"] = None
    return True


# ---- 図の薄い星座（GET /api/sky の proposed_datasets） -----------------------------------------


def proposed_datasets(root: Any) -> list[dict]:
    """話に添えた表のうち、取り込み済みで未公開のもの ``[{id, label, session_id, kinds}]``。
    手番の控え（``attachment``）だけを読む — ストアは読まない（``kinds`` は控えたもの）。
    戻した・公開した手番は出さない。"""
    out: list[dict] = []
    seen: set[str] = set()
    for head in chat_sessions.list_sessions(root):
        session = chat_sessions.load_session(root, head["id"])
        for turn in (session or {}).get("turns") or []:
            att = turn.get("attachment") or {}
            dataset_id = att.get("dataset_id")
            if turn.get("state") != "proposed" or not dataset_id or dataset_id in seen:
                continue
            data = registry.load_dataset(root, dataset_id)
            if data is None or (data.get("meta") or {}).get("promoted"):
                continue
            seen.add(dataset_id)
            out.append(
                {
                    "id": dataset_id,
                    "label": att.get("name") or dataset_id,
                    "session_id": head["id"],
                    "kinds": att.get("kinds") or [],
                }
            )
    return out
