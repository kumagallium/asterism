"""Tests for asterism.licenses (契約メモ contract_pr_d.md §1・担当 D1-license).

決定論・LLM ゼロ・store アクセスなしの純粋関数（正規化・再配布可否）と、
ファイルシステムだけを読む ``dataset_license`` を検証する。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from asterism import licenses as lic
from asterism import metadata as m

# ----------------------------------------------------------------------------
# normalize_license
# ----------------------------------------------------------------------------


def test_normalize_license_none_and_blank() -> None:
    assert lic.normalize_license(None) is None
    assert lic.normalize_license("") is None
    assert lic.normalize_license("   ") is None


def test_normalize_license_known_spdx_id_passthrough() -> None:
    assert lic.normalize_license("CC-BY-4.0") == "CC-BY-4.0"
    assert lic.normalize_license("MIT") == "MIT"


def test_normalize_license_case_insensitive_alias() -> None:
    assert lic.normalize_license("cc-by-4.0") == "CC-BY-4.0"
    assert lic.normalize_license("mit") == "MIT"


@pytest.mark.parametrize(
    "url",
    [
        "https://creativecommons.org/licenses/by/4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        "http://creativecommons.org/licenses/by/4.0/",
        "https://www.creativecommons.org/licenses/by/4.0/",
    ],
)
def test_normalize_license_known_url_variants(url: str) -> None:
    assert lic.normalize_license(url) == "CC-BY-4.0"


def test_normalize_license_unknown_spdx_like_string_passthrough() -> None:
    """未知は素通し（元の文字列のまま） - None に落とさない。"""
    assert lic.normalize_license("Some-Weird-License-1.0") == "Some-Weird-License-1.0"


def test_normalize_license_unknown_url_passthrough() -> None:
    unknown = "https://example.org/licenses/not-a-real-one"
    assert lic.normalize_license(unknown) == unknown


# ----------------------------------------------------------------------------
# redistributable
# ----------------------------------------------------------------------------


def test_redistributable_true_for_permissive_licenses() -> None:
    assert lic.redistributable("CC-BY-4.0") is True
    assert lic.redistributable("CC0-1.0") is True
    assert lic.redistributable("Apache-2.0") is True


def test_redistributable_false_for_nc_nd_and_proprietary() -> None:
    assert lic.redistributable("CC-BY-NC-4.0") is False
    assert lic.redistributable("CC-BY-ND-4.0") is False
    assert lic.redistributable("proprietary") is False


def test_redistributable_none_for_unknown_empty_or_none() -> None:
    assert lic.redistributable(None) is None
    assert lic.redistributable("") is None
    assert lic.redistributable("Some-Weird-License-1.0") is None


def test_redistributable_resolves_url_and_alias_through_known_table() -> None:
    assert lic.redistributable("https://creativecommons.org/licenses/by-nc/4.0/") is False
    assert lic.redistributable("cc-by-sa-4.0") is True


# ----------------------------------------------------------------------------
# dataset_license — reads the store's metadata.ttl (source of truth), falling
# back to mie.yaml, falling back to a bundled dataset's dataset.toml.
# ----------------------------------------------------------------------------


def _write_metadata_ttl(root: Path, dataset_id: str, document: dict) -> None:
    (root / dataset_id).mkdir(parents=True, exist_ok=True)
    graph = m.build_metadata_graph(document, dataset_id)
    (root / dataset_id / "metadata.ttl").write_text(m.metadata_turtle(graph), encoding="utf-8")


def test_dataset_license_reads_from_metadata_ttl(tmp_path: Path) -> None:
    _write_metadata_ttl(tmp_path, "ds-a", {"schema_info": {"license": "CC-BY-4.0"}})
    assert lic.dataset_license(tmp_path, "ds-a") == ("CC-BY-4.0", True)


def test_dataset_license_falls_back_to_mie_yaml_when_no_metadata_ttl(tmp_path: Path) -> None:
    dest = tmp_path / "ds-b"
    dest.mkdir(parents=True)
    (dest / "mie.yaml").write_text(
        "schema_info:\n  license: CC-BY-NC-4.0\n", encoding="utf-8"
    )
    assert lic.dataset_license(tmp_path, "ds-b") == ("CC-BY-NC-4.0", False)


def test_dataset_license_absent_is_unknown(tmp_path: Path) -> None:
    _write_metadata_ttl(tmp_path, "ds-c", {"schema_info": {"title": "no license here"}})
    assert lic.dataset_license(tmp_path, "ds-c") == (None, None)


def test_dataset_license_missing_dataset_dir(tmp_path: Path) -> None:
    assert lic.dataset_license(tmp_path, "does-not-exist") == (None, None)


def test_dataset_license_rejects_unsafe_id(tmp_path: Path) -> None:
    assert lic.dataset_license(tmp_path, "../escape") == (None, None)


def test_dataset_license_bundled_dataset_toml_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同梱データセット（README に明記のあるものだけ dataset.toml に license を
    持つ想定）: registry に何も無ければ ``ASTERISM_DATASETS_ROOT`` の
    ``<id>/dataset.toml`` を読む。"""
    datasets_dir = tmp_path / "datasets"
    (datasets_dir / "bundled-example").mkdir(parents=True)
    (datasets_dir / "bundled-example" / "dataset.toml").write_text(
        'name = "bundled-example"\nlicense = "CC-BY-4.0"\n', encoding="utf-8"
    )
    monkeypatch.setenv("ASTERISM_DATASETS_ROOT", str(datasets_dir))
    registry_root = tmp_path / "registry"
    assert lic.dataset_license(registry_root, "bundled-example") == ("CC-BY-4.0", True)


def test_dataset_license_bundled_dataset_toml_without_license_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """README に明記が無いデータセットは license キー自体が無い -> 不明。"""
    datasets_dir = tmp_path / "datasets"
    (datasets_dir / "no-license-example").mkdir(parents=True)
    (datasets_dir / "no-license-example" / "dataset.toml").write_text(
        'name = "no-license-example"\n', encoding="utf-8"
    )
    monkeypatch.setenv("ASTERISM_DATASETS_ROOT", str(datasets_dir))
    registry_root = tmp_path / "registry"
    assert lic.dataset_license(registry_root, "no-license-example") == (None, None)


def test_dataset_license_metadata_ttl_wins_over_dataset_toml(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """registry（正本）にライセンスがあれば、同梱 dataset.toml は見ない。"""
    datasets_dir = tmp_path / "datasets"
    (datasets_dir / "ds-d").mkdir(parents=True)
    (datasets_dir / "ds-d" / "dataset.toml").write_text(
        'name = "ds-d"\nlicense = "proprietary"\n', encoding="utf-8"
    )
    monkeypatch.setenv("ASTERISM_DATASETS_ROOT", str(datasets_dir))
    registry_root = tmp_path / "registry"
    _write_metadata_ttl(registry_root, "ds-d", {"schema_info": {"license": "MIT"}})
    assert lic.dataset_license(registry_root, "ds-d") == ("MIT", True)
