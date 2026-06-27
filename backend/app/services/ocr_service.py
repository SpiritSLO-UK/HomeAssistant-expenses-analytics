"""Local OCR engine (spec §21, §10.4).

Optional and pluggable, like SQLCipher and MQTT: the text-extraction engine is
imported lazily and the whole app runs fine without it. Two backends:

- **images** (PNG/JPG/…): Tesseract via ``pytesseract`` — needs the ``ocr`` extra
  *and* the ``tesseract`` binary on PATH (present in the add-on image, usually
  absent on a bare Windows dev box).
- **PDF**: embedded text via ``pypdf`` — pure-Python, works for digital receipts/
  invoices (scanned PDFs yield little text → low confidence → manual/review).

The image→text step lives here; turning text into fields is ``receipt_parser``
(pure, fully unit-tested without an engine).
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.logging import get_logger

logger = get_logger("app.ocr")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PDF_SUFFIXES = {".pdf"}

# DoS caps for PDF text extraction (CR-SEC-9): a receipt/invoice is a handful of
# pages, so read at most this many and clamp the extracted text so a "text bomb"
# PDF can't blow up memory. (Image decompression bombs are caught by Pillow's
# default Image.MAX_IMAGE_PIXELS guard, which we don't disable.)
_MAX_PDF_PAGES = 200
_MAX_TEXT_CHARS = 5_000_000

# A monetary amount (e.g. 12.34 or 1,234.56). Embedded PDF text is only trusted as a
# real digital statement when it contains one — otherwise a stray text layer on an
# image-only PDF (a page number / watermark) would block the rasterise+OCR fallback.
# The trailing (?!\.\d) rejects a dotted-date fragment like 01.05.2026 ("01.05" → no).
_MONEY_RE = re.compile(r"\d[\d,]*\.\d{2}\b(?!\.\d)")


class OcrUnavailable(RuntimeError):
    """No OCR engine is available for this file type on this install."""


@lru_cache(maxsize=1)
def _tesseract_ok() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401

        # The Python package can import while the binary is missing — check it.
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _pypdf_ok() -> bool:
    try:
        import pypdf  # noqa: F401

        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _pdfium_ok() -> bool:
    """Whether the PDF rasteriser (pypdfium2) is importable — needed to OCR a
    scanned / image-only PDF (alongside the tesseract binary)."""
    try:
        import pypdfium2  # noqa: F401

        return True
    except Exception:
        return False


def available() -> bool:
    return _tesseract_ok() or _pypdf_ok()


def status() -> dict:
    return {
        "available": available(),
        "image_ocr": _tesseract_ok(),  # Tesseract
        "pdf_text": _pypdf_ok(),       # pypdf
        "pdf_ocr": _tesseract_ok() and _pdfium_ok(),  # scanned-PDF OCR
        "image_formats": sorted(s.lstrip(".") for s in IMAGE_SUFFIXES),
    }


def can_handle(filename: str) -> bool:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return _tesseract_ok()
    if suffix in PDF_SUFFIXES:
        return _pypdf_ok()
    return False


def _ocr_image(path: Path) -> tuple[str, float | None]:
    import pytesseract
    from PIL import Image

    image = Image.open(path)
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    words = [w for w in data.get("text", []) if w and w.strip()]
    confs = [int(c) for c in data.get("conf", []) if str(c).lstrip("-").isdigit() and int(c) >= 0]
    text = " ".join(words)
    if not text:
        text = pytesseract.image_to_string(image)
    confidence = (sum(confs) / len(confs) / 100.0) if confs else None
    return text, confidence


def ocr_pdf_pages(path: Path, *, scale: float = 2.0, max_pages: int = 20) -> str:
    """Rasterise a scanned / image-only PDF and OCR each page with Tesseract.

    Returns ``""`` when the rasteriser (pypdfium2) or Tesseract isn't available,
    or on any render/OCR error — callers treat that as "no text extracted"."""
    if not (_pdfium_ok() and _tesseract_ok()):
        return ""
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except Exception:  # pragma: no cover - guarded by the *_ok() checks
        return ""
    parts: list[str] = []
    try:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(min(len(pdf), max_pages)):
                bitmap = pdf[i].render(scale=scale)  # pyright: ignore[reportArgumentType]  -- pypdfium2 render() takes a float scale
                parts.append(pytesseract.image_to_string(bitmap.to_pil()))
        finally:
            pdf.close()
    except Exception:  # pragma: no cover - engine/format errors
        logger.warning("PDF OCR fallback failed for %s", path.name, exc_info=True)
        return ""
    return "\n".join(parts).strip()


def _pdf_text(path: Path) -> tuple[str, float | None]:
    import pypdf

    reader = pypdf.PdfReader(str(path))
    # Best-effort: read at most _MAX_PDF_PAGES (a receipt is a few pages) and clamp
    # the text — DoS guard (CR-SEC-9).
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:_MAX_PDF_PAGES]).strip()
    if len(text) > _MAX_TEXT_CHARS:
        text = text[:_MAX_TEXT_CHARS]
    # Trust embedded text as a real digital statement only if it actually contains a
    # monetary amount. A tiny text layer on an image-only PDF (a page number, header or
    # watermark) previously returned 0.95 confidence and blocked the OCR fallback — an
    # empty-but-high-confidence parse. No amount ⇒ fall through to rasterise + OCR.
    if _MONEY_RE.search(text):
        return text, 0.95  # embedded (digital) text is exact
    # Scanned / image-only PDF (or a no-amount text scrap) → rasterise + OCR.
    ocr_text = ocr_pdf_pages(path)
    if ocr_text:
        return ocr_text, 0.5  # OCR'd — unverified, flagged for review downstream
    return text, 0.0


def extract_text(path: Path) -> tuple[str, float | None]:
    """Return ``(text, confidence0to1)`` or raise :class:`OcrUnavailable`."""
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        if not _tesseract_ok():
            raise OcrUnavailable("Image OCR needs the 'ocr' extra and the tesseract binary")
        return _ocr_image(path)
    if suffix in PDF_SUFFIXES:
        if not _pypdf_ok():
            raise OcrUnavailable("PDF text extraction needs the 'ocr' extra (pypdf)")
        return _pdf_text(path)
    raise OcrUnavailable(f"No OCR backend for '{suffix}' files")
