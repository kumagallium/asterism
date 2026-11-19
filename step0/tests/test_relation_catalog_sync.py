"""step0 の関係の写しが、ingest の目録（``asterism.relation_catalog``）と一致する。

step0 は ingest を import しない（写しを持つ）。ingest を入れた環境（CI は入れる）で、
目録から導いた値と突き合わせる。入っていなければ skip。
"""

from __future__ import annotations

import pytest

from asterism_step0 import pair_propose, paper_propose, theme_propose

rc = pytest.importorskip("asterism.relation_catalog")


def test_step0_relation_copies_match_the_catalog() -> None:
    without_quantity = tuple(r for r in rc.relation_kinds() if r != "quantity")
    without_eq = tuple(r for r in without_quantity if r != "eq")
    assert len(rc.relation_kinds()) == 12 and len(without_eq) == 10  # 空振りでない
    assert tuple(theme_propose.RELATIONS) == rc.relation_kinds()
    assert tuple(pair_propose.PAIR_RELS) == without_quantity
    assert tuple(pair_propose.SYMMETRIC_RELS) == rc.symmetric_pair_relations()
    # PAPER_RELS は並びが違う（part が先頭）。値の集合で見る
    assert set(paper_propose.PAPER_RELS) == set(without_eq)
    assert len(paper_propose.PAPER_RELS) == len(without_eq)
