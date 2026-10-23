"""Web demo handler - lets visitors try quotation generation from the browser."""

import base64
import logging

from fastapi import Request, Response, UploadFile
from fastapi.responses import JSONResponse

from src.services.ai_extractor import GeminiExtractor
from src.services.database import DynamoDBClient
from src.services.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)

DAILY_LIMIT_PER_IP = 5
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # ~60s of compressed voice audio, generous ceiling


class WebDemoHandler:
    """Generates a sample quotation PDF from text input on the web demo page."""

    def __init__(self, extractor: GeminiExtractor, pdf_gen: PDFGenerator, db_client: DynamoDBClient):
        self.extractor = extractor
        self.pdf_gen = pdf_gen
        self.db = db_client

    async def handle_generate(self, request: Request) -> Response:
        """Accept a text description and return the extracted data + PDF."""
        try:
            client_ip = self._get_client_ip(request)
            if not self.db.check_rate_limit(client_ip, limit=DAILY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached today's limit of {DAILY_LIMIT_PER_IP} free demo "
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
            if not self.db.check_rate_limit(client_ip, limit=DAILY_LIMIT_PER_IP):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": f"You've reached today's limit of {DAILY_LIMIT_PER_IP} free demo "
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

    def _build_success_response(self, extracted) -> JSONResponse:
        """Generate the demo PDF and build the shared success response payload."""
        pdf_bytes = self.pdf_gen.generate_quotation(
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
                "client_name": extracted.client_name,
                "items": [item.model_dump() for item in extracted.items],
                "total_before_gst": extracted.total_before_gst,
                "gst_amount": extracted.gst_amount,
                "grand_total": extracted.grand_total,
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
