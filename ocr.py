"""
ocr.py — Tesseract OCR pipeline for ClaimBack

Handles two cases:
  1. Image files (JPEG, PNG, GIF, WEBP) → direct PIL → Tesseract
  2. PDF files → rasterise each page with pdf2image → Tesseract per page

Returns extracted text and metadata. If OCR fails or isn't needed,
returns None so the caller can fall back to sending the raw bytes to Claude.
"""

import io
import logging
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# ── Lazy imports so the app still starts if these aren't installed ──

def _pil():
    from PIL import Image
    return Image

def _pytesseract():
    import pytesseract
    return pytesseract

def _pdf2image():
    try:
        from pdf2image import convert_from_bytes
        return convert_from_bytes
    except ImportError:
        return None


@dataclass
class OCRResult:
    text: str
    pages: int = 1
    method: str = "tesseract"
    warnings: list[str] = field(default_factory=list)


# ── Public API ──

def ocr_image_bytes(file_bytes: bytes, filename: str = "") -> OCRResult | None:
    """
    Run Tesseract on raw image bytes.
    Returns OCRResult or None if extraction failed / not applicable.
    """
    try:
        Image = _pil()
        pytesseract = _pytesseract()

        img = Image.open(io.BytesIO(file_bytes))

        # Upscale tiny images — Tesseract accuracy drops below ~150 DPI
        img = _ensure_min_dpi(img)

        text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
        text = text.strip()

        if not text:
            return OCRResult(text="", pages=1,
                             warnings=["No text detected in image"])

        log.info("OCR extracted %d chars from %s", len(text), filename or "image")
        return OCRResult(text=text, pages=1)

    except Exception as e:
        log.warning("OCR failed for image %s: %s", filename, e)
        return None


def ocr_pdf_bytes(file_bytes: bytes, filename: str = "",
                  max_pages: int = 20) -> OCRResult | None:
    """
    Rasterise a PDF and run Tesseract on each page.
    Falls back gracefully if pdf2image isn't installed.
    Returns OCRResult or None.
    """
    convert = _pdf2image()
    if convert is None:
        log.warning("pdf2image not installed — skipping PDF OCR for %s", filename)
        return None

    try:
        pytesseract = _pytesseract()

        images = convert(file_bytes, dpi=200, fmt="PNG", first_page=1,
                         last_page=max_pages)

        pages_text = []
        for i, img in enumerate(images, start=1):
            img = _ensure_min_dpi(img, target=200)
            page_text = pytesseract.image_to_string(img, config="--oem 3 --psm 6")
            pages_text.append(page_text.strip())
            log.debug("Page %d: %d chars", i, len(page_text))

        full_text = "\n\n--- Page Break ---\n\n".join(p for p in pages_text if p)

        warnings = []
        if len(images) == max_pages:
            warnings.append(f"PDF truncated to first {max_pages} pages")
        if not full_text:
            warnings.append("No text detected in any page")

        log.info("PDF OCR: %d pages, %d total chars from %s",
                 len(images), len(full_text), filename or "file")

        return OCRResult(text=full_text, pages=len(images), warnings=warnings)

    except Exception as e:
        log.warning("PDF OCR failed for %s: %s", filename, e)
        return None


def should_ocr(mime_type: str) -> bool:
    """Return True if this file type benefits from OCR preprocessing."""
    return mime_type in {
        "image/jpeg", "image/jpg", "image/png",
        "image/gif", "image/webp", "image/tiff",
        "application/pdf",
    }


def process_document(file_bytes: bytes, mime_type: str,
                     filename: str = "") -> dict:
    """
    High-level entry point. Given raw bytes and MIME type:
      - Runs OCR if applicable and text is found
      - Returns a document dict ready for the Claude API

    Returns dict with keys: name, type, data (base64) or text, ocr_used, ocr_pages
    """
    import base64

    base_doc = {
        "name": filename,
        "type": mime_type,
        "ocr_used": False,
        "ocr_pages": 0,
    }

    if not should_ocr(mime_type):
        # Plain text / email — decode as UTF-8
        try:
            base_doc["text"] = file_bytes.decode("utf-8")
            base_doc["type"] = "text/plain"
        except UnicodeDecodeError:
            base_doc["data"] = base64.standard_b64encode(file_bytes).decode()
        return base_doc

    # Try OCR first
    ocr_result = None
    if mime_type == "application/pdf":
        ocr_result = ocr_pdf_bytes(file_bytes, filename)
    else:
        ocr_result = ocr_image_bytes(file_bytes, filename)

    if ocr_result and ocr_result.text:
        # We have good OCR text — send as text to Claude (cheaper + more reliable)
        base_doc["type"] = "text/plain"
        base_doc["text"] = (
            f"[OCR extracted from {filename or mime_type}, "
            f"{ocr_result.pages} page(s)]\n\n{ocr_result.text}"
        )
        base_doc["ocr_used"] = True
        base_doc["ocr_pages"] = ocr_result.pages
        return base_doc

    # OCR failed or returned nothing — send raw bytes to Claude (it can handle images/PDFs natively)
    base_doc["data"] = base64.standard_b64encode(file_bytes).decode()
    return base_doc


# ── Helpers ──

def _ensure_min_dpi(img, target: int = 200):
    """Upscale image if it's too small for reliable OCR."""
    try:
        dpi = img.info.get("dpi", (72, 72))
        current = dpi[0] if isinstance(dpi, tuple) else dpi
        if current and current < target:
            scale = target / current
            new_size = (int(img.width * scale), int(img.height * scale))
            img = img.resize(new_size)
    except Exception:
        pass
    return img
