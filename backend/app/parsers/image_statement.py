"""Image / scanned bank-statement parser (backlog: import a photo/scan).

A photo or scanned image of a statement is OCR'd with Tesseract, then the same
review-heavy text→rows heuristic as the PDF parser (``parse_statement_text``)
turns it into transactions — so every row is flagged ``needs_review=True``.

Off the happy path it degrades clearly: no OCR engine → a helpful ParseError;
no rows recognised → a "use the CSV export" hint. The text→rows step is the
pure, unit-tested ``parse_statement_text``; only the image→text hop needs an engine.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.parsers.base import BaseStatementParser, ParseError, StandardTransaction
from app.parsers.generic_pdf import parse_statement_text
from app.services import ocr_service
from app.services.ocr_service import IMAGE_SUFFIXES, OcrUnavailable

# Magic-byte prefixes for the common image formats (PNG/JPEG/GIF/BMP/WebP).
_IMAGE_MAGIC = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"RIFF")


class ImageStatementParser(BaseStatementParser):
    parser_id = "image_statement"
    institution = "Generic (image / scan)"
    format = "image"

    def __init__(self, default_currency: str = "GBP"):
        self.default_currency = default_currency

    def can_parse(self, filename: str, content: bytes) -> bool:
        if Path(filename).suffix.lower() in IMAGE_SUFFIXES:
            return True
        return any(content.startswith(m) for m in _IMAGE_MAGIC)

    def parse(self, filename: str, content: bytes) -> list[StandardTransaction]:
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            suffix = ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        try:
            text, _conf = ocr_service.extract_text(tmp_path)
        except OcrUnavailable as exc:
            raise ParseError(
                "Reading a photo/scan needs OCR (the 'ocr' extra + the tesseract "
                "binary) — it's installed in the Home Assistant add-on. Or use the CSV export."
            ) from exc
        finally:
            tmp_path.unlink(missing_ok=True)

        rows = parse_statement_text(text, self.default_currency)
        if not rows:
            raise ParseError(
                "No transactions recognised in the image. Use a clear, straight "
                "photo/scan of the statement, or import the CSV export instead."
            )
        return rows
