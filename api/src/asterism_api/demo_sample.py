"""同梱の見本を、すでにある環境へ届ける（ADR kantan K62）。

デスクトップ版は初回の起動で 1 度だけ、同梱の見本（``datasets/world/snapshot.tar``）を
取り込む。以後の見本の直しが既存の環境に届かなかった（「入れ直すまで英字のまま」）。
起動のたびに、環境に入っている見本と同梱の見本を突き合わせ、**利用者が触っていない
単位だけ**を新しい版に入れ替える。

3 つの部品でできている:

* **改訂台帳**（``datasets/world/sample_revisions.json``・追記専用）。配った版ごとの
  「指紋」と、その版の各ファイル・各ツールの sha を残す。「環境のこのファイルは、
  過去に配ったどれかの版のもの＝利用者は触っていない」を、台帳の突き合わせで決定論に
  言えるようにするための記録。tar にも同じものを ``sample/revisions.json`` として
  入れて運ぶ（``ASTERISM_DEMO_SNAPSHOT`` で tar だけ渡されても動く）。
* **単位の表**（:data:`UNIT_TABLE`）。tar の member は、ちょうど 1 つの単位か「無視」に
  属する。単位ごとに、入れ替えるか保留するかを決める。
* **計画**（:func:`plan_refresh`）と**実行**（:func:`refresh_bundled_sample`）。計画は
  純関数（I/O なし）で、実行は registry・store に書く。

書き方の規律（契約メモ §7）: 履歴（``history/``）を作らない・ファイルはバイトのまま
原子的に置き換える・meta.json は最後に 1 回だけ原子的に書く。途中で落ちても、ファイルは
「配ったどれかの版」なので、次の起動でまた「触っていない」と判定されて収束する。

**データが変わる版**（同梱の canonical_sha256 ≠ 環境の ``imported.canonical_sha256``）も、
利用者がデータに手を加えていない環境には届ける。design・data・tools を 1 つの群として
「全部入れるか全部保留にするか」を決める（RML は design、データは data にあるので、片方
だけ進むと設計とデータが食い違うため）。入れ替えの順と、途中で落ちたときの収束は
:func:`_swap_data`・:func:`_recover` の docstring に書いた。引用の住所（行の IRI）が動く版は
自動では入れない（``ids_move`` で保留）。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import os
import re
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from asterism import substrate

from . import exchange, registry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 単位の表（1 か所）。tar の全 member は、ちょうど 1 つの単位か「無視」に属する。

LEDGER_MEMBER = "sample/revisions.json"
REGISTRY_PREFIX = "registry/"
META_MEMBER = "registry/meta.json"
TOOLS_MEMBER = "registry/query_tools.yaml"

UNIT_DESIGN = "design"
UNIT_DESCRIPTION = "description"
UNIT_TOOLS = "tools"
UNIT_NAME = "name"
UNIT_DATA = "data"

# 印の ``units`` に載せうる単位。data は入れ替えの結果を ``units`` でなく印の ``data``
# （``{"live_graph": …}``）に持つ（データが変わらない版では、data 単位は何もしないため）。
STAMP_UNITS = (UNIT_DESIGN, UNIT_DESCRIPTION, UNIT_TOOLS, UNIT_NAME)

# 単位 → tar の member（完全一致）と、member の接頭辞。name は meta の欄で member を持たない。
UNIT_TABLE: dict[str, dict[str, tuple[str, ...]]] = {
    UNIT_DESIGN: {
        "members": (
            "registry/mapping.yaml",
            "registry/model.yaml",
            "registry/diagram.md",
            "registry/mapping.rml.ttl",
        ),
        "prefixes": (),
    },
    UNIT_DESCRIPTION: {
        "members": ("registry/mie.yaml", "registry/metadata.ttl"),
        "prefixes": (),
    },
    UNIT_TOOLS: {"members": (TOOLS_MEMBER,), "prefixes": ()},
    UNIT_NAME: {"members": (), "prefixes": ()},
    UNIT_DATA: {
        "members": ("graphs/canonical.ttl",),
        "prefixes": ("registry/source/",),
    },
}
# 単位に属さず、指紋にも入れない member。
IGNORED_MEMBERS: tuple[str, ...] = (
    "manifest.json",
    META_MEMBER,
    "registry/proposal.md",
    LEDGER_MEMBER,
)
IGNORED = "ignored"

# 見本の設計を「人が触った」印になるファイル（あれば design を保留する）。
SOURCE_PREFIX = "registry/source/"
CANONICAL_MEMBER = "graphs/canonical.ttl"
APPLIED_BATCHES_DIR = ".applied_batches"

DECISION_FILES = (
    "display-meta.json",
    "column-decisions.json",
    "column-meanings.json",
    "handles.json",
    # 表の形をととのえる層の台帳（main の _RESHAPE_LEDGER_FILE）。利用者が元の表から
    # 派生表を作ると置かれる。設計はその派生表を前提にするので、設計を保留する。
    "reshape.json",
)

# 保留の理由コード（人向けの画面には出さない。画面の文言への翻訳は別の作業）。
HELD_EDITED = "edited"
# データが変わる版で、データの群を入れられない（環境の事実が読めなかったときの総称。
# 読めたときは下の細かい理由で出す）。
HELD_DATA = "data"
HELD_APPENDED = "appended"  # 追記した環境（利用者のデータを失うので入れない）
HELD_REINGESTED = "reingested"  # 取り込み直した・版の指し先が違う
HELD_UNSETTLED = "unsettled"  # control と meta が食い違い、回復もできない
HELD_IDS_MOVE = "ids_move"
HELD_IDS_UNKNOWN = "ids_unknown"
HELD_DECISIONS = "decisions"

# 台帳の seq がこれ以下の版は、印（meta.sample）を書く仕組みより前に配った版（A・B・C）。
# 印の無い環境が受け取りえたのはこの範囲だけ — 「利用者がツールを消した」を、台帳の
# どの版まで遡って言えるかの上限に使う。台帳は追記専用なので、この値は変わらない。
PRE_STAMP_LAST_SEQ = 3

# 印の ``pending``（データを入れ替えたあと、まだ済んでいない派生）。
PENDING_ONTOLOGY = "design_projection"
PENDING_CROSSWALK = "crosswalk"
PENDING_TOGOMCP = "togomcp"
_PENDING_ORDER = (PENDING_ONTOLOGY, PENDING_CROSSWALK, PENDING_TOGOMCP)

# 追記した印（meta の欄）。どれかが真なら、環境のデータには利用者のデータが混ざっている。
_APPEND_META_KEYS = ("feed", "append_seq", "appends", "triples_appended", "last_appended_at")

_DATASET_ENV = "ASTERISM_DEMO_DATASET"
_MAX_TAR_BYTES = 64 * 1024 * 1024
_ID_RE = re.compile(r"[a-z0-9-]{1,128}")


class BundleError(Exception):
    """同梱の tar が読めない・台帳が中身と合わない。何もせずに戻る合図。"""


def unit_of_member(name: str) -> str | None:
    """tar の member が属する単位（:data:`UNIT_TABLE` のキー）／ :data:`IGNORED` ／
    どれにも属さなければ ``None``（テストで「None が 1 つも無い」を固定する）。"""
    if name in IGNORED_MEMBERS:
        return IGNORED
    for unit, spec in UNIT_TABLE.items():
        if name in spec["members"] or any(name.startswith(p) for p in spec["prefixes"]):
            return unit
    return None


def _unit_paths(unit: str, present: Iterable[str]) -> list[str]:
    """``unit`` の member のうち ``present`` にあるもの（表の順）。"""
    have = set(present)
    return [m for m in UNIT_TABLE[unit]["members"] if m in have]


def _rel(member: str) -> str:
    """``registry/`` の下の member → データセットのディレクトリ内の相対パス。"""
    return member[len(REGISTRY_PREFIX) :]


# ---------------------------------------------------------------------------
# 指紋


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def fingerprint(obj: Any) -> str:
    """正規化 JSON（sort_keys・ensure_ascii=False・区切り固定）の sha256 の先頭 16 桁。"""
    text = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def tool_digest(tool: Any) -> str:
    """クエリツール 1 件の定義の digest（キーの並びに依らない）。"""
    text = json.dumps(tool, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_tools(text: str) -> list[Any]:
    """query_tools.yaml の ``tools`` 一覧。形が違えば ``ValueError``。"""
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"query_tools.yaml is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("query_tools.yaml is not a mapping")
    tools = data.get("tools")
    if tools is None:
        return []
    if not isinstance(tools, list):
        raise ValueError("query_tools.yaml: 'tools' is not a list")
    return tools


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]:
        return str(tool["name"])
    return None


def tools_digests(text: str) -> dict[str, str]:
    """``{ツール名: 定義の digest}``。名前の無いツールがあれば ``ValueError``。"""
    out: dict[str, str] = {}
    for tool in parse_tools(text):
        name = _tool_name(tool)
        if name is None:
            raise ValueError("a query tool has no name")
        out[name] = tool_digest(tool)
    return out


def registry_file_shas(members: Mapping[str, bytes]) -> dict[str, str]:
    """台帳の ``files``: ``registry/`` 以下の全ファイルの sha256（meta.json・無視するものを除く）"""
    return {
        name: sha256_hex(blob)
        for name, blob in sorted(members.items())
        if name.startswith(REGISTRY_PREFIX) and unit_of_member(name) != IGNORED
    }


def entry_revision(entry: Mapping[str, Any]) -> str:
    """台帳の 1 エントリの ``revision`` — ``name``・``canonical_sha256``・``files``・
    ``tools`` の指紋。``exported_at`` は入れない（中身が同じ再ビルドで変わらないように）。"""
    return fingerprint(
        {
            "name": entry["name"],
            "canonical_sha256": entry["canonical_sha256"],
            "files": entry["files"],
            "tools": entry["tools"],
        }
    )


def unit_revisions(entry: Mapping[str, Any]) -> dict[str, str]:
    """単位ごとの revision（その単位の member の sha・name・ツールの指紋）。"""
    files: Mapping[str, str] = entry["files"]
    out: dict[str, str] = {}
    for unit in (UNIT_DESIGN, UNIT_DESCRIPTION):
        out[unit] = fingerprint({p: files[p] for p in _unit_paths(unit, files)})
    out[UNIT_TOOLS] = fingerprint({"tools": entry["tools"]})
    out[UNIT_NAME] = fingerprint({"name": entry["name"]})
    out[UNIT_DATA] = fingerprint(
        {
            "canonical_sha256": entry["canonical_sha256"],
            "source": {p: s for p, s in files.items() if p.startswith("registry/source/")},
        }
    )
    return out


# ---------------------------------------------------------------------------
# tar と台帳


def read_members(payload: bytes) -> dict[str, bytes]:
    """同梱の tar（gzip の tar）を、安全に全部メモリへ読む。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as tar:
            return exchange._safe_members(tar, _MAX_TAR_BYTES)
    except Exception as exc:
        raise BundleError(f"cannot read the bundled sample: {exc}") from exc


def _snapshot_name(members: Mapping[str, bytes]) -> str:
    try:
        meta = json.loads(members[META_MEMBER].decode("utf-8"))
        name = meta.get("name")
        if isinstance(name, str) and name:
            return name
    except (KeyError, ValueError):
        pass
    try:
        manifest = json.loads(members["manifest.json"].decode("utf-8"))
        return str(manifest["name"])
    except (KeyError, ValueError) as exc:
        raise BundleError("the bundled sample has no name") from exc


def entry_from_members(
    members: Mapping[str, bytes], *, seq: int, note: Mapping[str, str]
) -> dict[str, Any]:
    """tar の中身から、台帳の 1 エントリを計算する（手で写さない）。"""
    manifest = json.loads(members["manifest.json"].decode("utf-8"))
    tools_text = members[TOOLS_MEMBER].decode("utf-8") if TOOLS_MEMBER in members else ""
    entry: dict[str, Any] = {
        "seq": seq,
        "note": {"ja": note["ja"], "en": note["en"]},
        "name": _snapshot_name(members),
        "canonical_sha256": manifest["canonical_sha256"],
        "files": registry_file_shas(members),
        "tools": tools_digests(tools_text) if tools_text.strip() else {},
    }
    entry["revision"] = entry_revision(entry)
    return entry


def revision_of_members(members: Mapping[str, bytes]) -> str:
    """tar の中身から計算した、いまの版の ``revision``。"""
    return entry_from_members(members, seq=0, note={"ja": "", "en": ""})["revision"]


_ENTRY_KEYS = ("seq", "revision", "note", "name", "canonical_sha256", "files", "tools")


def parse_ledger(text: str) -> list[dict[str, Any]]:
    """台帳の JSON を読み、形を検める。``seq`` は 1 から 1 ずつ増える。"""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"the sample ledger is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("the sample ledger must be a non-empty list")
    for i, entry in enumerate(data, start=1):
        if not isinstance(entry, dict) or any(k not in entry for k in _ENTRY_KEYS):
            raise ValueError(f"sample ledger entry {i} is missing keys")
        if entry["seq"] != i:
            raise ValueError(f"sample ledger seq must be 1,2,3,... (entry {i} has {entry['seq']})")
        note = entry["note"]
        if not (isinstance(note, dict) and isinstance(note.get("ja"), str)):
            raise ValueError(f"sample ledger entry {i} has no note")
        if not (isinstance(entry["files"], dict) and isinstance(entry["tools"], dict)):
            raise ValueError(f"sample ledger entry {i} has malformed files/tools")
    return data


def format_ledger(entries: Sequence[Mapping[str, Any]]) -> str:
    """台帳を決定論的に書く（sort_keys・indent 2・ensure_ascii=False・末尾改行）。"""
    return json.dumps(list(entries), sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def load_ledger_file(path: Path) -> list[dict[str, Any]]:
    return parse_ledger(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Bundled:
    """同梱の見本 1 つぶん。``revision``・``units`` は tar の中身から計算した値で、
    台帳の最新エントリと一致することを読むときに確かめてある。"""

    dataset_id: str
    name: str
    canonical_sha256: str
    files: dict[str, str]
    tools: dict[str, str]
    tool_defs: list[dict[str, Any]]
    members: dict[str, bytes]
    ledger: list[dict[str, Any]]
    latest: dict[str, Any]
    units: dict[str, str]

    @property
    def seq(self) -> int:
        return int(self.latest["seq"])

    @property
    def revision(self) -> str:
        return str(self.latest["revision"])

    def design_artifacts(self) -> dict[str, str]:
        """設計の成果物（``registry.load_dataset`` の ``artifacts`` と同じキー）。"""
        return {
            _rel(p): self.members[p].decode("utf-8") for p in _unit_paths(UNIT_DESIGN, self.members)
        }


def read_bundled(payload: bytes) -> Bundled:
    """同梱の tar を読む。読めない・台帳が無い／中身と合わない → :class:`BundleError`。"""
    members = read_members(payload)
    if LEDGER_MEMBER not in members:
        raise BundleError(f"the bundled sample has no {LEDGER_MEMBER}")
    try:
        ledger = parse_ledger(members[LEDGER_MEMBER].decode("utf-8"))
        manifest = json.loads(members["manifest.json"].decode("utf-8"))
        dataset_id = str(manifest["dataset_id"])
        tools_text = members[TOOLS_MEMBER].decode("utf-8") if TOOLS_MEMBER in members else ""
        tool_defs = parse_tools(tools_text) if tools_text.strip() else []
        computed = entry_from_members(members, seq=0, note={"ja": "", "en": ""})
    except BundleError:
        raise
    except Exception as exc:
        raise BundleError(f"the bundled sample is malformed: {exc}") from exc
    if not _ID_RE.fullmatch(dataset_id):
        raise BundleError(f"invalid dataset id in the bundled sample: {dataset_id!r}")
    latest = ledger[-1]
    if latest["revision"] != computed["revision"]:
        raise BundleError(
            "the bundled sample's ledger does not match its contents "
            f"(ledger {latest['revision']} != contents {computed['revision']})"
        )
    return Bundled(
        dataset_id=dataset_id,
        name=computed["name"],
        canonical_sha256=computed["canonical_sha256"],
        files=computed["files"],
        tools=computed["tools"],
        tool_defs=[t for t in tool_defs if _tool_name(t) is not None],
        members=dict(members),
        ledger=ledger,
        latest=latest,
        units=unit_revisions(computed),
    )


# ---------------------------------------------------------------------------
# 印（stamp）


def build_stamp(
    bundled: Bundled,
    reached: Iterable[str],
    held: Sequence[Mapping[str, str]],
    applied_at: str,
    *,
    tools_seq: int | None = None,
    data: Mapping[str, Any] | None = None,
    pending: Sequence[str] | None = None,
) -> dict[str, Any]:
    """meta.json の ``sample`` 欄。``units`` には「同梱と同じ状態まで届いた単位」だけを入れる。

    ``tools_seq`` は「ツールの合流を最後にやり終えた版の seq」（省略は ``bundled.seq``）。
    ツール単位が保留（読めない・data 保留）のままでも ``seq`` は進むので、ツールについて
    「環境が受け取った版」は ``seq`` でなくこちらで持つ — でないと、その間に同梱へ入った
    新しいツールを、次の起動で「利用者が消した」と取り違えて永久に配らない。

    ``data`` はデータを入れ替えた環境の印（``{"live_graph": 入れ替えた graph}``）。以後の
    起動で「利用者が取り込み直していない」を確かめる期待値になるので、一度書いたら
    引き継ぐ。``pending`` は入れ替えのあと済んでいない派生の名前（空なら済み）。
    """
    have = set(reached)
    out: dict[str, Any] = {
        "seq": bundled.seq,
        "revision": bundled.revision,
        "applied_at": applied_at,
        "tools_seq": bundled.seq if tools_seq is None else tools_seq,
        "units": {u: bundled.units[u] for u in STAMP_UNITS if u in have},
        "held": [dict(h) for h in held],
    }
    if data is not None:
        out["data"] = dict(data)
    if data is not None or pending:
        out["pending"] = list(pending or [])
    return out


def _valid_stamp(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    if not isinstance(raw.get("seq"), int) or isinstance(raw.get("seq"), bool):
        return None
    units = raw.get("units")
    held = raw.get("held")
    tools_seq = raw.get("tools_seq")
    if not isinstance(tools_seq, int) or isinstance(tools_seq, bool):
        tools_seq = raw["seq"]
    data = raw.get("data")
    pending = raw.get("pending")
    out: dict[str, Any] = {
        "seq": raw["seq"],
        "revision": raw.get("revision"),
        "tools_seq": tools_seq,
        "units": units if isinstance(units, dict) else {},
        "held": held if isinstance(held, list) else [],
        "pending": [p for p in pending if isinstance(p, str)] if isinstance(pending, list) else [],
    }
    if isinstance(data, dict):
        out["data"] = data
    return out


def stamp_core(stamp: Mapping[str, Any]) -> tuple[Any, ...]:
    """印の比べる部分（``applied_at`` は入れない — 結果が同じなら書き直さない）。"""
    return (
        stamp.get("seq"),
        stamp.get("revision"),
        stamp.get("tools_seq"),
        stamp.get("units"),
        stamp.get("held"),
        stamp.get("data"),
        stamp.get("pending") or [],
    )


# ---------------------------------------------------------------------------
# 計画（純関数）


def skip_reason(bundled: Bundled, meta: Mapping[str, Any]) -> str | None:
    """何もしない理由。動いてよければ ``None``。

    対象の見本かどうか（契約メモ §4 のうち meta で決まるもの）・下がった版・速い道。
    """
    if meta.get("origin") != "open":
        return "not-open"
    imported = meta.get("imported")
    if not isinstance(imported, dict):
        return "not-imported"
    if imported.get("canonical_sha256") not in {e["canonical_sha256"] for e in bundled.ledger}:
        return "unknown-canonical"
    if not meta.get("promoted"):
        return "not-promoted"
    if meta.get("status") == "retracted":
        return "retracted"
    stamp = _valid_stamp(meta.get("sample"))
    if stamp is not None:
        if stamp["seq"] > bundled.seq:
            return "newer-than-bundle"
        if (
            stamp["revision"] == bundled.revision
            and not stamp["held"]
            and not stamp["pending"]
            and all(stamp["units"].get(u) == bundled.units[u] for u in STAMP_UNITS)
        ):
            return "up-to-date"
    return None


def _known_shas(bundled: Bundled, path: str) -> set[str]:
    """``path`` の、過去に配ったどれかの版（と同梱）の sha。"""
    known = {e["files"][path] for e in bundled.ledger if path in e["files"]}
    if path in bundled.files:
        known.add(bundled.files[path])
    return known


def _touched(bundled: Bundled, paths: Sequence[str], files: Mapping[str, bytes]) -> list[str]:
    """環境のファイルのうち、配ったどの版とも一致しない（無い）もの。"""
    out: list[str] = []
    for p in paths:
        blob = files.get(p)
        if blob is None or sha256_hex(blob) not in _known_shas(bundled, p):
            out.append(p)
    return out


def _known_tool_digests(bundled: Bundled) -> dict[str, set[str]]:
    """ツール名 → 過去に配ったどれかの版（と同梱）の定義の digest。"""
    known: dict[str, set[str]] = {}
    for e in bundled.ledger:
        for name, digest in e["tools"].items():
            known.setdefault(name, set()).add(digest)
    for name, digest in bundled.tools.items():
        known.setdefault(name, set()).add(digest)
    return known


def _seen_tool_names(bundled: Bundled, seen_seq: int) -> set[str]:
    """環境が受け取ったことのある版（``seq <= seen_seq``）のツール名。

    同梱の最新の版だけにある名前は、環境がまだ受け取っていない — 環境に無くても
    利用者が消したのではない。
    """
    return {name for e in bundled.ledger if e["seq"] <= seen_seq for name in e["tools"]}


def merge_tools(
    bundled: Bundled, env_tools: Sequence[Any], *, seen_seq: int
) -> tuple[list[Any], list[str]]:
    """ツール名ごとの合流（契約メモ §8）。``(合流後の一覧, 保留する名前)``。

    * 環境にあり digest が過去の版のもの → 同梱の定義に置き換え（位置は保つ）
    * 同梱と同じ → そのまま／どちらでもない（利用者が直した・同名の別の定義）→ 保留
    * 環境に無い: 環境が受け取った版（``seq <= seen_seq``）にその名前がある → 利用者が
      消した（足さない）／無い → 足す
    * 環境にあって同梱に無いもの → 残す
    """
    known = _known_tool_digests(bundled)
    seen = _seen_tool_names(bundled, seen_seq)
    merged: list[Any] = list(env_tools)
    index: dict[str, int] = {}
    for i, tool in enumerate(merged):
        name = _tool_name(tool)
        if name is not None and name not in index:
            index[name] = i
    held: list[str] = []
    for tool in bundled.tool_defs:
        name = str(_tool_name(tool))
        digest = bundled.tools[name]
        i = index.get(name)
        if i is None:
            if name not in seen:
                merged.append(tool)
            continue
        current = tool_digest(merged[i])
        if current == digest:
            continue
        if current in known.get(name, set()):
            merged[i] = tool
        else:
            held.append(name)
    return merged, sorted(held)


def dump_tools(tools: Sequence[Any]) -> bytes:
    """registry の ``save_query_tool`` と同じ YAML の形（``{tools: [...]}``）。"""
    return yaml.safe_dump({"tools": list(tools)}, allow_unicode=True, sort_keys=False).encode(
        "utf-8"
    )


@dataclass
class RefreshPlan:
    """1 回の入れ替えでやること。``skip`` があれば何もしない。"""

    skip: str | None = None
    # データセットのディレクトリ内の相対パス → 新しいバイト
    replace: dict[str, bytes] = field(default_factory=dict)
    derive_design: bool = False  # classes の再計算と ontology graph の再投影
    reproject_description: bool = False  # meta graph の再投影
    new_name: str | None = None
    # 投影を待たずに「同梱と同じ状態まで届いた」単位
    reached: set[str] = field(default_factory=set)
    held: list[dict[str, str]] = field(default_factory=list)
    # ツールの合流を最後にやり終えた版の seq（印の ``tools_seq``）
    tools_seq: int = 0
    # データが変わる版で、design・data・tools の群を入れ替える（``replace`` に source・設計・
    # ツールが入っている。canonical と番号・graph の扱いは実行が決める）
    swap_data: bool = False


@dataclass(frozen=True)
class DataEnv:
    """データの群を入れてよいかの判定に使う、環境の事実（実行が集める。plan は I/O をしない）。"""

    # 環境の source/ の全ファイル（``registry/source/…`` → sha256。``.applied_batches`` は除く）
    source_shas: Mapping[str, str]
    # ``source/.applied_batches/`` がある（追記の途中で落ちた窓も拾う）
    applied_batches: bool
    # control graph の liveGraph・stagedGraph（無ければ ``None``）
    control_live: str | None
    control_staged: str | None


def _held(unit: str, reason: str, detail: str | None = None) -> dict[str, str]:
    out = {"unit": unit, "reason": reason}
    if detail:
        out["detail"] = detail
    return out


def _source_touched(bundled: Bundled, env_shas: Mapping[str, str]) -> list[str]:
    """環境の source/ のうち、配ったどの版とも一致しないもの（sha の違うもの・配ったことの
    無い名前・以前の版にあったのに消えたもの）。

    同梱に無い名前でも、sha が過去に配った版のどれかと一致すれば触った印ではない（後の版が
    source を外しても、手を加えていない環境は入れ替えられる。外れたファイルは入れ替えでも
    消さない — 利用者のデータを消す経路を増やさないため）。同梱の最新の版で初めて入る
    source が環境に無いのは触った印ではない。"""
    out: set[str] = set()
    in_bundle = {p for p in bundled.files if p.startswith(SOURCE_PREFIX)}
    for path, sha in env_shas.items():
        if sha not in _known_shas(bundled, path):
            out.add(path)
    earlier = bundled.ledger[:-1]
    for path in in_bundle - set(env_shas):
        if any(path in e["files"] for e in earlier):
            out.add(path)
    return sorted(out)


def _group_reasons(
    bundled: Bundled,
    *,
    meta: Mapping[str, Any],
    stamp: Mapping[str, Any] | None,
    files: Mapping[str, bytes],
    decision_files: Sequence[str],
    bundled_subjects: list[dict] | None,
    data_env: DataEnv | None,
) -> list[tuple[str, str | None]]:
    """データが変わる版で、design・data・tools の群を入れられない理由（空なら入れてよい）。

    入れてよい条件は全部（契約メモ contract_sample_refresh_pr2_data.md §2）: design を
    触っていない・decisions（``reshape.json`` を含む）が無い・source を触っていない・
    追記していない・取り込み直していない・control と meta が一致・ID の作り方が同じ。
    """
    if data_env is None:
        return [(HELD_DATA, None)]
    reasons: list[tuple[str, str | None]] = []

    touched = _touched(bundled, _unit_paths(UNIT_DESIGN, bundled.files), files)
    touched += _source_touched(bundled, data_env.source_shas)
    if touched:
        reasons.append((HELD_EDITED, ", ".join(sorted(touched))))

    appended = [k for k in _APPEND_META_KEYS if meta.get(k)]
    if data_env.applied_batches:
        appended.append(APPLIED_BATCHES_DIR)
    if appended:
        reasons.append((HELD_APPENDED, ", ".join(appended)))

    data_mark = (stamp or {}).get("data")
    expected_live = (data_mark or {}).get("live_graph") or substrate.versioned_graph_iri(
        bundled.dataset_id, 1
    )
    reingested: list[str] = []
    if meta.get("ingested"):
        reingested.append("ingested")
    if meta.get("graph_iri"):
        reingested.append("graph_iri")
    if data_env.control_staged:
        reingested.append("staged")
    if meta.get("live_graph") != expected_live:
        reingested.append("live_graph")
    if reingested:
        reasons.append((HELD_REINGESTED, ", ".join(reingested)))

    if decision_files:
        reasons.append((HELD_DECISIONS, ", ".join(sorted(decision_files))))

    env_subjects = meta.get("published_subjects")
    if env_subjects is None or bundled_subjects is None:
        reasons.append((HELD_IDS_UNKNOWN, None))
    elif env_subjects != bundled_subjects:
        reasons.append((HELD_IDS_MOVE, None))

    if data_env.control_live != meta.get("live_graph"):
        reasons.append((HELD_UNSETTLED, None))
    return reasons


def plan_refresh(
    bundled: Bundled,
    *,
    meta: Mapping[str, Any],
    files: Mapping[str, bytes],
    decision_files: Sequence[str],
    bundled_subjects: list[dict] | None,
    data_env: DataEnv | None = None,
) -> RefreshPlan:
    """判定だけの純関数（I/O なし）。

    ``files`` は環境の design・description・tools の member（``registry/…`` のキー）の
    バイト（無いものは含めない）。``decision_files`` は dataset ディレクトリにある
    :data:`DECISION_FILES` の名前。``bundled_subjects`` は同梱の設計から作った
    「公開時の ID の作り方」（main の ``_subjects_of_design``）。

    ``data_env`` は、データが変わる版（同梱の canonical_sha256 ≠ 環境のもの）でだけ使う。
    ``None`` はその事実が読めなかったとき — 群を入れず、理由 ``data`` で保留する。
    """
    reason = skip_reason(bundled, meta)
    if reason:
        return RefreshPlan(skip=reason)

    plan = RefreshPlan()
    held = plan.held
    stamp = _valid_stamp(meta.get("sample"))
    stamp_units: Mapping[str, Any] = stamp["units"] if stamp else {}
    imported = meta["imported"]
    data_differs = bundled.canonical_sha256 != imported.get("canonical_sha256")

    # --- design: 4 つとも触っていないときだけ、全部入れ替える -----------------
    design_paths = _unit_paths(UNIT_DESIGN, bundled.files)
    design_held = False
    group_held = False  # データが変わる版で、design・data・tools の群を保留する
    if data_differs:
        # design・data・tools は 1 つの群: 全部入れるか、同じ理由で全部保留にするか。
        group = _group_reasons(
            bundled,
            meta=meta,
            stamp=stamp,
            files=files,
            decision_files=decision_files,
            bundled_subjects=bundled_subjects,
            data_env=data_env,
        )
        if group:
            for unit in (UNIT_DESIGN, UNIT_DATA, UNIT_TOOLS):
                for reason_code, detail in group:
                    held.append(_held(unit, reason_code, detail))
            design_held = group_held = True
        else:
            plan.swap_data = True
            plan.derive_design = True
            for p in design_paths:
                if sha256_hex(files[p]) != bundled.files[p]:
                    plan.replace[_rel(p)] = bundled.members[p]
            for p in bundled.files:
                if p.startswith(SOURCE_PREFIX):
                    env_sha = data_env.source_shas.get(p) if data_env else None
                    if env_sha != bundled.files[p]:
                        plan.replace[_rel(p)] = bundled.members[p]
            design_held = True  # 下の「データが同じ版の design」の判定は通らない
    else:
        env_subjects = meta.get("published_subjects")
        if env_subjects is None or bundled_subjects is None:
            held.append(_held(UNIT_DESIGN, HELD_IDS_UNKNOWN))
            design_held = True
        elif env_subjects != bundled_subjects:
            held.append(_held(UNIT_DESIGN, HELD_IDS_MOVE))
            design_held = True
        if decision_files:
            held.append(_held(UNIT_DESIGN, HELD_DECISIONS, ", ".join(sorted(decision_files))))
            design_held = True
        touched = _touched(bundled, design_paths, files)
        if touched:
            held.append(_held(UNIT_DESIGN, HELD_EDITED, ", ".join(sorted(touched))))
            design_held = True
    if not design_held:
        for p in design_paths:
            if sha256_hex(files[p]) != bundled.files[p]:
                plan.replace[_rel(p)] = bundled.members[p]
        # 印の無い環境（v0.46.0〜v0.47.1）も、ファイルが同じでも派生を 1 回やり直す。
        if plan.replace or stamp_units.get(UNIT_DESIGN) != bundled.units[UNIT_DESIGN]:
            plan.derive_design = True
        else:
            plan.reached.add(UNIT_DESIGN)

    # --- description: 2 つとも触っていないときだけ -----------------------------
    desc_paths = _unit_paths(UNIT_DESCRIPTION, bundled.files)
    touched = _touched(bundled, desc_paths, files)
    if touched:
        held.append(_held(UNIT_DESCRIPTION, HELD_EDITED, ", ".join(sorted(touched))))
    else:
        changed = False
        for p in desc_paths:
            if sha256_hex(files[p]) != bundled.files[p]:
                plan.replace[_rel(p)] = bundled.members[p]
                changed = True
        # ファイルが同じでも、印が「この版の説明まで届いた」と言っていなければ投影し直す:
        # 前の起動で投影が失敗した（ファイルは置き換え済み）・置き換えと印の書き込みの間で
        # 落ちた、のどちらも、次の起動で収束させるため（design の派生と同じ 1 つの規則）。
        if changed or stamp_units.get(UNIT_DESCRIPTION) != bundled.units[UNIT_DESCRIPTION]:
            plan.reproject_description = True
        else:
            plan.reached.add(UNIT_DESCRIPTION)

    # --- tools: 丸ごと触っていなければバイトで、触っていればツール名ごとに合流 --
    # 環境がツールについて受け取った版: 印があれば印の ``tools_seq``、無ければ印より前に
    # 配った版すべて。合流をやり終えたら今回の版まで進める（保留・読めないときは進めない）。
    seen_seq = stamp["tools_seq"] if stamp else min(PRE_STAMP_LAST_SEQ, bundled.seq)
    plan.tools_seq = min(seen_seq, bundled.seq)
    if group_held:
        pass  # 群の保留で、上で理由を出した（ツールは合流もしない・受け取った版も進めない）
    else:
        env_bytes = files.get(TOOLS_MEMBER)
        if env_bytes is not None and sha256_hex(env_bytes) in _known_shas(bundled, TOOLS_MEMBER):
            if sha256_hex(env_bytes) != bundled.files.get(TOOLS_MEMBER):
                plan.replace[_rel(TOOLS_MEMBER)] = bundled.members[TOOLS_MEMBER]
            plan.reached.add(UNIT_TOOLS)
            plan.tools_seq = bundled.seq
        else:
            try:
                env_tools = parse_tools(env_bytes.decode("utf-8")) if env_bytes else []
            except (ValueError, UnicodeDecodeError):
                held.append(_held(UNIT_TOOLS, HELD_EDITED, _rel(TOOLS_MEMBER)))
            else:
                merged, edited = merge_tools(bundled, env_tools, seen_seq=seen_seq)
                plan.tools_seq = bundled.seq
                if merged != env_tools:
                    plan.replace[_rel(TOOLS_MEMBER)] = dump_tools(merged)
                for name in edited:
                    held.append(_held(UNIT_TOOLS, HELD_EDITED, name))
                if not edited:
                    plan.reached.add(UNIT_TOOLS)

    # --- name: 台帳のどれかの名前のままのときだけ ------------------------------
    env_name = meta.get("name")
    ledger_names = {e["name"] for e in bundled.ledger} | {bundled.name}
    if env_name == bundled.name:
        plan.reached.add(UNIT_NAME)
    elif env_name in ledger_names:
        plan.new_name = bundled.name
        plan.reached.add(UNIT_NAME)
    else:
        held.append(_held(UNIT_NAME, HELD_EDITED))

    return plan


# ---------------------------------------------------------------------------
# 実行


def _read_env_files(dataset_dir: Path, bundled: Bundled) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for unit in (UNIT_DESIGN, UNIT_DESCRIPTION, UNIT_TOOLS):
        for member in UNIT_TABLE[unit]["members"]:
            path = dataset_dir / _rel(member)
            if path.is_file():
                files[member] = path.read_bytes()
    return files


def _classes_of_diagram(diagram_md: str) -> list[str]:
    return registry.extract_classes(registry.mermaid_of(diagram_md))


async def refresh_bundled_sample(cfg: Any, client: Any, snapshot_path: Path | None) -> None:
    """起動のたびに、環境の見本と同梱の見本を突き合わせ、触っていない単位だけ入れ替える。

    best-effort — 例外は外へ出さず warning に留める（起動を止めない）。
    対象でないとき（単一ユーザーでない・``ASTERISM_DEMO_DATASET=0``・同梱の tar が
    無い／読めない・環境に見本が無い・見本でない・下がった版・すでに最新）は何もしない。
    """
    try:
        await _refresh(cfg, client, snapshot_path)
    except Exception:
        logger.warning("refresh_bundled_sample: failed (continuing)", exc_info=True)


_VERSION_NUMBER_RE = re.compile(r"/v(\d+)$")


def _graph_key(meta: Mapping[str, Any], dataset_id: str) -> str:
    """control graph の主語（lifespan の ``mark_graph_promoted`` と同じ引き方）。"""
    return str(meta.get("canonical_graph") or substrate.canonical_graph_iri(dataset_id))


def _source_shas(dataset_dir: Path) -> tuple[dict[str, str], bool]:
    """環境の source/ の全ファイルの sha（``registry/source/…`` のキー）と、
    ``.applied_batches`` があるか。"""
    base = dataset_dir / "source"
    shas: dict[str, str] = {}
    applied = (base / APPLIED_BATCHES_DIR).exists()
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            rel = path.relative_to(base)
            if APPLIED_BATCHES_DIR in rel.parts or not path.is_file():
                continue
            if registry.is_atomic_tmp_name(path.name):
                continue  # 置き換えの途中で落ちて残った一時ファイル（利用者のものではない）
            shas[SOURCE_PREFIX + rel.as_posix()] = sha256_hex(path.read_bytes())
    return shas, applied


async def _read_data_env(client: Any, dataset_dir: Path, key: str) -> DataEnv:
    shas, applied = _source_shas(dataset_dir)
    return DataEnv(
        source_shas=shas,
        applied_batches=applied,
        control_live=await substrate.live_graph_of(client, key),
        control_staged=await substrate.staged_graph_of(client, key),
    )


async def _number_floor(client: Any, dataset_id: str, meta: Mapping[str, Any]) -> int:
    """入れ替える graph の番号が超えるべき下限。

    ストアにある ``canonical/{id}/v*``・pendingDrop に残っている番号・``meta.live_graph`` の
    番号のどれよりも大きくする。番号は再利用しない: pendingDrop の印は再起動をまたいで
    残り、掃除は参照を見ずに消すので、印の残った番号を公開中にすると、起動のあとで
    公開中の graph が消える。
    """
    prefix = substrate.versioned_graph_iri(dataset_id, 1)[: -len("1")]
    graphs = list(await substrate.all_version_graphs(client, dataset_id=dataset_id))
    graphs += [g for g in await substrate.pending_drops(client, limit=1_000_000)]
    live = meta.get("live_graph")
    if isinstance(live, str):
        graphs.append(live)
    floor = 0
    for g in graphs:
        m = _VERSION_NUMBER_RE.search(g)
        if g.startswith(prefix) and m:
            floor = max(floor, int(m.group(1)))
    return floor


async def _swap_data(
    cfg: Any,
    client: Any,
    bundled: Bundled,
    plan: RefreshPlan,
    meta: dict[str, Any],
    key: str,
) -> tuple[dict[str, Any], str]:
    """データが変わる版の群を入れ替える。``(新しい meta, 新しい live の graph)``。

    順は変えない（変えると、途中で落ちたときに公開中のデータが消える）:

    0. 何も書く前に、rebase が要るのに置き換えるファイルに元の土台の IRI が入っていないかを
       確かめる（入っていれば入れずに止める）。
    1. **番号を取る**（``reserve_data_seq``。原子的に書く）。ストアの版・pendingDrop・
       ``meta.live_graph`` のどれよりも大きくなるまで取り直す。
    2. **新しい graph を載せる**（同梱の canonical を、import と同じ rebase の規則を通して）。
       件数が ``manifest.canonical_triples`` と違えば、その graph を消して中止（meta・control は
       旧のまま）。``set_staged_graph`` は呼ばない — staged と live が同じ graph を指す状態を
       作らないため（``mark_graph_promoted`` は staged を消さず、次の取り込みが公開中の graph を
       掃除に積んでしまう）。
    3. **ファイルを置き換える**（設計・source・ツール）。
    4. **meta を 1 回だけ原子的に書く**（live_graph・data_seq・件数・版・印）。
       ``mark_promoted`` は使わない（``published_subjects``・``alignment`` の上書きが付いてくる）。
    5. **公開を切り替える**（``promote_to_canonical``）。meta（4）は control（5）より前:
       逆にすると、5 の後・4 の前で落ちたとき、lifespan が control を旧へ巻き戻し、旧は既に
       掃除待ちなので両方を失う。4 の後・5 の前なら、lifespan が control を新へ寄せる。
    """
    dataset_id = bundled.dataset_id
    root: Path = cfg.registry_root
    manifest = json.loads(bundled.members["manifest.json"].decode("utf-8"))
    expected = manifest.get("canonical_triples")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise RuntimeError("the bundled manifest has no canonical_triples")

    # 0. rebase が要るのに、置き換えるファイルに元の土台の IRI が入っているなら、入れない
    #    （import はファイルも rebase するが、入れ替えはバイトのまま置く。rebase すると
    #    次の起動の「触っていない」判定が同梱の sha と合わなくなる）。何も書く前に止める。
    origin_base, rebase = exchange._iri_policy(manifest, cfg.iri_base)
    if rebase:
        needle = f"{origin_base}/datasets/".encode()
        for rel, blob in plan.replace.items():
            if Path(rel).suffix.lower() in exchange._TEXT_SUFFIXES and needle in blob:
                raise RuntimeError(
                    f"{rel} carries the origin IRI base; not swapping into a custom IRI base"
                )

    # 1. 番号
    floor = await _number_floor(client, dataset_id, meta)
    n = registry.reserve_data_seq(root, dataset_id)
    for _ in range(floor + 2):
        if n > floor:
            break
        n = registry.reserve_data_seq(root, dataset_id)
    else:
        raise RuntimeError("cannot reserve a fresh data number")
    new_live = substrate.versioned_graph_iri(dataset_id, n)

    # 2. 新しい graph
    ttl = bundled.members[CANONICAL_MEMBER]
    if rebase:
        ttl = exchange._rebase(ttl, origin_base, cfg.iri_base)
    await client.post_turtle_bytes(ttl, graph_iri=new_live)
    count = await client.graph_triple_count(new_live)
    if count != expected:
        await substrate.chunked_drop_graph(client, new_live)
        raise RuntimeError(
            f"the new graph holds {count} triples, the bundled manifest says {expected}"
        )

    # 3. ファイル
    if plan.replace:
        registry.replace_artifact_bytes(root, dataset_id, plan.replace)

    # 4. meta（1 回だけ）
    now = datetime.now(UTC).isoformat()
    bundled_meta = json.loads(bundled.members[META_MEMBER].decode("utf-8"))
    changes: dict[str, Any] = {
        "live_graph": new_live,
        "data_seq": n,
        "triple_count": count,
        "triples_promoted": count,
        "imported": {**(meta.get("imported") or {}), "canonical_sha256": bundled.canonical_sha256},
        "promoted_at": now,
    }
    for name in ("source_files", "has_source", "source_kind"):
        if name in bundled_meta:
            changes[name] = bundled_meta[name]
    classes = _classes_of_diagram(bundled.members["registry/diagram.md"].decode("utf-8"))
    if classes:
        changes["classes"] = classes
        changes["class_count"] = len(classes)
    version = int(meta.get("version") or 0) + 1
    changes["version"] = version
    changes["versions"] = [
        *list(meta.get("versions") or []),
        {
            "version": version,
            "promoted_at": now,
            "triples_promoted": count,
            "alignment": meta.get("alignment"),
        },
    ]
    pending = [PENDING_ONTOLOGY, PENDING_CROSSWALK]
    if cfg.togomcp_dir is not None:
        pending.append(PENDING_TOGOMCP)
    changes["sample"] = build_stamp(
        bundled,
        plan.reached,
        plan.held,
        now,
        tools_seq=plan.tools_seq,
        data={"live_graph": new_live},
        pending=pending,
    )
    new_meta = registry.update_meta_atomic(root, dataset_id, changes)
    if new_meta is None:
        raise RuntimeError("cannot write the sample's meta.json")

    # 5. 公開の切り替え（旧は pendingDrop に積まれる）
    await substrate.promote_to_canonical(client, key, new_live)
    return new_meta, new_live


async def _publish_togomcp(cfg: Any, client: Any, dataset_id: str, live_graph: str) -> None:
    from asterism_api import main as m
    from asterism_api import togomcp_sync

    data = registry.load_dataset(cfg.registry_root, dataset_id) or {}
    mie_text = await m._mie_text_for_publish(client, dataset_id, data.get("artifacts", {}))
    await asyncio.to_thread(
        togomcp_sync.publish_dataset,
        cfg.togomcp_dir,
        dataset_id,
        mie_text,
        live_graph,
        endpoint_url=cfg.togomcp_endpoint_url,
        endpoint_name=cfg.togomcp_endpoint_name,
    )


async def _run_derivations(
    cfg: Any, client: Any, dataset_id: str, live_graph: str, todo: Sequence[str]
) -> list[str]:
    """``todo``（:data:`PENDING_ONTOLOGY`・:data:`PENDING_CROSSWALK`・:data:`PENDING_TOGOMCP`）を
    やり直し、済んでいないものの名前を返す。全部冪等（DROP して載せる）。"""
    from asterism_api import main as m

    remaining: list[str] = []
    for item in _PENDING_ORDER:
        if item not in todo:
            continue
        try:
            if item == PENDING_ONTOLOGY:
                data = registry.load_dataset(cfg.registry_root, dataset_id) or {}
                if not await m._project_ontology_graph(
                    client, dataset_id, data.get("artifacts", {})
                ):
                    remaining.append(item)
            elif item == PENDING_CROSSWALK:
                await m._maybe_rebuild_crosswalk(client, cfg.registry_root, dataset_id)
            elif cfg.togomcp_dir is not None:
                await _publish_togomcp(cfg, client, dataset_id, live_graph)
        except Exception:
            logger.warning("refresh_bundled_sample: %s failed", item, exc_info=True)
            remaining.append(item)
    return remaining


async def _recover(
    cfg: Any, client: Any, bundled: Bundled, meta: dict[str, Any], key: str
) -> dict[str, Any]:
    """前の起動でデータの入れ替えの途中で落ちていたら、収束させる（判定より前）。

    * meta の live_graph が印の ``data.live_graph`` と同じで、control の liveGraph がそれと
      違う（入れ替えの 4 の後・5 の前で落ちた）→ ``promote_to_canonical`` を完了させる。
      refresh が走らない環境でも、lifespan が control を meta へ寄せる（旧は孤児として
      回収される）ので、公開中のデータは消えない。
    * 印に ``pending`` が残っている → その派生だけをやり直し、済んだら ``pending`` を減らす。
    新しい meta を返す。何もなければ渡された meta のまま。
    """
    stamp = _valid_stamp(meta.get("sample"))
    if stamp is None or not isinstance(stamp.get("data"), dict):
        return meta
    data_live = stamp["data"].get("live_graph")
    live = meta.get("live_graph")
    pending = list(stamp["pending"])
    if not data_live or live != data_live:
        # 利用者が取り込み直した（データはもう入れ替えたものではない）— 派生の続きは要らない
        if pending:
            return _write_pending(cfg, bundled, meta, [])
        return meta
    if await substrate.live_graph_of(client, key) != live:
        if await client.graph_triple_count(live) <= 0:
            logger.warning("refresh_bundled_sample: %s holds no triples — not promoting it", live)
            return meta
        await substrate.promote_to_canonical(client, key, live)
    if not pending:
        return meta
    remaining = await _run_derivations(cfg, client, bundled.dataset_id, live, pending)
    if remaining == pending:
        return meta
    return _write_pending(cfg, bundled, meta, remaining)


def _write_pending(
    cfg: Any, bundled: Bundled, meta: dict[str, Any], remaining: list[str]
) -> dict[str, Any]:
    sample = {**dict(meta.get("sample") or {}), "pending": remaining}
    new_meta = registry.update_meta_atomic(
        cfg.registry_root, bundled.dataset_id, {"sample": sample}
    )
    return new_meta if new_meta is not None else meta


async def _refresh(cfg: Any, client: Any, snapshot_path: Path | None) -> None:
    if not cfg.single_user:
        logger.info("refresh_bundled_sample: not single-user — skipping")
        return
    if (os.environ.get(_DATASET_ENV) or "").strip() == "0":
        logger.info("refresh_bundled_sample: %s=0 — skipping", _DATASET_ENV)
        return
    if snapshot_path is None or not snapshot_path.is_file():
        logger.info("refresh_bundled_sample: no bundled sample found — skipping")
        return
    try:
        bundled = read_bundled(snapshot_path.read_bytes())
    except (BundleError, OSError) as exc:
        logger.warning("refresh_bundled_sample: %s — skipping", exc)
        return

    root: Path = cfg.registry_root
    dataset_dir = root / bundled.dataset_id
    meta_path = dataset_dir / "meta.json"
    if not meta_path.is_file():
        logger.info("refresh_bundled_sample: no %s in the registry — skipping", bundled.dataset_id)
        return
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("refresh_bundled_sample: cannot read meta.json — skipping", exc_info=True)
        return
    if not isinstance(meta, dict):
        return
    reason = skip_reason(bundled, meta)
    if reason:
        logger.info("refresh_bundled_sample: %s — nothing to do", reason)
        return

    # main は local.py より後に読む（環境変数の既定を入れた後でだけ import できる）。
    from asterism_api.main import (
        _project_meta_graph,
        _project_ontology_graph,
        _subjects_of_design,
    )

    key = _graph_key(meta, bundled.dataset_id)
    # 前の起動の入れ替えが途中で落ちていたら、判定より前に収束させる。
    meta = await _recover(cfg, client, bundled, meta, key)

    data_env: DataEnv | None = None
    if bundled.canonical_sha256 != (meta.get("imported") or {}).get("canonical_sha256"):
        try:
            data_env = await _read_data_env(client, dataset_dir, key)
        except Exception:
            logger.warning("refresh_bundled_sample: cannot read the store", exc_info=True)
    plan = plan_refresh(
        bundled,
        meta=meta,
        files=_read_env_files(dataset_dir, bundled),
        decision_files=[n for n in DECISION_FILES if (dataset_dir / n).exists()],
        bundled_subjects=_subjects_of_design(bundled.design_artifacts()),
        data_env=data_env,
    )
    if plan.skip:
        logger.info("refresh_bundled_sample: %s — nothing to do", plan.skip)
        return

    old = _valid_stamp(meta.get("sample"))
    # 1. データが変わる版の群を入れ替える（ファイルの置き換え・meta・公開の切り替えを含む）。
    #    そうでなければ、ファイルを（履歴を作らず・バイトのまま・原子的に）置き換える。
    live_graph: str | None = None
    if plan.swap_data:
        meta, live_graph = await _swap_data(cfg, client, bundled, plan, meta, key)
    elif plan.replace:
        registry.replace_artifact_bytes(root, bundled.dataset_id, plan.replace)

    # 2. ストアの graph を、新しい成果物から投影し直す。
    reached = set(plan.reached)
    changes: dict[str, Any] = {}
    if plan.derive_design or plan.reproject_description:
        data = registry.load_dataset(root, bundled.dataset_id) or {}
        artifacts = data.get("artifacts", {})
        if plan.derive_design:
            classes = _classes_of_diagram(artifacts.get("diagram.md", ""))
            if classes:
                changes["classes"] = classes
                changes["class_count"] = len(classes)
            try:
                written = await _project_ontology_graph(client, bundled.dataset_id, artifacts)
            except Exception:
                logger.warning("refresh_bundled_sample: ontology projection failed", exc_info=True)
                written = 0
            if written:
                reached.add(UNIT_DESIGN)
        if plan.reproject_description:
            blank = not (artifacts.get("metadata.ttl") or "").strip()
            try:
                # 失敗を 0 件に丸めさせない: 説明が空のとき 0 件は「DROP が成功した」印にも
                # なるので、失敗（例外）と区別できるようにする。
                written = await _project_meta_graph(
                    client, bundled.dataset_id, artifacts, raise_on_failure=True
                )
            except Exception:
                logger.warning("refresh_bundled_sample: meta projection failed", exc_info=True)
                written = 0
                blank = False
            # 説明が空なら 0 件が正しい答えのとき（投影は空の graph の DROP になる）。
            if written or blank:
                reached.add(UNIT_DESCRIPTION)
    if plan.new_name is not None:
        changes["name"] = plan.new_name

    # データを入れ替えたときの、残りの派生（つながりのハブ・外部への配信）。
    # 印の ``data`` は引き継ぐ。``pending`` は済んでいないものだけ残す。
    data_mark = old.get("data") if old else None
    pending: list[str] = list(old["pending"]) if old else []
    if live_graph is not None:
        data_mark = {"live_graph": live_graph}
        # つながりのハブは、見本が参加者でなければ何もしない（``_maybe_rebuild_crosswalk``）。
        todo = [PENDING_CROSSWALK]
        pending = [] if UNIT_DESIGN in reached else [PENDING_ONTOLOGY]
        if cfg.togomcp_dir is not None:
            todo.append(PENDING_TOGOMCP)
        pending += await _run_derivations(cfg, client, bundled.dataset_id, live_graph, todo)

    # 3. 最後に meta を 1 回だけ、原子的に書く。変わるものが無ければ書かない。
    stamp = build_stamp(
        bundled,
        reached,
        plan.held,
        datetime.now(UTC).isoformat(),
        tools_seq=plan.tools_seq,
        data=data_mark,
        pending=pending,
    )
    old_now = _valid_stamp(meta.get("sample"))
    old_core = stamp_core(old_now) if old_now is not None else None
    if old_core != stamp_core(stamp):
        changes["sample"] = stamp
    changes = {k: v for k, v in changes.items() if k == "sample" or meta.get(k) != v}
    if not changes:
        if plan.held:
            # 保留のまま続く起動は書き込みが無い。ログだけでは入れ替わっていないことが
            # 分からなくなるので、判定のたびに理由コードを 1 行出す。
            logger.info(
                "refresh_bundled_sample: seq %s — still held %s",
                bundled.seq,
                [f"{h['unit']}:{h['reason']}" for h in plan.held],
            )
        return
    registry.update_meta_atomic(root, bundled.dataset_id, changes)
    logger.info(
        "refresh_bundled_sample: seq %s — replaced %s; reached %s; held %s%s",
        bundled.seq,
        sorted(plan.replace) or "nothing",
        sorted(reached),
        [f"{h['unit']}:{h['reason']}" for h in plan.held] or "nothing",
        f"; swapped the data into {live_graph}" if live_graph else "",
    )


async def write_seed_stamp(
    cfg: Any, dataset_id: str, payload: bytes, *, design_ok: bool, description_ok: bool
) -> None:
    """種まきが、取り込み・公開・投影を済ませた後に印を書く。

    新しい環境では、次の起動の :func:`refresh_bundled_sample` が 1 回目から速い道に
    なるようにするため。計算は refresh と同じ（:func:`build_stamp`）。best-effort。
    """
    try:
        bundled = read_bundled(payload)
        if bundled.dataset_id != dataset_id:
            return
        reached = {UNIT_TOOLS, UNIT_NAME}
        if design_ok:
            reached.add(UNIT_DESIGN)
        if description_ok:
            reached.add(UNIT_DESCRIPTION)
        stamp = build_stamp(bundled, reached, [], datetime.now(UTC).isoformat())
        registry.update_meta_atomic(cfg.registry_root, dataset_id, {"sample": stamp})
    except BundleError as exc:
        logger.info("seed_demo_dataset: no sample stamp — %s", exc)
    except Exception:
        logger.warning("seed_demo_dataset: writing the sample stamp failed", exc_info=True)
