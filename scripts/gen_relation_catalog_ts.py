#!/usr/bin/env python3
"""関係の目録（ingest の ``relation_catalog``）を ui の組み込みの目録（TypeScript）に書き出す。

    python scripts/gen_relation_catalog_ts.py          # ui/src/relations/builtinCatalog.ts を書き直す
    python scripts/gen_relation_catalog_ts.py --check  # 書き直さず、今のファイルが目録と一致するか見る（CI 用）

一致しなければ終了コード 1。``asterism``（ingest）が import できる環境（api の venv など）で動かす。
design.md §23.13・ADR docs/architecture/relation-catalog.md。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from asterism import relation_catalog

OUT = Path(__file__).resolve().parents[1] / "ui" / "src" / "relations" / "builtinCatalog.ts"

HEADER = """\
// 生成物。手で直さない。作り直しは `python scripts/gen_relation_catalog_ts.py`（目録の正は
// ingest/src/asterism/relation_catalog.py）。形は `GET /api/relations` の返りと同じ。
// 実行時は API の目録がこれに重なる（relations/catalog.ts）。型（関係名・述語名）はここから導く。
"""


def render() -> str:
    body = json.dumps(relation_catalog.catalog_to_json(), ensure_ascii=False, indent=2)
    return f"{HEADER}\nexport const BUILTIN_CATALOG = {body} as const\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--check", action="store_true", help="書き直さず、一致するか見る")
    args = ap.parse_args()
    want = render()
    if args.check:
        have = OUT.read_text(encoding="utf-8") if OUT.is_file() else None
        if have != want:
            print(
                f"{OUT} が関係の目録と一致しない。"
                " `python scripts/gen_relation_catalog_ts.py` で作り直す。",
                file=sys.stderr,
            )
            return 1
        print("ok: builtinCatalog.ts は目録と一致")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(want, encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
