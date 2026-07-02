# AI-backed document extraction for customer-submitted bills / receipts /
# order screenshots.
#
# Division of labour (same philosophy as classifier.py's escalation design):
#   * REGEX gates the pipeline — free, deterministic. Text messages only get
#     an LLM call when they actually look bill-ish (order numbers, currency
#     amounts, invoice/txn vocabulary). Attachments gate on type alone.
#   * The LLM (vision-capable gpt-4o-mini) does the READING — images,
#     PDFs and free text all reduce to one strict-json_schema extraction
#     call, so the output shape is guaranteed (see classifier.py for the
#     strict-mode rationale).
#   * REGEX again for post-extraction normalisation — amounts to plain
#     decimals, currency symbols to ISO codes. Deterministic cleanup the
#     model shouldn't be trusted with.
#
# Cost control: extraction only fires through the gates above, results are
# idempotent per attachment/message (caller passes seen keys), and the whole
# feature sits behind config.DOC_EXTRACTION_ENABLED.

import asyncio
import base64
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path

import httpx

import config
from llm_client import client

# ── Gate: does free text look like it carries billing/order details? ──────
# Deliberately broad-but-cheap: false positives cost one small LLM call that
# will come back is_financial_document=false; false negatives mean a missed
# extraction (agent still sees the raw message). Tuned for IN + generic
# formats: ₹/Rs/INR amounts, order/invoice/txn vocabulary.
BILL_TEXT_HINT = re.compile(
    r"""(?ix)
    \b(
        invoice | receipt | bill\b | billed |
        order\s*(no|number|id|\#) |
        (txn|transaction|payment|utr|ref(erence)?)\s*(no|number|id|\#)? \s*[:\-]?\s*\w{6,} |
        amount\s*(paid|due|charged) |
        (paid|charged|refunded|debited)\s*(rs\.?|inr|₹|\$|usd|eur)
    )\b
    | (₹|\brs\.?\s?|\binr\s?|\$)\s*[\d,]{3,}
    """,
)


def text_looks_billish(text: str) -> bool:
    return bool(text and BILL_TEXT_HINT.search(text))


# ── Strict structured-output schema ───────────────────────────────────────
# First-class fields for the attributes agents most need; everything is
# required (strict-mode rule) with "" meaning "not present in the document".
# `other_details` is the DYNAMIC part: whatever else the model finds in THIS
# particular document lands there as label/value pairs — so the stored shape
# adapts per document (GST numbers, tracking ids, seller names, item lists)
# without giving up schema enforcement.
DOCUMENT_RESPONSE_SCHEMA = {
    "name": "document_extraction",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "is_financial_document", "document_type", "order_id",
            "invoice_number", "transaction_id", "amount", "currency",
            "document_date", "merchant", "issue_hint", "other_details",
        ],
        "properties": {
            "is_financial_document": {"type": "boolean"},
            "document_type": {
                "type": "string",
                "enum": [
                    "invoice", "receipt", "payment_screenshot",
                    "order_screenshot", "shipping_label", "bank_statement",
                    "refund_proof", "other",
                ],
            },
            "order_id":        {"type": "string"},
            "invoice_number":  {"type": "string"},
            "transaction_id":  {"type": "string"},
            "amount":          {"type": "string"},
            "currency":        {"type": "string"},
            "document_date":   {"type": "string"},
            "merchant":        {"type": "string"},
            "issue_hint":      {"type": "string"},
            "other_details": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["label", "value"],
                    "properties": {
                        "label": {"type": "string"},
                        "value": {"type": "string"},
                    },
                },
            },
        },
    },
}

EXTRACTION_SYSTEM_PROMPT = """\
You extract structured data from documents customers send to a store's
support inbox: invoices, receipts, payment/order screenshots, shipping
labels, bank statements, or plain-text messages quoting order details.

Rules:
- Only report values VISIBLY PRESENT in the input. Never guess or invent.
  Use "" for any field not present.
- is_financial_document: true only if the input actually contains billing /
  order / payment information. A meme, selfie, product photo or casual
  message is false (set every other field to "" / empty).
- amount: the main total as written (you may keep separators); currency:
  the currency symbol or code as written (₹, Rs, INR, $, USD...).
- document_date: as written; prefer ISO YYYY-MM-DD when unambiguous. Never
  infer missing parts — a date with no year stays as written ("1 June"
  stays "1 June", NOT a guessed "2023-06-01").
- issue_hint: one short sentence describing the problem the customer
  appears to be raising, if inferable from the document or message ("" if
  none).
- other_details: any OTHER useful attributes present (GST number, tracking
  id, item names, quantity, seller, payment method, UPI id...). Keep labels
  short and values verbatim.
"""

# ── Post-extraction normalisation (regex layer) ───────────────────────────
_CURRENCY_MAP = {
    "₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupees": "INR",
    "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR", "£": "GBP",
}


def _normalise(fields: dict) -> dict:
    out = dict(fields)
    amount = (out.get("amount") or "").strip()
    if amount:
        # Pull the first numeric token rather than stripping characters —
        # naive char-stripping leaks the dot from "Rs." into the number
        # ("Rs. 2,999" → ".2999", caught in testing). The token regex
        # handles "₹1,499.00" → 1499.00, "Rs. 2,999" → 2999, "$49.99" → 49.99.
        m = re.search(r"\d[\d,]*(?:\.\d+)?", amount)
        if m:
            out["amount"] = m.group(0).replace(",", "")
    currency = (out.get("currency") or "").strip().lower()
    out["currency"] = _CURRENCY_MAP.get(currency, (out.get("currency") or "").upper().strip())
    return out


# ── LLM call plumbing ─────────────────────────────────────────────────────
async def _call_extraction(content_parts: list, source: str,
                           lf_parent: dict = None) -> dict | None:
    """Run one strict-schema extraction call. Returns the parsed dict or
    None on any failure (callers treat None as 'nothing extracted')."""
    try:
        r = await client.chat.completions.create(
            model=config.DOC_EXTRACTION_MODEL,
            temperature=0,
            max_tokens=600,
            response_format={
                "type": "json_schema",
                "json_schema": DOCUMENT_RESPONSE_SCHEMA,
            },
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": content_parts},
            ],
            name="document-extraction",
            metadata={"source": source, "langfuse_tags": ["doc-extraction"]},
            **(lf_parent or {}),
        )
        return _normalise(json.loads(r.choices[0].message.content))
    except Exception as e:  # noqa: BLE001 — best-effort by design
        print(f"[docs] extraction call failed ({source}): {type(e).__name__}: {e}")
        return None


async def _extract_from_image(data: bytes, mime: str,
                              lf_parent: dict = None) -> dict | None:
    b64 = base64.b64encode(data).decode()
    return await _call_extraction(
        [
            {"type": "text", "text": "Extract the document data from this image."},
            {
                "type": "image_url",
                # "high" detail — receipts/bills carry small print that the
                # low-detail downscale destroys. This is the main cost knob:
                # high-detail image ≈ $0.002-0.01 with gpt-4o-mini.
                "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "high"},
            },
        ],
        source="image",
        lf_parent=lf_parent,
    )


async def _extract_from_text(text: str, lf_parent: dict = None) -> dict | None:
    return await _call_extraction(
        [{"type": "text", "text": f"Extract the document data from this message:\n\n{text}"}],
        source="text",
        lf_parent=lf_parent,
    )


def _rasterise_pdf_first_page(data: bytes) -> bytes | None:
    """Render page 1 of a scanned PDF to PNG via poppler's pdftoppm.
    SYNCHRONOUS — call through asyncio.to_thread; pdftoppm can take
    seconds, and running it inline would block the bridge's event loop
    (every webhook stalls while one PDF renders)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "doc.pdf"
            pdf_path.write_bytes(data)
            out_prefix = Path(tmp) / "page"
            subprocess.run(
                ["pdftoppm", "-png", "-r", "150", "-f", "1", "-l", "1",
                 str(pdf_path), str(out_prefix)],
                check=True, capture_output=True, timeout=30,
            )
            pngs = sorted(Path(tmp).glob("page*.png"))
            return pngs[0].read_bytes() if pngs else None
    except FileNotFoundError:
        print("[docs] scanned PDF skipped — poppler-utils not installed "
              "(apt install poppler-utils)")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[docs] pdf rasterise failed: {e}")
        return None


def _pdf_text_layer(data: bytes) -> str:
    """Extract the text layer from the first pages. SYNCHRONOUS — pypdf
    parsing is CPU-bound; call through asyncio.to_thread."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:3])
    except Exception as e:  # noqa: BLE001
        print(f"[docs] pypdf text extraction failed: {e}")
        return ""


async def _extract_from_pdf(data: bytes, lf_parent: dict = None) -> dict | None:
    """Digital PDFs (text layer) → cheap text extraction via pypdf.
    Scanned PDFs (no text layer) → render page 1 with poppler's pdftoppm
    and go through the vision path. Both CPU/subprocess steps run in a
    worker thread so the event loop keeps serving webhooks."""
    text = await asyncio.to_thread(_pdf_text_layer, data)
    if len(text.strip()) > 40:
        return await _extract_from_text(text[:8000], lf_parent=lf_parent)

    png = await asyncio.to_thread(_rasterise_pdf_first_page, data)
    if png is None:
        return None
    return await _extract_from_image(png, "image/png", lf_parent=lf_parent)


async def _download(url: str) -> tuple[bytes | None, str]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
            r = await http.get(url)
            if r.status_code >= 300:
                print(f"[docs] attachment download failed [{r.status_code}]")
                return None, ""
            return r.content, (r.headers.get("content-type") or "").split(";")[0]
    except Exception as e:  # noqa: BLE001
        print(f"[docs] attachment download error: {type(e).__name__}: {e}")
        return None, ""


# ── Public entry point ────────────────────────────────────────────────────
# Attachment size cap — a 20 MB photo would be slow to b64 and pricey to
# send; bills are small. Oversized files are skipped with a log line.
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024


def sniff_image_mime(data: bytes) -> str | None:
    """Return the OpenAI-vision-supported mime for these bytes, or None.

    Trusts MAGIC BYTES, never extensions or content-type headers — both lie
    routinely (caught in testing: an AVIF photo renamed to .jpg made the
    vision API 400 with invalid_image_format). HEIC/AVIF — what iPhones and
    many Android cameras produce — are NOT supported by OpenAI vision, so
    they return None and the caller skips with a log line instead of
    burning a doomed API call."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None  # HEIC/AVIF/TIFF/BMP/... — vision API rejects these


async def extract_for_message(content: str, attachments: list,
                              message_id, seen_keys: set,
                              lf_parent: dict = None) -> list[dict]:
    """Run extraction for one incoming message. Returns a list of result
    dicts, each carrying a stable `source_key` for idempotency:
      att:<attachment_id>   for attachments
      msg:<message_id>      for text-only extraction
    Only results the model marked is_financial_document=true are returned.
    """
    results: list[dict] = []

    for att in attachments or []:
        att_id = att.get("id")
        key = f"att:{att_id}"
        if not att_id or key in seen_keys:
            continue
        # Chatwoot file_type: images arrive as 'image', PDFs as 'file'.
        if att.get("file_type") not in ("image", "file"):
            continue
        data_url = att.get("data_url")
        if not data_url:
            continue

        data, mime = await _download(data_url)
        if not data:
            continue
        if len(data) > MAX_ATTACHMENT_BYTES:
            print(f"[docs] attachment {att_id} skipped — "
                  f"{len(data) // 1024} KB exceeds cap")
            continue

        # Route by ACTUAL bytes, not by mime/extension — channel CDNs and
        # customers rename files freely. `mime` from the download response
        # is intentionally ignored here.
        if data.startswith(b"%PDF"):
            fields = await _extract_from_pdf(data, lf_parent=lf_parent)
        else:
            sniffed = sniff_image_mime(data)
            if sniffed is None:
                print(f"[docs] attachment {att_id} skipped — bytes are not "
                      f"a supported image/pdf format (HEIC/AVIF/docx/...)")
                continue
            fields = await _extract_from_image(data, sniffed, lf_parent=lf_parent)

        if fields and fields.get("is_financial_document"):
            fields["source_key"] = key
            fields["source"] = "attachment"
            results.append(fields)

    # Text-only path: no qualifying attachments AND the text passes the
    # regex gate. (When a bill image carries a caption, the image result
    # already includes the context — no second call.)
    text_key = f"msg:{message_id}"
    if not results and text_looks_billish(content) and text_key not in seen_keys:
        fields = await _extract_from_text(content[:8000], lf_parent=lf_parent)
        if fields and fields.get("is_financial_document"):
            fields["source_key"] = text_key
            fields["source"] = "text"
            results.append(fields)

    return results
