"""論文の文から「関係」だけを AI に選ばせる（ui-redesign PR 12b 追補・design.md §20）。

``graph_propose`` の閉じた差分（用語の鍵・種類・関係・根拠・問い）は、弱いモデルが 1 回で整合させて
書けなかった（gpt-oss-120b の実機で 6 回とも別の欄で外れた）。そこで AI の仕事を
「名前 → 関係 → 名前＋根拠の文の番号」に絞る。AI が書くのはこれだけ::

    {"relations": [{"from": "...", "from_ja": "...|null", "from_en": "...|null",
                    "rel": "part|sub|lowers|raises|affects",
                    "to": "...", "to_ja": "...|null", "to_en": "...|null", "evidence": ["s12"]}]}

用語の鍵・種類・問い・理由・``use`` は書かせない（機械が組む — ``asterism_api.chat_paper``）。
引用文も書かせない（番号だけ・機械がストアから引く）。検証は 1 本ずつ（形の悪い 1 本はその本だけ
落として数える）。JSON が読めない・``relations`` が配列でないときだけ聞き直す（最初 + 2 回）。
AI にデータの値は渡さない（論文は利用者が添えた本文・既存の用語は名前だけ）。
"""

from __future__ import annotations

import re
from typing import Any

from asterism_step0.llm import LLMClient
from asterism_step0.theme_propose import (
    _CODE_LIKE,
    ThemeDraftError,
    _complete_guided,
    _extract_json_object,
    _lines,
    _problem_tail,
)

__all__ = [
    "MAX_RELATIONS",
    "PAPER_RELS",
    "PAPER_SYSTEM_PROMPT",
    "PaperProposeError",
    "paper_relations_json_schema",
    "propose_paper_relations",
    "validate_paper_relations",
]

#: 論文の関係の種類（``graph_diff`` の ``rel`` の部分集合）
PAPER_RELS = ("part", "sub", "lowers", "raises", "affects")
MAX_RELATIONS = 20
MAX_EVIDENCE = 3
_LEN_NAME = 80
_LEN_SENTENCE = 600  # prompt に載せる 1 文の長さ
_MAX_SENTENCES = 300
_MAX_TERMS = 200
_ATTEMPTS = 3
_EVIDENCE_RE = re.compile(r"^s[0-9]{1,4}\Z")


class PaperProposeError(ValueError):
    """AI の答えが JSON として読めない・``relations`` が配列でない（聞き直しても）。"""


PAPER_SYSTEM_PROMPT = """\
You read the numbered sentences of one paper and list the RELATIONS between terms that those
sentences state. Answer with ONE JSON object and nothing else:

{"relations": [
  {"from": "synthesis procedure", "from_ja": "合成手順", "from_en": "synthesis procedure",
   "rel": "part", "to": "MatPROV dataset", "to_ja": "MatPROV データセット",
   "to_en": "MatPROV dataset", "evidence": ["s12"]}
]}

- "from" / "to": a term exactly as the sentences write it (never translate it).
  "from_ja" / "to_ja": its Japanese name, "from_en" / "to_en": its English name
  (null when you do not know one).
- "rel": exactly one of
  part (from is a part of to) / sub (from is a kind of to) /
  lowers (from lowers to) / raises (from raises to) / affects (from changes to).
- "evidence": one to three sentence NUMBERS (s1, s2, ...) that state the relation. Numbers only,
  never a quotation. Both terms must appear in those sentences.
- At most 20 relations. Leave out a relation the sentences do not state.
- Reuse the exact name of an existing term (list below) when a sentence means it.
- Do NOT write keys, kinds, questions, reasons, SPARQL, IRIs or code.
"""


def paper_relations_json_schema() -> dict:
    """guided decoding 用のスキーマ — 全欄必須（``*_ja`` は null 可）・``rel`` は enum・
    ``evidence`` は番号の pattern。"""
    name = {"type": "string", "maxLength": _LEN_NAME}
    name_ja = {"type": ["string", "null"], "maxLength": _LEN_NAME}  # *_en も同じ形
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["relations"],
        "properties": {
            "relations": {
                "type": "array",
                "maxItems": MAX_RELATIONS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "from", "from_ja", "from_en", "rel", "to", "to_ja", "to_en", "evidence",
                    ],
                    "properties": {
                        "from": name,
                        "from_ja": name_ja,
                        "from_en": name_ja,
                        "rel": {"type": "string", "enum": list(PAPER_RELS)},
                        "to": name,
                        "to_ja": name_ja,
                        "to_en": name_ja,
                        "evidence": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": MAX_EVIDENCE,
                            "items": {"type": "string", "pattern": "^s[0-9]{1,4}$"},
                        },
                    },
                },
            }
        },
    }


def _name(v: Any) -> str | None:
    """名前 1 つ: 文字列・空白を畳む・``_LEN_NAME`` 字まで・コード／IRI に見えるものは不可。"""
    if not isinstance(v, str):
        return None
    s = " ".join(v.split())
    if not s or len(s) > _LEN_NAME or _CODE_LIKE.search(s):
        return None
    return s


def _one(o: Any) -> dict | None:
    if not isinstance(o, dict):
        return None
    frm, to = _name(o.get("from")), _name(o.get("to"))
    if frm is None or to is None or frm.casefold() == to.casefold():
        return None
    if o.get("rel") not in PAPER_RELS:
        return None
    ev = o.get("evidence")
    if not isinstance(ev, list) or not 1 <= len(ev) <= MAX_EVIDENCE:
        return None
    if not all(isinstance(e, str) and _EVIDENCE_RE.match(e.strip()) for e in ev):
        return None
    return {
        "from": frm,
        "from_ja": _name(o.get("from_ja")),
        "from_en": _name(o.get("from_en")),
        "rel": o["rel"],
        "to": to,
        "to_ja": _name(o.get("to_ja")),
        "to_en": _name(o.get("to_en")),
        "evidence": list(dict.fromkeys(e.strip() for e in ev)),
    }


def validate_paper_relations(raw: Any) -> dict:
    """答えを 1 本ずつ検証する → ``{relations, shape_dropped, over_limit}``。形の悪い 1 本は落として
    数え（``shape_dropped``）、20 本を超える分は別に数える（``over_limit``）。鍵や種類の欄が来ても
    無視する。``raw`` が object でない・``relations`` が配列でないときだけ
    :class:`PaperProposeError`。"""
    if not isinstance(raw, dict) or not isinstance(raw.get("relations"), list):
        raise PaperProposeError("$.relations: must be a list")
    items = raw["relations"]
    good = [r for r in (_one(o) for o in items) if r is not None]
    kept = good[:MAX_RELATIONS]
    return {
        "relations": kept,
        "shape_dropped": len(items) - len(good),
        "over_limit": len(good) - len(kept),
    }


def _user_message(
    sentences: list[dict], existing_terms: list[dict], lang: str | None, problem: str | None
) -> str:
    rows = [
        f"{x['id']} | " + " ".join(str(x.get("text") or "").split())[:_LEN_SENTENCE]
        for x in sentences[:_MAX_SENTENCES]
    ]
    terms = [
        f"{t.get('label_ja') or ''} | {t.get('label_en') or ''}"
        for t in existing_terms[:_MAX_TERMS]
    ]
    return (
        "# Sentences of the paper (number | sentence)\n"
        + _lines(rows)
        + "\n\n# Existing terms (日本語 | English)\n"
        + _lines(terms)
        + _problem_tail(problem)
    )


def propose_paper_relations(
    client: LLMClient,
    sentences: list[dict],
    existing_terms: list[dict],
    lang: str | None = None,
    *,
    attempts: int | None = None,
) -> dict:
    """1 回の guided call → ``{relations, shape_dropped}``（検証済み）。JSON が読めない・
    ``relations`` が配列でないときだけ、理由を名指しして聞き直す（最初 + 2 回）。

    ``sentences`` = ``[{id: "s1", text}]``・``existing_terms`` = ``{label_ja, label_en}`` の行
    （名前だけ）。三度目も外れたら :class:`PaperProposeError`。"""
    schema = paper_relations_json_schema()
    problem: str | None = None
    last: Exception | None = None
    for _ in range(_ATTEMPTS if attempts is None else max(1, attempts)):
        user = _user_message(sentences, existing_terms, lang, problem)
        text = _complete_guided(client, PAPER_SYSTEM_PROMPT, user, schema)
        try:
            return validate_paper_relations(_extract_json_object(text))
        except (PaperProposeError, ThemeDraftError) as exc:
            last = exc
            problem = str(exc)
    raise PaperProposeError(str(last))
