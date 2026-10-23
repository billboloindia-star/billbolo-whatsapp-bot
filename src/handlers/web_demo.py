"""Web demo handler - lets visitors try quotation generation from the browser."""

import base64
import logging

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from src.services.ai_extractor import GeminiExtractor
from src.services.pdf_generator import PDFGenerator

logger = logging.getLogger(__name__)


class WebDemoHandler:
    """Generates a sample quotation PDF from text input on the web demo page."""

    def __init__(self, extractor: GeminiExtractor, pdf_gen: PDFGenerator):
        self.extractor = extractor
        self.pdf_gen = pdf_gen

    async def handle_generate(self, request: Request) -> Response:
        """Accept a text description and return the extracted data + PDF."""
        try:
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
        except Exception as e:
            logger.error(f"Web demo generation failed: {e}")
            return JSONResponse(
                status_code=500,
                content={"error": "Something went wrong. Please try again."},
            )


def create_web_demo_handler() -> WebDemoHandler:
    """Create WebDemoHandler with dependencies injected."""
    return WebDemoHandler(
        extractor=GeminiExtractor(),
        pdf_gen=PDFGenerator(),
    )
