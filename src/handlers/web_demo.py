"""Web demo handler - lets visitors try quotation generation from the browser."""

import base64
import logging
import re
import uuid

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from src.services.ai_extractor import GeminiExtractor
from src.services.database import DynamoDBClient
from src.services.ledger_assistant import LedgerAssistant
from src.services.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)

MONTHLY_LIMIT_PER_IP = 5
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~60s of compressed voice audio, generous ceiling
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB ceiling for a bill photo/PDF
DEMO_LEDGER_TTL_DAYS = 1  # demo ledger data is throwaway, unlike a real user's
SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


class WebDemoHandler:
    """Generates a sample quotation PDF from text input on the web demo page."""

    def __init__(self, extractor: GeminiExtractor, pdf_gen: PDFGenerator, db_client: DynamoDBClient):
        self.extractor = extractor
        self.pdf_gen = pdf_gen
        self.db = db_client
        self.ledger = LedgerAssistant(db_client, extractor, ttl_days=DEMO_LEDGER_TTL_DAYS)

    async def handle_generate(self, request: Request) -> Response:
        """Accept a text description and return the extracted data + PDF."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=MONTHLY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached this month's limit of {MONTHLY_LIMIT_PER_IP} free demo "
                        "quotations. Message BillBolo on WhatsApp for unlimited use: "
                        "wa.me/918123422932"
                    },
                )

            body = await request.json()
            text = (body.get("text") or "").strip()

            if not text:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Please describe what you'd like to quote."},
                )

            if len(text) > 1000:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Description is too long (max 1000 characters)."},
                )

            extracted = self.extractor.extract_from_text(text)
            if not extracted:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "Couldn't understand that. Try something like "
                        "'Quotation for Sharma Ji, 80 sq ft wardrobe at 1600 per sq ft'."
                    },
                )

            return self._build_success_response(extracted)
        except Exception as e:
            logger.error(f"Web demo generation failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": "Something went wrong. Please try again."},
            )

    async def handle_generate_audio(self, request: Request) -> Response:
        """Accept a recorded voice note and return the extracted data + PDF."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=MONTHLY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached this month's limit of {MONTHLY_LIMIT_PER_IP} free demo "
                        "quotations. Message BillBolo on WhatsApp for unlimited use: "
                        "wa.me/918123422932"
                    },
                )

            form = await request.form()
            audio_file = form.get("audio")
            if not isinstance(audio_file, UploadFile):
                return JSONResponse(
                    status_code=400,
                    content={"error": "No audio file received."},
                )

            audio_bytes = await audio_file.read()
            if not audio_bytes:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Empty audio recording. Please try again."},
                )

            if len(audio_bytes) > MAX_AUDIO_BYTES:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Recording is too long (max 60 seconds)."},
                )

            mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
            extracted = self.extractor.extract_from_audio(audio_bytes, audio_mime_type=mime_type)
            if not extracted:
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "Couldn't understand that recording. Try speaking clearly "
                        "with item names, quantities and rates."
                    },
                )

            return self._build_success_response(extracted)
        except Exception as e:
            logger.error(f"Web demo audio generation failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": "Something went wrong. Please try again."},
            )

    async def handle_generate_expense(self, request: Request) -> Response:
        """Accept a bill/receipt photo or PDF and return the extracted expense data.
        Demo only - nothing is persisted or tied to any account."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=MONTHLY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached this month's limit of {MONTHLY_LIMIT_PER_IP} free demo "
                        "tries. Message BillBolo on WhatsApp for unlimited use: "
                        "wa.me/918123422932"
                    },
                )

            form = await request.form()
            image_file = form.get("image")
            if not isinstance(image_file, UploadFile):
                return JSONResponse(status_code=400, content={"error": "No file received."})

            image_bytes = await image_file.read()
            if not image_bytes:
                return JSONResponse(status_code=400, content={"error": "Empty file."})

            if len(image_bytes) > MAX_IMAGE_BYTES:
                return JSONResponse(status_code=400, content={"error": "File is too large (max 10 MB)."})

            mime_type = getattr(image_file, "content_type", None) or "image/jpeg"
            extracted = self.extractor.extract_from_image(image_bytes, image_mime_type=mime_type)
            if not extracted:
                return JSONResponse(
                    status_code=422,
                    content={"error": "Couldn't read that bill/receipt. Try a clearer photo or a different file."},
                )

            return JSONResponse(
                status_code=200,
                content={
                    "vendor": extracted.seller_name or extracted.client_name,
                    "items": [item.model_dump() for item in extracted.items],
                    "grand_total": extracted.grand_total,
                    "category": extracted.expense_category,
                    "seller_name": extracted.seller_name,
                    "seller_gstin": extracted.seller_gstin,
                    "order_date": extracted.order_date,
                },
            )
        except Exception as e:
            logger.error(f"Web demo expense extraction failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": "Something went wrong. Please try again."},
            )

    async def handle_ledger_command(self, request: Request) -> Response:
        """Accept a ledger-style text message ('Ramesh owes me 2000', 'who
        owes me money') and return the assistant's reply. Demo only - scoped
        to a short-lived, client-generated session id, never a real phone
        number, and the underlying ledger data expires after a day."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=MONTHLY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached this month's limit of {MONTHLY_LIMIT_PER_IP} free demo "
                        "tries. Message BillBolo on WhatsApp for unlimited use: "
                        "wa.me/918123422932"
                    },
                )

            body = await request.json()
            text = (body.get("text") or "").strip()
            session_id = (body.get("session_id") or "").strip()

            if not text:
                return JSONResponse(status_code=400, content={"error": "Please type a message."})
            if len(text) > 500:
                return JSONResponse(status_code=400, content={"error": "Message is too long (max 500 characters)."})

            identifier, session_id = self._resolve_demo_ledger_identifier(session_id)
            reply = self.ledger.handle(identifier, text, force=True)
            return JSONResponse(status_code=200, content={"reply": reply, "session_id": session_id})
        except Exception as e:
            logger.error(f"Web demo ledger command failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def handle_ledger_audio_command(self, request: Request) -> Response:
        """Same as handle_ledger_command, but for a recorded voice note -
        works in whatever language the person spoke, same as the real bot."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=MONTHLY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached this month's limit of {MONTHLY_LIMIT_PER_IP} free demo "
                        "tries. Message BillBolo on WhatsApp for unlimited use: "
                        "wa.me/918123422932"
                    },
                )

            form = await request.form()
            audio_file = form.get("audio")
            session_id = str(form.get("session_id") or "")
            if not isinstance(audio_file, UploadFile):
                return JSONResponse(status_code=400, content={"error": "No audio file received."})

            audio_bytes = await audio_file.read()
            if not audio_bytes:
                return JSONResponse(status_code=400, content={"error": "Empty recording. Please try again."})
            if len(audio_bytes) > MAX_AUDIO_BYTES:
                return JSONResponse(status_code=400, content={"error": "Recording is too long (max 60 seconds)."})

            mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
            identifier, session_id = self._resolve_demo_ledger_identifier(session_id)
            reply = self.ledger.handle_audio(identifier, audio_bytes, mime_type)
            return JSONResponse(status_code=200, content={"reply": reply, "session_id": session_id})
        except Exception as e:
            logger.error(f"Web demo ledger audio command failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    def _resolve_demo_ledger_identifier(self, session_id: str) -> tuple[str, str]:
        """Validate/generate the session id and ensure a short-lived demo
        profile exists for it (pending-confirmation storage needs one)."""
        if not SESSION_ID_RE.match(session_id or ""):
            session_id = uuid.uuid4().hex
        identifier = f"demoledger#{session_id}"
        if not self.db.get_user_profile(identifier):
            self.db.create_user_profile(identifier, business_name="Demo", ttl_days=DEMO_LEDGER_TTL_DAYS)
        return identifier, session_id

    def _build_success_response(self, extracted) -> JSONResponse:
        """Generate the demo PDF and build the shared success response payload.
        Dispatches to a real invoice PDF (due date/payment mode/balance due)
        when the text was classified as one, instead of always labeling the
        output a quotation."""
        generate_fn = self.pdf_gen.generate_invoice if extracted.data_type == "invoice" else self.pdf_gen.generate_quotation
        pdf_bytes = generate_fn(
            extracted_data=extracted,
            business_info={"name": "BillBolo Demo"},
            is_pro=False,
        )
        if not pdf_bytes:
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to generate PDF. Please try again."},
            )

        return JSONResponse(
            status_code=200,
            content={
                "data_type": extracted.data_type,
                "client_name": extracted.client_name,
                "items": [item.model_dump() for item in extracted.items],
                "total_before_gst": extracted.total_before_gst,
                "gst_amount": extracted.gst_amount,
                "grand_total": extracted.grand_total,
                "due_date": getattr(extracted, "due_date", None),
                "payment_mode": getattr(extracted, "payment_mode", None),
                "amount_paid": getattr(extracted, "amount_paid", None),
                "pdf_base64": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
            },
        )

    def _get_client_ip(self, request: Request) -> str:
        """Best-effort client IP extraction behind API Gateway/Mangum."""
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        if request.client and request.client.host:
            return request.client.host
        return "unknown"


def create_web_demo_handler() -> WebDemoHandler:
    """Create WebDemoHandler with dependencies injected."""
    return WebDemoHandler(
        extractor=GeminiExtractor(),
        pdf_gen=PDFGenerator(),
        db_client=DynamoDBClient(),
    )
