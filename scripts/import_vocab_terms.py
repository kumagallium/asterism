#!/usr/bin/env python3
"""公式のオントロジー RDF から語を取り込み、`known_vocabs.yaml` を検証／追記する。

⭐**語は実ファイルの IRI からしか作らない。** カタログの不変条件は
「すべての語 IRI は実在・捏造しない」で、記憶で名前を書くのはその真逆にあたる。
このスクリプトは各語彙の `source`（authoritative な RDF）を取得し、

- 既にカタログにある語が **本当にその RDF に在るか**（drift / 誤記の検出）
- その RDF が提供する **未収録の語**（＝拡充の候補）

を報告する。`--write` を付けたときだけ、選んだ語を YAML の該当語彙の `terms:`
末尾に**追記**する（全書き換えはしない — この YAML は判断の理由を書いた
コメントが本体と同じくらい重要で、`yaml.safe_dump` はそれを消してしまう）。

カタログの語は `iri == namespace + name` で表される。この形にならない語彙
（EMMO のように不透明な IRI + `skos:prefLabel` で語を表すもの）は**採らずに
報告する** — 収録するにはカタログ側に明示 IRI の欄が要る（別 ADR）。

使い方::

    # 収録済みの語が実ファイルに在るか確かめる（全語彙）
    python scripts/import_vocab_terms.py --verify

    # 1 語彙の未収録候補を見る
    python scripts/import_vocab_terms.py --prefix dcterms

    # 選んで追記する（name をカンマ区切りで）
    python scripts/import_vocab_terms.py --prefix dcterms --add created,modified --write
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sys
import urllib.request
from pathlib import Path

import rdflib
import yaml

_ROOT = Path(__file__).resolve().parents[1]
_CATALOG = _ROOT / "ingest" / "src" / "asterism" / "grounding" / "known_vocabs.yaml"

_RDF = rdflib.RDF
_RDFS = rdflib.RDFS
_OWL = rdflib.OWL
_SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")

_CLASS_TYPES = {_OWL.Class, _RDFS.Class}
_PROPERTY_TYPES = {
    _OWL.ObjectProperty,
    _OWL.DatatypeProperty,
    _OWL.AnnotationProperty,
    _RDF.Property,
}


def _fetch(url: str, timeout: int = 60) -> bytes:
    # User-Agent 無しだと弾く配布元がある（dublincore.org / purl.org が 403）。
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "text/turtle, application/rdf+xml;q=0.9, */*;q=0.5",
            "User-Agent": "asterism-vocab-importer/1.0 (+https://github.com/kumagallium/asterism)",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def _parse(payload: bytes, url: str) -> rdflib.Graph:
    g = rdflib.Graph()
    suffix = url.rsplit(".", 1)[-1].lower()
    fmts = {"ttl": "turtle", "owl": "xml", "rdf": "xml", "nt": "nt", "jsonld": "json-ld"}
    order = [fmts.get(suffix, "turtle"), "xml", "turtle", "json-ld", "nt"]
    seen: set[str] = set()
    for fmt in order:
        if fmt in seen:
            continue
        seen.add(fmt)
        try:
            g.parse(data=payload, format=fmt)
            return g
        except Exception:  # noqa: BLE001,S112 — 形式は総当たりで当てるしかない
            continue
    raise SystemExit(f"parse failed for {url}")


def _label_of(g: rdflib.Graph, subject: rdflib.term.Node) -> str:
    """英語のラベル（無ければ言語指定なし → prefLabel）。無ければ空。"""
    for predicate in (_RDFS.label, _SKOS.prefLabel):
        best = ""
        for obj in g.objects(subject, predicate):
            if not isinstance(obj, rdflib.Literal):
                continue
            lang = (obj.language or "").lower()
            if lang.startswith("en"):
                return str(obj)
            if not lang and not best:
                best = str(obj)
        if best:
            return best
    return ""


def extract_terms_by_label(g: rdflib.Graph, namespace: str) -> list[dict]:
    """不透明 IRI の語彙用: 照合名は ``skos:prefLabel``、IRI は実ファイルの値そのまま。

    EMMO のように ``…#EMMO_4f2a…`` で語を鋳造する語彙は、局所名では誰も探せない
    （人も AI も「Crystal」で探す）。名前は prefLabel を採り、``iri`` を明示して
    実在する識別子を保つ — 捏造しないという不変条件は IRI 側の話。
    """
    found: dict[str, dict] = {}
    for kinds, kind in ((_CLASS_TYPES, "class"), (_PROPERTY_TYPES, "property")):
        for rdf_type in kinds:
            for subject in g.subjects(_RDF.type, rdf_type):
                if not isinstance(subject, rdflib.URIRef):
                    continue
                iri = str(subject)
                if not iri.startswith(namespace):
                    continue
                name = next(
                    (str(o) for o in g.objects(subject, _SKOS.prefLabel) if isinstance(o, rdflib.Literal)),
                    "",
                ).strip()
                # 照合に使えない名前（空・空白入り）は採らない
                if not name or " " in name:
                    continue
                if name in found and found[name]["kind"] == "class":
                    continue
                found[name] = {
                    "name": name,
                    "kind": kind,
                    "label": _label_of(g, subject) or name,
                    "iri": iri,
                }
    return sorted(found.values(), key=lambda t: t["name"])


def extract_terms(g: rdflib.Graph, namespace: str) -> list[dict]:
    """``namespace`` 直下にあり ``iri == namespace + name`` を満たす語だけ。

    局所名に ``/`` や ``#`` が残るもの（さらに下の階層）は採らない — 連結して
    元の IRI に戻らない語をカタログに入れると、実在しない IRI を名乗ることになる。
    """
    found: dict[str, dict] = {}
    for kinds, kind in ((_CLASS_TYPES, "class"), (_PROPERTY_TYPES, "property")):
        for rdf_type in kinds:
            for subject in g.subjects(_RDF.type, rdf_type):
                if not isinstance(subject, rdflib.URIRef):
                    continue
                iri = str(subject)
                if not iri.startswith(namespace):
                    continue
                name = iri[len(namespace) :]
                if not name or "/" in name or "#" in name:
                    continue
                # class と property の両方に型付けされていたら class を優先
                if name in found and found[name]["kind"] == "class":
                    continue
                found[name] = {"name": name, "kind": kind, "label": _label_of(g, subject) or name}
    return sorted(found.values(), key=lambda t: t["name"])


def _catalog() -> dict:
    return yaml.safe_load(_CATALOG.read_text(encoding="utf-8")) or {}


def _entry(prefix: str) -> dict:
    for v in _catalog().get("vocabularies", []):
        if str(v.get("prefix")) == prefix:
            return v
    raise SystemExit(f"unknown vocabulary prefix: {prefix}")


def _yaml_scalar(text: str) -> str:
    """flow マッピングの中に置ける 1 行のスカラ表記（必要なときだけ引用する）。"""
    dumped = yaml.safe_dump(text, allow_unicode=True, default_flow_style=True).strip()
    return dumped.removesuffix("...").strip()


def _append_terms(prefix: str, terms: list[dict]) -> int:
    """該当語彙の ``terms:`` の末尾に追記する（コメントを壊さない表面書き換え）。"""
    lines = _CATALOG.read_text(encoding="utf-8").splitlines(keepends=True)
    start = next(
        (i for i, ln in enumerate(lines) if ln.strip() == f"- prefix: {prefix}"),
        -1,
    )
    if start < 0:
        raise SystemExit(f"cannot locate the block for {prefix}")
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("- prefix: ")),
        len(lines),
    )
    terms_at = next(
        (i for i in range(start, end) if lines[i].strip() in ("terms:", "terms: []")),
        -1,
    )
    if terms_at < 0:
        raise SystemExit(f"{prefix} has no `terms:` block")
    # まだ語が 1 つも無い語彙は `terms: []`（インラインの空リスト）で書かれている。
    # 下にぶら下げるためにブロック形式へ開く。
    if lines[terms_at].strip() == "terms: []":
        lines[terms_at] = lines[terms_at].replace("terms: []", "terms:")
    # 既存の語がある場合はその最後の行の直後、無ければ `terms:` の直後に入れる。
    # ⭐後者を「ブロックの終わり」にすると、**次の節の見出しコメントの後ろ**に
    # 潜り込む（実測: emmo の初回取り込みで「Generic / cross-domain」の見出しの
    # 下に材料語彙の語が並んだ）。
    last_item = max(
        (i for i in range(terms_at + 1, end) if lines[i].lstrip().startswith("- ")),
        default=-1,
    )
    insert_at = last_item + 1 if last_item >= 0 else terms_at + 1
    block = [
        (
            f"      - {{ name: {t['name']}, kind: {t['kind']}, "
            f"label: {_yaml_scalar(t['label'])}"
            + (f", iri: {t['iri']}" if t.get("iri") else "")
            + " }\n"
        )
        for t in terms
    ]
    lines[insert_at:insert_at] = block
    _CATALOG.write_text("".join(lines), encoding="utf-8")
    return len(block)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prefix", help="対象の語彙 prefix（省略時は --verify で全語彙）")
    ap.add_argument("--source", help="RDF の URL（省略時はカタログの source）")
    ap.add_argument("--verify", action="store_true", help="収録済みの語が実ファイルに在るか確かめる")
    ap.add_argument("--add", default="", help="追記する語の name（カンマ区切り）")
    ap.add_argument("--write", action="store_true", help="--add を実際に書き込む")
    ap.add_argument(
        "--label-names",
        action="store_true",
        help="不透明 IRI の語彙: 照合名に skos:prefLabel を使い、iri を明示して書く",
    )
    ap.add_argument(
        "--exclude",
        default="",
        help="この正規表現に当たる名前は採らない（機械的な分類の除外用）",
    )
    ap.add_argument(
        "--exclude-under",
        default="",
        help=(
            "この名前の下位クラスを採らない（カンマ区切り）。名前の形ではなく"
            "オントロジー自身の階層で外すので、'Ampere' のような語も正しく落ちる"
        ),
    )
    args = ap.parse_args()

    prefixes = [args.prefix] if args.prefix else [v["prefix"] for v in _catalog()["vocabularies"]]
    exit_code = 0
    for prefix in prefixes:
        entry = _entry(prefix)
        source = args.source or str(entry.get("source") or "")
        namespace = str(entry["namespace"])
        have = {str(t["name"]) for t in (entry.get("terms") or [])}
        if not source:
            print(f"{prefix}: source が無い（取り込み元 URL 未記載）")
            exit_code = 1
            continue
        try:
            g = _parse(_fetch(source), source)
        except Exception as exc:  # noqa: BLE001 — ネットワーク/形式は環境要因
            print(f"{prefix}: 取得/解析できず ({exc})")
            exit_code = 1
            continue
        terms = (
            extract_terms_by_label(g, namespace)
            if args.label_names
            else extract_terms(g, namespace)
        )
        if args.exclude_under:
            roots = {r.strip() for r in args.exclude_under.split(",") if r.strip()}
            by_name = {t["name"]: t for t in terms}
            root_iris = {
                rdflib.URIRef(by_name[r]["iri"]) for r in roots if r in by_name and by_name[r].get("iri")
            }
            if not root_iris:
                print(f"{prefix}: --exclude-under の根が見つからない: {sorted(roots)}")
            else:
                def _under(iri: str, g: rdflib.Graph = g, root_iris: set = root_iris) -> bool:
                    seen: set = set()
                    stack = [rdflib.URIRef(iri)]
                    while stack:
                        node = stack.pop()
                        for parent in g.objects(node, _RDFS.subClassOf):
                            if not isinstance(parent, rdflib.URIRef) or parent in seen:
                                continue
                            if parent in root_iris:
                                return True
                            seen.add(parent)
                            stack.append(parent)
                    return False

                before = len(terms)
                terms = [t for t in terms if not (t.get("iri") and _under(t["iri"]))]
                print(f"{prefix}: 除外 {before - len(terms)} 語（{', '.join(sorted(roots))} の下位）")
        if args.exclude:
            drop = re.compile(args.exclude)
            before = len(terms)
            terms = [t for t in terms if not drop.search(t["name"])]
            print(f"{prefix}: 除外 {before - len(terms)} 語（--exclude {args.exclude}）")
        names = {t["name"] for t in terms}
        missing = sorted(have - names)
        new = [t for t in terms if t["name"] not in have]
        print(
            f"{prefix}: 実ファイル {len(terms)} 語 / 収録済み {len(have)} 語 "
            f"/ 未収録 {len(new)} 語 / 実ファイルに無い収録語 {len(missing)} 語"
        )
        if missing:
            print(f"   ⚠ 実ファイルに見つからない: {', '.join(missing)}")
            if not terms:
                print("   （0 語＝この語彙は iri == namespace + name の形ではない可能性）")
            exit_code = 1
        if args.add:
            if args.add.strip() == "all":
                # その語彙**まるごと**。すでに収録を決めた語彙の全語を入れるのは
                # 「LOV を丸写しにしない」という方針と両立する（語彙を選ぶのが
                # curation で、選んだ標準の中を間引くのは別の話）。
                picked = [t for t in terms if t["name"] not in have]
            else:
                wanted = [w.strip() for w in args.add.split(",") if w.strip()]
                unknown = [w for w in wanted if w not in names]
                if unknown:
                    raise SystemExit(f"実ファイルに無い語は追記できない: {', '.join(unknown)}")
                picked = [t for t in terms if t["name"] in wanted and t["name"] not in have]
            if not picked:
                print("   追記するものはない（すべて収録済み）")
            elif args.write:
                print(f"   追記 {_append_terms(prefix, picked)} 語 -> {_CATALOG}")
                print(f"   ※ 語彙の `retrieved` を {_dt.datetime.now(_dt.UTC).date().isoformat()} に手で更新すること")
            else:
                for t in picked:
                    print(f"   + {{ name: {t['name']}, kind: {t['kind']}, label: {t['label']} }}")
                print("   （--write で実際に追記）")
        elif new and args.prefix:
            for t in new[:40]:
                print(f"   - {t['name']} ({t['kind']}) {t['label']}")
            if len(new) > 40:
                print(f"   …ほか {len(new) - 40} 語")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
