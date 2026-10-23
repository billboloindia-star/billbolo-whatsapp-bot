"""Gemini Flash API multimodal parser for extracting structured data from audio/images."""

import json
import logging
from typing import Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from src.config import settings

logger = logging.getLogger(__name__)


class LineItem(BaseModel):
    """A single line item in a quotation/invoice."""

    item_name: str = Field(..., description="Name of the item/service")
    quantity: float = Field(..., description="Quantity")
    rate: float = Field(..., description="Rate per unit in INR")
    unit: str = Field(default="qty", description="Unit of measurement (sq ft, hrs, etc)")
    amount: Optional[float] = Field(None, description="Total amount (auto-calculated)")
    hsn_code: Optional[str] = Field(None, description="HSN/SAC code, if printed on the bill")

    def calculate_amount(self) -> float:
        """Calculate total amount for this item."""
        self.amount = self.quantity * self.rate
        return self.amount


class ExtractedData(BaseModel):
    """Structured data extracted from voice note or bill image."""

    data_type: str = Field(..., description="Type: 'quotation', 'invoice', 'expense'")
    client_name: Optional[str] = Field(None, description="Client/Party name")
    items: list[LineItem] = Field(default_factory=list, description="Line items")
    notes: Optional[str] = Field(None, description="Additional notes/terms")
    gst_percentage: float = Field(default=18.0, description="GST percentage")
    total_before_gst: Optional[float] = Field(None, description="Subtotal before tax")
    gst_amount: Optional[float] = Field(None, description="GST amount")
    grand_total: Optional[float] = Field(None, description="Final total with tax")
    currency: str = Field(default="INR", description="Currency code")
    language_detected: Optional[str] = Field(None, description="Detected language (hi, en, etc)")
    seller_name: Optional[str] = Field(None, description="Name of the selling business/shop, if printed on a bill")
    seller_gstin: Optional[str] = Field(None, description="Seller's GSTIN, if printed on a bill")
    seller_address: Optional[str] = Field(None, description="Seller's address/place, if printed on a bill")
    billing_address: Optional[str] = Field(None, description="Billing address, if printed on a bill")
    shipping_address: Optional[str] = Field(None, description="Shipping address, if printed on a bill")
    order_date: Optional[str] = Field(None, description="Order/invoice date, if printed on a bill")


class GeminiExtractor:
    """Gemini Flash multimodal parser with strict JSON validation."""

    MODEL_NAME = "gemini-flash-lite-latest"

    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)

    def extract_from_audio(self, audio_bytes: bytes, audio_mime_type: str = "audio/ogg") -> Optional[ExtractedData]:
        """Extract structured data from audio (voice note)."""
        try:
            prompt = self._build_extraction_prompt("voice note")
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type),
                    prompt,
                ],
            )
            return self._parse_response(response.text)
        except Exception as e:
            logger.error(f"Failed to extract from audio: {e}")
            return None

    def extract_from_text(self, text: str) -> Optional[ExtractedData]:
        """Extract structured data from a plain text description."""
        try:
            prompt = self._build_extraction_prompt("text description")
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[text, prompt],
            )
            return self._parse_response(response.text)
        except Exception as e:
            logger.error(f"Failed to extract from text: {e}")
            return None

    def extract_from_image(self, image_bytes: bytes, image_mime_type: str = "image/jpeg") -> Optional[ExtractedData]:
        """Extract structured data from bill/receipt image."""
        try:
            prompt = self._build_extraction_prompt("bill/receipt image")
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=image_mime_type),
                    prompt,
                ],
            )
            return self._parse_response(response.text)
        except Exception as e:
            logger.error(f"Failed to extract from image: {e}")
            return None

    def _build_extraction_prompt(self, source_type: str) -> str:
        """Build the extraction prompt for Gemini."""
        return f"""You are a professional bill/quotation extraction assistant for Indian businesses.

Extract structured data from this {source_type}. The user may speak in English, Hindi, Bengali, Tamil, Telugu, Marathi, Hinglish, or a mix of English and any Indian language.

IMPORTANT: Always respond with ONLY a valid JSON object (no markdown, no extra text).

JSON Schema:
{{
    "data_type": "quotation|invoice|expense",
    "client_name": "Name of client/party (or null if not mentioned)",
    "items": [
        {{
            "item_name": "Name of item/service",
            "quantity": 5,
            "rate": 1600.00,
            "unit": "sq ft|hrs|pcs|kg|etc",
            "amount": null,
            "hsn_code": "HSN or SAC code printed next to this item on the bill (or null if not present)"
        }}
    ],
    "notes": "Payment terms, delivery details, etc (or null)",
    "gst_percentage": 18,
    "total_before_gst": null,
    "gst_amount": null,
    "grand_total": null,
    "currency": "INR",
    "language_detected": "hi|en|other",
    "seller_name": "Name of the selling business/shop printed on the bill (or null)",
    "seller_gstin": "Seller's GSTIN printed on the bill (or null)",
    "seller_address": "Seller's address/place printed on the bill (or null)",
    "billing_address": "Billing address printed on the bill (or null)",
    "shipping_address": "Shipping address printed on the bill (or null, if same as billing leave shipping_address null)",
    "order_date": "Order/invoice date printed on the bill, as written (or null)"
}}

Rules:
1. Extract EXACT quantities and rates mentioned (do not round)
2. If rate is in thousands (e.g., "1600 per sq ft"), use exact value
3. If items lack rates, set rate to 0 and mark for manual lookup
4. Calculate totals: total_before_gst = SUM(quantity * rate), gst_amount = total_before_gst * (gst_percentage/100), grand_total = total_before_gst + gst_amount
5. Detect language and set language_detected field
6. For voice notes: user may say "Quotation for Sharma Ji, 80 sq ft wardrobe at 1,600 per sq ft" — parse exactly
7. If a bill/receipt shows an HSN or SAC code next to an item (common on GST invoices), capture it exactly as printed in that item's hsn_code field. Leave it null if the bill has no HSN/SAC codes.
8. If a bill/receipt shows the seller's business name, GSTIN, or address (usually printed at the top as the "Sold By" / letterhead), capture them in seller_name, seller_gstin, seller_address. Capture billing_address and shipping_address separately if both are printed (common on e-commerce invoices) - leave shipping_address null if only one address is shown. Capture order_date/invoice_date exactly as printed in order_date. Leave any of these null if not present on the bill - do not guess or invent them.

Return ONLY the JSON object, no other text."""

    def _parse_response(self, response_text: str) -> Optional[ExtractedData]:
        """Parse and validate Gemini response as ExtractedData."""
        try:
            # Extract JSON from response (may contain markdown formatting)
            json_text = response_text.strip()
            if json_text.startswith("```"):
                json_text = json_text.split("```")[1]
                if json_text.startswith("json"):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            data_dict = json.loads(json_text)
            extracted = ExtractedData(**data_dict)

            # Calculate totals
            for item in extracted.items:
                item.calculate_amount()

            extracted.total_before_gst = sum(item.amount or 0 for item in extracted.items)
            extracted.gst_amount = extracted.total_before_gst * (extracted.gst_percentage / 100)
            extracted.grand_total = extracted.total_before_gst + extracted.gst_amount

            logger.info(f"Successfully extracted data: {extracted.data_type}")
            return extracted
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            logger.debug(f"Raw response: {response_text}")
            return None
