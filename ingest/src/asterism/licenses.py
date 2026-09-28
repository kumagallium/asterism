"""ライセンスの読み手（契約メモ contract_pr_d.md §1・担当 D1-license）。

正本はストア（``metadata.ttl`` の ``dcterms:license``。
``asterism.metadata`` の ``_build_schema_info``/``_project_schema_info`` が
``mie.yaml`` の ``schema_info.license`` と往復する）。ここはその値を
「配れるか」の判定に落とすだけの、決定論・LLM ゼロの純粋関数群。

``KNOWN_LICENSES`` は許可リスト（保守側 O13: リストに無い・不明・空はすべて
「不明」= ``None`` に倒す。NC/ND 系は配る相手の用途が分からないので明示的に
``False``）。値そのものの正しさ（本当にその dataset がそのライセンスかどうか）
はここでは検証しない — それは人間が ``PUT /api/datasets/{id}/license`` で
書く内容の責任。
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml
from rdflib import URIRef

from asterism import metadata, substrate
from asterism.datasets import datasets_root

# 再配布可（True）/ 不可（False）の許可リスト。ここに無い正規化済み値は
# redistributable() で None（不明）— 新しいライセンスを配れる扱いにするには、
# このリストに明示で足す変更が要る（O13 の「配れる判定は保守側」の実装）。
KNOWN_LICENSES: dict[str, bool] = {
    # 再配布可
    "CC0-1.0": True,
    "CC-BY-4.0": True,
    "CC-BY-3.0": True,
    "CC-BY-SA-4.0": True,
    "CC-BY-SA-3.0": True,
    "ODbL-1.0": True,
    "ODC-By-1.0": True,
    "PDDL-1.0": True,
    "MIT": True,
    "Apache-2.0": True,
    "BSD-2-Clause": True,
    "BSD-3-Clause": True,
    # 再配布不可（配る相手の用途が分からないので保守側に倒す）
    "CC-BY-NC-4.0": False,
    "CC-BY-ND-4.0": False,
    "CC-BY-NC-SA-4.0": False,
    "CC-BY-NC-ND-4.0": False,
    "proprietary": False,
    "all-rights-reserved": False,
}

# 大文字小文字だけが違う別名（例: 人が "cc-by-4.0" と書いた）から SPDX id へ。
_CASE_ALIASES: dict[str, str] = {key.lower(): key for key in KNOWN_LICENSES}

# 既知の URL だけを正規化する表（ADR dataset-description-in-the-store.md §3:
# 「それ以外は素通し」— 未知の URL はそのままの文字列で返る。ホスト名 + パス
# （スキーム・www.・末尾スラッシュを剥がした後、小文字化）をキーにする）。
_URL_ALIASES: dict[str, str] = {
    "creativecommons.org/publicdomain/zero/1.0": "CC0-1.0",
    "creativecommons.org/licenses/by/4.0": "CC-BY-4.0",
    "creativecommons.org/licenses/by/3.0": "CC-BY-3.0",
    "creativecommons.org/licenses/by-sa/4.0": "CC-BY-SA-4.0",
    "creativecommons.org/licenses/by-sa/3.0": "CC-BY-SA-3.0",
    "creativecommons.org/licenses/by-nc/4.0": "CC-BY-NC-4.0",
    "creativecommons.org/licenses/by-nd/4.0": "CC-BY-ND-4.0",
    "creativecommons.org/licenses/by-nc-sa/4.0": "CC-BY-NC-SA-4.0",
    "creativecommons.org/licenses/by-nc-nd/4.0": "CC-BY-NC-ND-4.0",
    "opendatacommons.org/licenses/odbl/1-0": "ODbL-1.0",
    "opendatacommons.org/licenses/by/1-0": "ODC-By-1.0",
    "opendatacommons.org/licenses/pddl/1-0": "PDDL-1.0",
    "opensource.org/licenses/mit": "MIT",
    "opensource.org/license/mit": "MIT",
    "apache.org/licenses/license-2.0": "Apache-2.0",
    "opensource.org/licenses/apache-2.0": "Apache-2.0",
    "opensource.org/licenses/bsd-2-clause": "BSD-2-Clause",
    "opensource.org/licenses/bsd-3-clause": "BSD-3-Clause",
}


def _url_key(value: str) -> str:
    """``https://www.creativecommons.org/licenses/by/4.0/`` -> the
    ``_URL_ALIASES`` key form (scheme/``www.``/trailing slash stripped)."""
    v = value.strip()
    for prefix in ("https://www.", "http://www.", "https://", "http://"):
        if v.lower().startswith(prefix):
            v = v[len(prefix) :]
            break
    return v.rstrip("/").lower()


def normalize_license(value: str | None) -> str | None:
    """SPDX id / URL / 大小文字違いの別名 -> SPDX id。未知はそのままの文字列
    (空・None のみ ``None``)。"""
    if value is None:
        return None
    v = value.strip()
    if not v:
        return None
    if v in KNOWN_LICENSES:
        return v
    if "://" in v:
        mapped = _URL_ALIASES.get(_url_key(v))
        return mapped if mapped is not None else v
    mapped = _CASE_ALIASES.get(v.lower())
    return mapped if mapped is not None else v


def redistributable(value: str | None) -> bool | None:
    """``True``/``False`` が :data:`KNOWN_LICENSES` に載っているときのみ、
    それ以外（未知・空・None）は ``None``（不明 = 保守側で「手元限り」）。"""
    normalized = normalize_license(value)
    if normalized is None:
        return None
    return KNOWN_LICENSES.get(normalized)


_DATASET_ID_RE = re.compile(r"[a-z0-9-]{1,128}")


def _license_from_store(registry_root: Path, dataset_id: str) -> str | None:
    """正本（``metadata.ttl`` の ``dcterms:license``）→ 無ければ投影の残り
    （``mie.yaml`` の ``schema_info.license`` — 古い・未移行のデータセット用の
    後方互換）。"""
    dataset_dir = registry_root / dataset_id
    metadata_path = dataset_dir / "metadata.ttl"
    if metadata_path.is_file():
        text = metadata_path.read_text(encoding="utf-8")
        if text.strip():
            graph = metadata.graph_from_turtle(text)
            subject = URIRef(substrate.dataset_iri(dataset_id))
            value = graph.value(subject, metadata.DCTERMS.license)
            if value is not None:
                s = str(value).strip()
                if s:
                    return s

    mie_path = dataset_dir / "mie.yaml"
    if mie_path.is_file():
        text = mie_path.read_text(encoding="utf-8")
        if text.strip():
            try:
                doc = metadata.parse_mie_yaml(text)
            except (yaml.YAMLError, ValueError):
                return None
            schema_info = doc.get("schema_info")
            if isinstance(schema_info, dict):
                value = schema_info.get("license")
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _license_from_dataset_toml(dataset_id: str) -> str | None:
    """同梱データセット（``datasets/<name>/dataset.toml``）の ``license`` キー
    （README に明記があるものだけ、その dataset.toml 自身に書かれている前提 —
    ここは読むだけで、書きはしない）。"""
    root = datasets_root()
    if root is None:
        return None
    path = root / dataset_id / "dataset.toml"
    if not path.is_file():
        return None
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("license") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def dataset_license(registry_root: Path, dataset_id: str) -> tuple[str | None, bool | None]:
    """``(正規化後のライセンス, 再配布可)``。判定不能は ``(None, None)`` か
    ``(<値>, None)``（値はあるが許可リストに無い）— どちらも O13 の「不明は
    手元限り」に倒すのは呼び出し側（``materials_for``）の仕事。"""
    if not _DATASET_ID_RE.fullmatch(dataset_id):
        return (None, None)
    raw = _license_from_store(registry_root, dataset_id)
    if raw is None:
        raw = _license_from_dataset_toml(dataset_id)
    normalized = normalize_license(raw)
    return (normalized, redistributable(normalized))
