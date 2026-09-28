"""「データを置く」画面の api（契約メモ contract_pr_c.md §4。担当 C1-place）。

``register_place(app, cfg)`` が 3 つのルートを ``app`` に直接足す（§0.1 の型）:

* ``POST /api/place/inspect``  — 置こうとしている表の形を読み、既知の型と突き合わせる。
* ``POST /api/place/subjects`` — 型が決まった表の、行ごとの照合状態（3 状態）。
* ``POST /api/place/commit``   — 「ページを並べる」— 設計なしで新しいデータセットとして
  置く。既存の materialize → ingest → promote の経路を順に呼ぶだけ（LLM は呼ばない・
  生成コードは実行しない）。

置くだけで「棚に無い形」だった場合の「棚を作る」導線は ui 側の責務（§4.3 の最後の段落
の通り、api 側には何も要らない）。

Deviation from the contract's literal wording (see this PR's ``notes``): §0.1 says
to "import ``main.py`` の ``require_write_auth``". That function is a closure
defined *inside* ``build_app`` (not a module-level name), so it cannot literally
be imported — and even if it could, importing anything *from* ``main.py`` here
would create a circular import once ``main.py`` imports ``register_place`` from
this module (same deviation ``cards_routes.py`` documents). Instead,
:func:`register_place`'s inner ``_require_write_auth`` reproduces the exact same
fail-closed check (same messages, same constant-time comparison via the already
importable ``_write_credential_ok``) against the ``cfg`` this module already
receives.
"""

from __future__ import annotations

import csv
import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asterism.class_schema import class_schema
from asterism.mapping_ir_read import MappingIRReadError
from asterism.rml_validate import RmlValidationError, read_csv_header, validate_rml_design
from asterism.shape_match import (
    MissingKeyColumnsError,
    TypeSignature,
    match_shape,
    match_subjects,
    prune_mapping_ir_yaml,
    type_signatures,
)

# 列名の正規化（place/subjects の列特定にも使う。同名関数を re-export していない
# ので private のまま直接 import する — 同じ C1-place が書いた 2 ファイルの間）。
from asterism.shape_match import _normalize as _norm_col
from asterism.subjects import normalize_set_spec, set_id_of
from asterism_step0.dialect import (
    TABULAR_SUFFIXES,
    describe_dialect,
    detect_dialect,
    dialect_ir_fields,
    is_default,
)
from asterism_step0.inspect import inspect_source_set
from asterism_step0.mapping_ir import MappingIRParseError, parse_mapping_ir
from asterism_step0.rml_compile import RmlCompileError, compile_mapping_ir
from asterism_step0.spec_yaml import dump_spec_yaml, load_spec_yaml
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from asterism_api import registry, staging
from asterism_api.main import Settings, _write_credential_ok

__all__ = ["register_place"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# リクエスト body
# ---------------------------------------------------------------------------


class PlaceInspectBody(BaseModel):
    staging_id: str | None = None
    dataset_id: str | None = None


class PlaceSubjectsBody(BaseModel):
    staging_id: str | None = None
    dataset_id: str | None = None
    type_id: str


class PlaceCommitBody(BaseModel):
    staging_id: str
    type_id: str
    choices: dict[str, str | None] = Field(default_factory=dict)
    name: str


# ---------------------------------------------------------------------------
# ソース解決・読み取りの小さな道具
# ---------------------------------------------------------------------------


def _resolve_place_source(
    registry_root: Path, staging_id: str | None, dataset_id: str | None
) -> list[Path]:
    if staging_id:
        try:
            _sdir, paths = staging.load(registry_root, staging_id)
        except staging.StagingNotFound as exc:
            raise HTTPException(404, f"staging {staging_id!r} not found (expired?)") from exc
        return paths
    if dataset_id:
        paths = registry.list_source_files(registry_root, dataset_id)
        if not paths:
            raise HTTPException(404, f"dataset {dataset_id!r} has no source files")
        return paths
    raise HTTPException(400, "staging_id or dataset_id is required")


def _first_tabular(paths: list[Path]) -> Path:
    tabular = [p for p in paths if p.suffix.lower() in TABULAR_SUFFIXES]
    if not tabular:
        raise HTTPException(400, "置ける表(CSV など)が見つかりません")
    return tabular[0]


def _dialect_text(path: Path) -> str:
    try:
        return describe_dialect(detect_dialect(path))
    except OSError:
        return "default"


def _uploaded_column_for(target_column: str, uploaded_columns: list[str]) -> str | None:
    target_norm = _norm_col(target_column)
    return next((c for c in uploaded_columns if _norm_col(c) == target_norm), None)


def _read_column_values(path: Path, column: str) -> list[str]:
    """CSV の 1 列を頭から読む（BOM 耐性の utf-8-sig）。列名は正規化して探す —
    アップロードされた表の綴りが signature 側の列名と完全一致するとは限らない。"""
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = reader.fieldnames or []
            actual = _uploaded_column_for(column, list(fieldnames))
            if actual is None:
                return []
            return [str(row.get(actual) or "").strip() for row in reader]
    except OSError:
        return []


async def _signature_label(client: Any, registry_root: Path, class_iri: str) -> str | None:
    try:
        schema = await class_schema(client, registry_root, class_iri)
    except Exception:  # ラベルは付加情報 — 取得に失敗しても本体は止めない
        return None
    return schema.get("label") if schema else None


def _find_signature(registry_root: Path, type_id: str) -> TypeSignature:
    signature = next((s for s in type_signatures(registry_root) if s.type_id == type_id), None)
    if signature is None:
        raise HTTPException(404, f"unknown type_id {type_id!r}")
    return signature


# ---------------------------------------------------------------------------
# commit の内部段階 — materialize → ingest → promote（既存の経路を順に呼ぶだけ）
#
# 個別にテストで monkeypatch できるよう、3 つを別々のモジュール関数にしている
# （テストは呼び順と meta.origin=own だけを固定し、実 ingest は既存 e2e に任せる
# — 契約メモ §4.4）。
# ---------------------------------------------------------------------------


def _materialize_own(
    cfg: Settings,
    *,
    dataset_name: str,
    signature: TypeSignature,
    source_path: Path,
    source_name: str,
    columns: list[str],
) -> dict[str, Any]:
    """signature の元データセットの mapping.yaml/model.yaml/mapping.rml.ttl を
    コピーし、対象 TriplesMap の source をアップロードされたファイル名に差し替えて
    新しいデータセットとして保存する（materialize の内部処理そのもの — LLM は
    呼ばない・生成コードは実行しない・compile_mapping_ir/registry.save_dataset を
    直接使う）。戻り値は保存された meta（``id`` に新しい dataset_id）。

    コピーの前に ``prune_mapping_ir_yaml`` で、置いたファイルに無い列を参照する
    property 行を刈り込む（O5: 部分一致は一致した列だけで置く）。刈り込まずに
    コピーすると、ファイルに無い列を参照した RML が ingest 時の RML design
    validation で 400/422 になり、さらに空のデータセットが registry に残っていた
    （この関数が直していたバグ）。ID の列（subject template）が無ければ
    :class:`MissingKeyColumnsError` を 400 に変換する。刈り込んだあとも
    ``validate_rml_design`` を通してから ``registry.save_dataset`` する
    （検証が通ってからデータセットを作る）。
    """
    src = registry.load_dataset(cfg.registry_root, signature.dataset_id)
    if src is None:
        raise HTTPException(404, f"signature dataset {signature.dataset_id!r} not found")
    mapping_text = str(src.get("artifacts", {}).get("mapping.yaml") or "")
    try:
        pruned_text = prune_mapping_ir_yaml(mapping_text, signature.type_id, columns)
    except MissingKeyColumnsError as exc:
        raise HTTPException(
            400,
            "この形では ID の列が足りません: " + ", ".join(exc.columns),
        ) from exc
    except MappingIRReadError as exc:
        raise HTTPException(500, f"signature mapping.yaml is unreadable: {exc}") from exc

    try:
        doc = load_spec_yaml(pruned_text)
    except Exception as exc:  # YAML が壊れていれば 500 で理由を返す
        raise HTTPException(500, f"pruned mapping.yaml is unreadable: {exc}") from exc
    if not isinstance(doc, dict) or not (doc.get("maps") or [None])[0]:
        raise HTTPException(500, "pruned mapping.yaml has no map")
    target = doc["maps"][0]

    new_map = dict(target)
    old_source = str(new_map.get("source") or "")
    new_map["source"] = source_name

    dialects = dict(doc.get("dialects") or {})
    dialects.pop(old_source, None)
    try:
        detected = detect_dialect(source_path)
        if not is_default(detected):
            dialects[source_name] = dialect_ir_fields(detected)
        else:
            dialects.pop(source_name, None)
    except OSError:
        pass

    new_doc: dict[str, Any] = {
        "version": doc.get("version", 1),
        "prefixes": doc.get("prefixes") or {},
        "maps": [new_map],
    }
    if dialects:
        new_doc["dialects"] = dialects
    mapping_yaml_text = dump_spec_yaml(new_doc)

    try:
        ir = parse_mapping_ir(mapping_yaml_text)
        rml_ttl = compile_mapping_ir(ir)
    except MappingIRParseError as exc:
        issues = "; ".join(exc.args[0]) if exc.args else str(exc)
        raise HTTPException(500, f"could not compile the copied mapping: {issues}") from exc
    except RmlCompileError as exc:
        raise HTTPException(500, f"could not compile the copied mapping: {exc}") from exc

    # 検証が通ってからデータセットを作る（§4.4「失敗時に registry を汚さない」）。
    # ``source_path`` は staging のファイルそのもの（ファイル名は既に
    # ``source_name``）— コピー前だが rml:source の解決先として同じものを指せる。
    try:
        validate_rml_design(rml_ttl, source_path.parent)
    except RmlValidationError as exc:
        raise HTTPException(
            400, {"error": "RML design validation failed", "issues": exc.issues}
        ) from exc

    artifacts = {
        "diagram.md": str(src.get("artifacts", {}).get("diagram.md") or ""),
        "model.yaml": str(src.get("artifacts", {}).get("model.yaml") or ""),
        "mie.yaml": str(src.get("artifacts", {}).get("mie.yaml") or ""),
        "mapping.rml.ttl": rml_ttl,
        "mapping.yaml": mapping_yaml_text,
    }
    return registry.save_dataset(
        cfg.registry_root,
        dataset_name,
        artifacts,
        complete=True,
        warnings=[],
        traps=[],
        exit_code=0,
        created_at=datetime.now(UTC).isoformat(),
        proposal_md="",
    )


def _find_endpoint(app: FastAPI, path: str, method: str):
    for route in app.routes:
        methods = getattr(route, "methods", None) or ()
        if getattr(route, "path", None) == path and method in methods:
            return route.endpoint
    raise HTTPException(500, f"internal route {path} not registered")


async def _ingest_own(app: FastAPI, dataset_id: str) -> dict[str, Any]:
    """既存の ``POST /api/datasets/{id}/ingest`` の経路を ASGI を経由せず直接呼ぶ
    （認証はこの commit 自身の write-auth で済んでいる）。バックグラウンドジョブ
    として起動されるので、そのジョブの完了まで待ってから返す — commit は
    materialize→ingest→promote を順番に、同期的に終わらせる契約のため。
    """
    endpoint = _find_endpoint(app, "/api/datasets/{dataset_id}/ingest", "POST")
    response = await endpoint(dataset_id=dataset_id, files=[])
    body: dict[str, Any] = json.loads(bytes(response.body))
    job_id = body.get("job_id")
    jobs = getattr(app.state, "jobs", None)
    job = jobs.get(job_id) if jobs is not None and job_id else None
    if job is not None and getattr(job, "task", None) is not None:
        await job.task
        job = jobs.get(job_id)
    if job is not None and getattr(job, "status", None) == "error":
        raise HTTPException(502, f"ingest failed: {job.error}")
    return body


async def _promote_own(app: FastAPI, dataset_id: str) -> dict[str, Any]:
    """既存の ``POST /api/datasets/{id}/promote`` の経路を直接呼ぶ。"""
    endpoint = _find_endpoint(app, "/api/datasets/{dataset_id}/promote", "POST")
    response = await endpoint(dataset_id=dataset_id)
    return json.loads(bytes(response.body))


def _fill_key_template(template: str, column: str, value: str) -> str | None:
    slot = "{" + column + "}"
    if slot not in template:
        return None
    return template.replace(slot, value)


# ---------------------------------------------------------------------------
# register_place
# ---------------------------------------------------------------------------


def register_place(app: FastAPI, cfg: Settings) -> None:
    def _require_write_auth(
        authorization: str | None = Header(default=None),
        x_asterism_token: str | None = Header(default=None),
    ) -> None:
        if not cfg.api_token:
            raise HTTPException(
                503,
                "利用許可コード (管理者が設定する API token) が未設定のため、"
                "この操作はできません",
            )
        if not _write_credential_ok(cfg, authorization, x_asterism_token):
            raise HTTPException(401, "利用許可コードが違います")

    _write_auth = [Depends(_require_write_auth)]

    @app.post("/api/place/inspect", dependencies=_write_auth)
    async def place_inspect(body: PlaceInspectBody) -> JSONResponse:
        paths = _resolve_place_source(cfg.registry_root, body.staging_id, body.dataset_id)
        inspections, _fks = inspect_source_set(paths, fk_hint_columns=None)
        files = [
            {"name": insp.name, "columns": [c.name for c in insp.columns], "rows": insp.total_rows}
            for insp in inspections
        ]
        candidates = type_signatures(cfg.registry_root)
        primary_columns = files[0]["columns"] if files else []
        match = match_shape(primary_columns, candidates)

        dialect_text = "default"
        tabular = [p for p in paths if p.suffix.lower() in TABULAR_SUFFIXES]
        if tabular:
            dialect_text = _dialect_text(tabular[0])

        signature_label: str | None = None
        if match.type_id is not None:
            signature_label = await _signature_label(
                app.state.client, cfg.registry_root, match.type_id
            )

        return JSONResponse(
            {
                "files": files,
                "match": {
                    "type_id": match.type_id,
                    "dialect": dialect_text,
                    "matched_columns": list(match.matched_columns),
                    "unmatched_columns": list(match.unmatched_columns),
                    "confidence": match.confidence,
                },
                "signature_label": signature_label,
                "signature_dataset_id": match.dataset_id,
            }
        )

    @app.post("/api/place/subjects", dependencies=_write_auth)
    async def place_subjects(body: PlaceSubjectsBody) -> JSONResponse:
        paths = _resolve_place_source(cfg.registry_root, body.staging_id, body.dataset_id)
        source_path = _first_tabular(paths)
        signature = _find_signature(cfg.registry_root, body.type_id)
        if not signature.key_columns:
            raise HTTPException(400, "signature has no key column to match on")
        key_col = signature.key_columns[0]

        raw_values = _read_column_values(source_path, key_col)
        counts: dict[str, int] = {}
        order: list[str] = []
        for v in raw_values:
            if not v:
                continue
            if v not in counts:
                order.append(v)
            counts[v] = counts.get(v, 0) + 1
        dedup_values = order[:500]

        matched = await match_subjects(app.state.client, signature, dedup_values)
        by_value = {m["value"]: m for m in matched}

        items: list[dict[str, Any]] = []
        for value in dedup_values:
            row = by_value.get(value, {"match": "own_only", "iri": None, "candidates": []})
            entry: dict[str, Any] = {
                "value": value,
                "rows": counts.get(value, 0),
                "match": row.get("match", "own_only"),
            }
            if row.get("iri"):
                entry["iri"] = row["iri"]
            if row.get("candidates"):
                entry["candidates"] = row["candidates"]
            items.append(entry)

        return JSONResponse({"items": items})

    @app.post("/api/place/commit", dependencies=_write_auth)
    async def place_commit(body: PlaceCommitBody) -> JSONResponse:
        try:
            _sdir, paths = staging.load(cfg.registry_root, body.staging_id)
        except staging.StagingNotFound as exc:
            raise HTTPException(404, f"staging {body.staging_id!r} not found (expired?)") from exc
        source_path = _first_tabular(paths)
        signature = _find_signature(cfg.registry_root, body.type_id)
        key_col = signature.key_columns[0] if signature.key_columns else None

        raw_values = _read_column_values(source_path, key_col) if key_col else []
        dedup_values = list(dict.fromkeys(v for v in raw_values if v))[:500]

        matched = await match_subjects(app.state.client, signature, dedup_values) if key_col else []
        by_value = {m["value"]: m for m in matched}

        # 置いたファイルの列 — signature の刈り込み（O5）と pruned_columns の
        # 両方の根拠。BOM 耐性の読み手（``_read_column_values`` と同じ流儀）。
        uploaded_columns = read_csv_header(source_path)
        uploaded_norms = {_norm_col(c) for c in uploaded_columns} - {""}
        pruned_columns = [
            cs.column for cs in signature.columns if _norm_col(cs.column) not in uploaded_norms
        ]

        # ① materialize — signature のコピーを、ファイルに無い列を刈り込んで
        # 新しいデータセットとして保存する（検証済みの RML のみ保存する）。
        meta = _materialize_own(
            cfg,
            dataset_name=body.name,
            signature=signature,
            source_path=source_path,
            source_name=source_path.name,
            columns=uploaded_columns,
        )
        dataset_id = str(meta["id"])
        try:
            dest_source_dir = registry.source_dir(cfg.registry_root, dataset_id)
            if dest_source_dir is None:
                raise HTTPException(500, "could not resolve the new dataset's source directory")
            dest_source_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, dest_source_dir / source_path.name)
            registry.mark_source_saved(
                cfg.registry_root, dataset_id, [source_path.name], conversion=None
            )

            # ② ingest（既存 job）
            ingest_result = await _ingest_own(app, dataset_id)

            # ③ promote（既存）
            await _promote_own(app, dataset_id)
        except Exception:
            # 失敗時に registry を汚さない — materialize は検証済みだが、ingest/
            # promote がここで失敗すれば作りかけのデータセットを消してから
            # そのまま元の例外を伝える（呼び出し元に 400/422/502 をそのまま返す）。
            # delete 自体が例外を投げても、元の例外（呼び出し元が見るべき
            # 本当の失敗理由）を握りつぶしてはいけない（checker finding: 素の
            # `except Exception: ...; raise` は delete 失敗時にここで新しい
            # 例外に置き換わってしまい、元の 400/422/502 の手がかりが消える）。
            try:
                registry.delete_dataset(cfg.registry_root, dataset_id)
            except Exception:
                logger.warning(
                    "place: failed to clean up dataset %s after ingest/promote failure",
                    dataset_id,
                    exc_info=True,
                )
            raise

        # ④ meta.origin = 'own' を書く
        registry._update_meta(cfg.registry_root, dataset_id, {"origin": "own"})

        class_label = await _signature_label(app.state.client, cfg.registry_root, signature.type_id)
        now = datetime.now(UTC).isoformat()

        subjects: list[dict[str, Any]] = []
        for value in dedup_values:
            row = by_value.get(value) or {"match": "own_only", "iri": None}
            state = str(row.get("match", "own_only"))
            iri = row.get("iri")
            if value in body.choices:
                choice = body.choices[value]
                if choice:
                    state, iri = "linked", choice
                else:
                    state, iri = "own_only", None
            if state != "linked" or not iri:
                iri = _fill_key_template(signature.template, key_col, value) if key_col else None
            subjects.append(
                {
                    "kind": "individual",
                    "id": iri,
                    "label": value,
                    "class_label": class_label,
                    "source": "own",
                    "card_count": None,
                    "match": state,
                    "subject_key": f"i:{iri}" if iri else "",
                    "created_at": now,
                }
            )

        spec = normalize_set_spec(
            {
                "class": signature.type_id,
                "where": [],
                "order_by": None,
                "limit": 20,
                "source_scope": "own",
            }
        )
        set_id = set_id_of(spec)

        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "job_id": str(ingest_result.get("job_id") or ""),
                "subjects": subjects,
                "set": {"set_id": set_id, "spec": spec},
                "pruned_columns": pruned_columns,
            }
        )
