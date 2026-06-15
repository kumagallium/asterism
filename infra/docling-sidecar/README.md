# Docling sidecar — PDF → structure (the isolated ML step)

Asterism keeps the ingest path deterministic and **ML-free** (CLAUDE.md
「生成コードを実行しない／runtime に ML を入れない」). Born-digital **PDF** needs a
layout-aware ML model (Docling, IBM) to recover its structure, so that heavy dependency
lives here, in a **separate service** — never in the api image.

This service is deliberately thin:

```
GET  /health   -> {"status":"ok","converter":"docling/<ver> (...; ocr=off)"}
POST /convert  -> {"docling_doc": {...DoclingDocument.export_to_dict()...},
                   "converter": "docling/<ver> (...)"}
```

`/convert` runs Docling on an uploaded **born-digital** PDF (OCR **off** — deterministic
given pinned model versions) and returns the **raw** `DoclingDocument` dict. It does NOT
produce JATS: the deterministic, vetted `DoclingDocument → JATS` adapter
(`asterism.documents.docling_dict_to_jats`) runs in the trusted api/ingest runtime, where
it is unit-tested with a committed fixture and needs no torch. **ML here (isolated); the
deterministic transform in-repo (vetted).**

## How the api uses it

The api calls `POST /convert` over the closed network, set by `ASTERISM_DOCLING_URL`
(e.g. `http://docling:8090`). If that env is unset or the service is unreachable, PDF
ingest fails with a clear 4xx — exactly like the optional pandoc Word converter — and
CSV/JSON/XML/Word are unaffected. The conversion is recorded downstream as a
`lit:DocumentConversionActivity` (converter + version): a disclosed, version-pinned
provenance claim (vs RAG hiding the conversion).

## Run

Docker (self-contained build context = this directory):

```bash
docker build -f infra/docling-sidecar/Dockerfile -t asterism-docling-sidecar infra/docling-sidecar
docker run --rm -p 8090:8090 asterism-docling-sidecar
# then point the api at it:  ASTERISM_DOCLING_URL=http://localhost:8090
```

Local (dev), in a venv with `pip install -r requirements.txt`:

```bash
uvicorn app:app --host 0.0.0.0 --port 8090 --workers 1
```

## Spec / sizing

- **GPU not required** (born-digital, OCR off). CPU inference takes ~tens of seconds per
  paper (10–20 pp).
- **RAM**: one conversion holds the layout/table models in memory; concurrency is pinned
  to **1 at a time** (`app.py`), so peak RAM is bounded — ~4 GiB is enough, 8 GiB
  comfortable, regardless of request load.
- **Disk/image**: torch (CPU) + Docling + baked models ≈ 2.5–4 GiB.
- Because conversion is consumed by the api's **async ingest job** (SSE progress), spec
  affects how *fast* a PDF converts, not *whether* it works — a slow box just shows a
  longer progress bar.

## Scope

Born-digital PDFs only (OCR off). Scanned PDFs (OCR) and table-grid → `doco:Table` are
follow-ups — see `docs/architecture/pdf-docling-conversion.md`.
