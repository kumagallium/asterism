"""語 2 つの間の関係を、閉じた集合から 1 つ AI に選ばせる（PR 14c・pr14c-contract §1）。

渡すのは 2 語の名前・種別・役割と、両方が出る知見の文（番号 c1…・最大 8）だけ。id も IRI も
データの値も渡さない。AI が返すのは閉じた JSON 1 つ::

    {"rel": "makes", "from": "a", "evidence": ["c1"], "note": "理由 1 行"}

``none`` のときだけ、任意の欄 ``unstated_kind``（言えなかった関係の種類を短い名詞で）も返せる。
関係は ``PAIR_RELS`` の 11 個か ``none``（言えない）。根拠は番号だけ（引用文は書かせない）。
1 回の仕事は 1 組だけ。検査に落ちたら理由を添えて聞き直す（最初 + 2 回）。
やらないこと: 確からしさ・文脈の語・台帳への保存（ingest の仕事）。id・IRI・コードも書かせない。
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from typing import Any

from asterism_step0.language import _LANGUAGE_NAMES
from asterism_step0.llm import LLMClient
from asterism_step0.theme_propose import (
    _CODE_LIKE,
    ThemeDraftError,
    _complete_guided,
    _extract_json_object,
    _problem_tail,
)

__all__ = [
    "MAX_CLAIMS",
    "MAX_EVIDENCE",
    "NONE",
    "PAIR_RELS",
    "PAIR_SYSTEM_PROMPT",
    "SYMMETRIC_RELS",
    "PairProposeError",
    "build_pair_user",
    "pair_json_schema",
    "propose_pair",
    "validate_pair_answer",
]

#: 選べる関係（ingest の ``themes.RELATION_KINDS`` から ``quantity`` を除いたものと同じ）
PAIR_RELS = (
    "sub", "eq", "part", "lowers", "raises", "affects",
    "makes", "measures", "becomes", "competes", "has_property",
)  # fmt: skip
NONE = "none"
SYMMETRIC_RELS = ("eq", "competes")  # 向きが無い
MAX_CLAIMS = 8
MAX_EVIDENCE = 3
_ATTEMPTS = 3
_LEN_NOTE = 200
_LEN_KIND = 20  # unstated_kind の長さの上限
_LEN_CLAIM = 600  # prompt に載せる 1 文の長さ
_LEN_NAME = 80
_EVIDENCE_RE = re.compile(r"^c([0-9]{1,2})\Z")


class PairProposeError(ValueError):
    """AI の答えが読めない・検査に落ちた（聞き直しても）。"""


PAIR_SYSTEM_PROMPT = """\
You are given two terms, a and b, and numbered statements (c1, c2, ...) that mention both of
them. Decide which single RELATION between the two terms those statements state, or that they
state none. Each term has a kind (class = a kind of thing / property = something said or
measured about a thing) and maybe a role (material / method / concept / other).
Answer with ONE JSON object and nothing else:

{"rel": "makes", "from": "a", "evidence": ["c1"], "note": "..."}

- "rel": exactly one of
  sub (from is a kind of to; both the same kind) /
  eq (a and b are the same thing; both the same kind) /
  part (from is a part of to; both the same kind) /
  lowers (from lowers to) / raises (from raises to) / affects (from changes to);
  for these three, to is a property and from may be anything, another property included /
  makes (the method or process from produces the material to) /
  measures (the method from measures to, a material or a property) /
  becomes (the material from turns into the material to) /
  competes (the two materials compete: one forms instead of the other) /
  has_property (the material from has the property to) /
  none.
- "from": "a" or "b", the subject of the relation (the other term is the object).
  For eq and competes (no direction) write "a". For none write null.
- "evidence": one to three statement NUMBERS that state the relation. Numbers only, never a
  quotation. An empty list for none.
- "note": one short line saying why, in the language of the statements.
- Choose none when the statements only mention the two terms together, only compare them,
  state a relation that is not in the list, or when you would have to rely on general
  knowledge instead of what the statements say.
- Use eq only when a statement says the two are the same thing (not merely similar, and not
  because their abbreviations look alike).
- Only for none: "unstated_kind" (optional) is a short noun naming the kind of relation the
  statements express between the two terms when it is not in the list (e.g. condition,
  comparison, co-use, purpose), in the language of the statements, at most 20 characters.
  When there is no relation or you cannot tell, do not write it (never write none or
  unknown). Omit it for every other rel.
- Do NOT write ids, keys, IRIs, SPARQL or code.
"""


def pair_json_schema() -> dict:
    """guided decoding 用のスキーマ — 全欄必須・``rel`` と ``from`` は enum・根拠は番号。"""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["rel", "from", "evidence", "note"],
        "properties": {
            "rel": {"type": "string", "enum": [*PAIR_RELS, NONE]},
            "from": {"type": ["string", "null"], "enum": ["a", "b", None]},
            "evidence": {
                "type": "array",
                "maxItems": MAX_EVIDENCE,
                "items": {"type": "string", "pattern": "^c[0-9]{1,2}$"},
            },
            "note": {"type": "string", "maxLength": _LEN_NOTE},
            # 任意（required に入れない）。rel が none のときだけ意味を持つ
            "unstated_kind": {"type": "string", "maxLength": _LEN_KIND},
        },
    }


def _term_line(tag: str, t: dict) -> str:
    name = " ".join(str(t.get("name") or "").split())[:_LEN_NAME]
    role = t.get("role")
    meta = f"kind: {t.get('kind')}" + (f", role: {role}" if role else "")
    return f"{tag}: {name} ({meta})"


def build_pair_user(pair: dict, lang: str = "ja") -> str:
    """ユーザ側の文: 2 語・番号つきの知見・note を書く言語の 1 行。"""
    claims = [" ".join(str(c or "").split())[:_LEN_CLAIM] for c in pair["claims"]]
    code = (lang or "").strip()
    lang_line = f"Write the note in {_LANGUAGE_NAMES.get(code.lower(), code)}." if code else ""
    parts = [
        _term_line("a", pair["a"]),
        _term_line("b", pair["b"]),
        "",
        "Statements:",
        *(f"c{i}: {c}" for i, c in enumerate(claims, 1)),
    ]
    if lang_line:
        parts += ["", lang_line]
    return "\n".join(parts)


_KIND_NONE_WORDS = frozenset(
    {
        "none", "null", "n/a", "na", "unknown", "nothing", "no relation",
        "なし", "無し", "不明", "該当なし", "関係なし", "分からない", "わからない",
    }
)  # fmt: skip
_KIND_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)
_KIND_NUMBER = re.compile(r"^c\d+(?:,\s*c\d+)*\Z", re.IGNORECASE)
_KIND_CODE = re.compile(
    r"https?://|[<>{}`]|\b(?:SELECT|INSERT|DELETE|WHERE|PREFIX)\b", re.IGNORECASE
)


def _clean_kind(raw: Any) -> str | None:
    """``unstated_kind`` の正規化（step0 と ingest に同じ規則を 2 か所書き、見張りのテストで
    同じ答えを縛る）。通れば整えた文字列・捨てるなら ``None``。

    NFKC にそろえ・空白を詰め・制御と書式の文字（Cc・Cf）を取り除く。捨てる物: 空・20 文字超
    （切らない）・文字が無い（記号・数字だけ）・1 文字の英字・「無い／分からない」の言い換え・
    関係の短い名前と ``none``・知見の番号の形・スキームや URL・Markdown のリンク・
    コードの記号と SQL 語。"""
    if not isinstance(raw, str):
        return None
    kind = unicodedata.normalize("NFKC", raw)
    kind = " ".join(kind.split())
    kind = "".join(ch for ch in kind if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    if not kind or len(kind) > _LEN_KIND:
        return None
    if not any(ch.isalpha() for ch in kind):
        return None
    low = kind.casefold()
    if len(kind) == 1 and kind.isascii():
        return None
    if low in _KIND_NONE_WORDS or low in {*PAIR_RELS, "none"} or _KIND_NUMBER.match(low):
        return None
    if _KIND_SCHEME.match(kind) or "//" in kind or low.startswith("www."):
        return None
    if "[" in kind or "]" in kind or _KIND_CODE.search(kind):
        return None
    return kind


def validate_pair_answer(obj: Any, n_claims: int) -> dict:
    """答えを正規化する → ``{rel, from, evidence, note}``（``none`` で種類があれば
    ``unstated_kind`` も）。
    形が合わなければ :class:`PairProposeError`。"""
    if not isinstance(obj, dict):
        raise PairProposeError("$: must be an object")
    rel = obj.get("rel")
    if rel != NONE and rel not in PAIR_RELS:
        raise PairProposeError(f"$.rel: must be one of {[*PAIR_RELS, NONE]}")
    note = obj.get("note")
    note = " ".join(note.split()) if isinstance(note, str) else ""
    note = "" if _CODE_LIKE.search(note) else note[:_LEN_NOTE]
    if rel == NONE:
        out = {"rel": NONE, "from": None, "evidence": [], "note": note}
        kind = _clean_kind(obj.get("unstated_kind"))
        if kind:  # 無いときは欄ごと出さない（古い答えと同じ形）
            out["unstated_kind"] = kind
        return out
    frm = obj.get("from")
    if rel in SYMMETRIC_RELS:
        frm = "a"
    elif frm not in ("a", "b"):
        raise PairProposeError('$.from: must be "a" or "b"')
    ev = obj.get("evidence")
    if not isinstance(ev, list):
        raise PairProposeError("$.evidence: must be a list of statement numbers")
    kept: list[str] = []
    for e in ev:
        m = _EVIDENCE_RE.match(e.strip()) if isinstance(e, str) else None
        if m and 1 <= int(m.group(1)) <= n_claims and e.strip() not in kept:
            kept.append(e.strip())
    if not kept:
        raise PairProposeError(
            f"$.evidence: a relation needs one to {MAX_EVIDENCE} statement numbers"
            f" between c1 and c{n_claims}"
        )
    return {"rel": rel, "from": frm, "evidence": kept[:MAX_EVIDENCE], "note": note}


def propose_pair(
    llm: LLMClient, pair: dict, lang: str = "ja", *, on_call: Callable[[], None] | None = None
) -> dict:
    """1 組について聞く → 検査済みの ``{rel, from, evidence, note}``。読めない・検査に落ちたら
    理由を添えて聞き直し（最初 + 2 回）、3 回とも駄目なら :class:`PairProposeError`。"""
    claims = pair.get("claims")
    if not isinstance(claims, list) or not 1 <= len(claims) <= MAX_CLAIMS:
        raise ValueError(f"claims must be 1 to {MAX_CLAIMS} statements")
    for k in ("a", "b"):
        t = pair.get(k)
        if not isinstance(t, dict) or not " ".join(str(t.get("name") or "").split()):
            raise ValueError(f"term {k} needs a name")
    schema = pair_json_schema()
    base = build_pair_user(pair, lang)
    problem: str | None = None
    last: Exception | None = None
    for _ in range(_ATTEMPTS):
        text = _complete_guided(llm, PAIR_SYSTEM_PROMPT, base + _problem_tail(problem), schema)
        if on_call is not None:  # complete が成功して返るたび（聞き直しの各回も）。使用量の記録用
            on_call()
        try:
            return validate_pair_answer(_extract_json_object(text), len(claims))
        except (PairProposeError, ThemeDraftError) as exc:
            last = exc
            problem = str(exc)
    raise PairProposeError(str(last))
