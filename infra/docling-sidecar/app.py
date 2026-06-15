"""Docling PDF→structure sidecar — the ONE place the document layer runs ML.

Asterism's core discipline (CLAUDE.md「生成コードを実行しない／runtime に ML を入れない」)
keeps the ingest path deterministic. PDF needs layout-aware ML (Docling) to recover
structure, so that ML is isolated *here*, in a separate service, OUT of the api image.

This service is deliberately thin: it runs Docling on an uploaded born-digital PDF
(OCR OFF — deterministic given pinned model versions) and returns the RAW
``DoclingDocument`` as a dict (``export_to_dict``) plus the converter version. It does
NOT import asterism and does NOT produce JATS — the deterministic, vetted
``DoclingDocument→JATS`` adapter (``asterism.documents.docling_dict_to_jats``) runs in
the trusted api/ingest runtime, where it is unit-tested with a committed fixture and
needs no torch. So: ML here (isolated), the deterministic transform in-repo (vetted).

The api calls ``POST /convert`` over the closed network (``ASTERISM_DOCLING_URL``) and
degrades with a clear 4xx if this service is absent — exactly like the optional pandoc
Word converter. The conversion is recorded as a ``lit:DocumentConversionActivity``
(converter + version) downstream — a disclosed, version-pinned provenance claim.

Run:  uvicorn app:app --host 0.0.0.0 --port 8090   (single worker — see /convert)
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from contextlib import suppress
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

# Born-digital PDFs only (OCR off). A generous cap; a PDF is far denser than prose.
_MAX_PDF_BYTES = int(os.environ.get("DOCLING_MAX_PDF_BYTES", str(64 * 1024 * 1024)))
# Per-conversion wall-clock cap (Docling layout+table inference on CPU is the slow part).
_CONVERT_TIMEOUT_S = float(os.environ.get("DOCLING_CONVERT_TIMEOUT_S", "600"))

app = FastAPI(title="asterism docling sidecar")

# Bound concurrency to ONE conversion at a time so peak RAM (model + inference) is
# fixed regardless of request load — the lever that keeps the spec requirement small.
_gate = asyncio.Semaphore(1)
# The DocumentConverter (and the torch models it loads) is built once, lazily, so the
# module imports without torch (light smoke/lint) and the first request warms it.
_converter = None
_converter_lock = asyncio.Lock()


def _converter_version() -> str:
    """A disclosable, version-pinned converter id: ``docling/<ver> (...; ocr=off)``."""
    from importlib.metadata import PackageNotFoundError, version

    parts = []
    for pkg in ("docling", "docling-core", "docling-ibm-models"):
        with suppress(PackageNotFoundError):
            parts.append(f"{pkg}/{version(pkg)}")
    head = parts[0] if parts else "docling"
    rest = "; ".join(parts[1:] + ["ocr=off"])
    return f"{head} ({rest})" if rest else head


def _build_converter():
    """Construct a Docling converter for born-digital PDFs (OCR off, tables on)."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False  # born-digital only — deterministic; OCR is a later toggle
    opts.do_table_structure = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )


async def _get_converter():
    global _converter
    if _converter is None:
        async with _converter_lock:
            if _converter is None:
                _converter = await asyncio.to_thread(_build_converter)
    return _converter


def _convert_sync(converter, data: bytes) -> dict:
    """Blocking Docling conversion of ``data`` (PDF bytes) → ``export_to_dict``."""
    # Docling reads a path or a DocumentStream; a temp file is the most robust input.
    with tempfile.TemporaryDirectory(prefix="docling-") as tmp:
        pdf = Path(tmp) / "upload.pdf"
        pdf.write_bytes(data)
        result = converter.convert(str(pdf))
        return result.document.export_to_dict()


@app.get("/health")
async def health() -> JSONResponse:
    # Does not load torch — just reports the pinned converter id so the api can show it.
    return JSONResponse({"status": "ok", "converter": _converter_version()})


@app.post("/convert")
async def convert(request: Request) -> JSONResponse:
    """Convert an uploaded born-digital PDF to a raw ``DoclingDocument`` dict.

    Body: the PDF bytes (``application/pdf`` or raw octet-stream). Returns
    ``{"docling_doc": {...}, "converter": "docling/<ver> (...)"}``. One conversion runs
    at a time (peak RAM is bounded); a request waits its turn behind the semaphore.
    """
    data = await request.body()
    if not data:
        raise HTTPException(400, "empty body — POST the PDF bytes")
    if len(data) > _MAX_PDF_BYTES:
        raise HTTPException(413, f"PDF exceeds the {_MAX_PDF_BYTES >> 20} MiB limit")
    if data[:5] != b"%PDF-":
        raise HTTPException(415, "not a PDF (missing %PDF- header)")
    converter = await _get_converter()
    async with _gate:
        try:
            doc = await asyncio.wait_for(
                asyncio.to_thread(_convert_sync, converter, data), timeout=_CONVERT_TIMEOUT_S
            )
        except TimeoutError as exc:
            raise HTTPException(504, "PDF conversion timed out") from exc
        except Exception as exc:  # surface a clean failure to the api caller
            raise HTTPException(422, f"Docling could not convert the PDF: {exc}") from exc
    return JSONResponse({"docling_doc": doc, "converter": _converter_version()})
