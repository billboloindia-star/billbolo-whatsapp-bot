"""Gemini Flash API multimodal parser for extracting structured data from audio/images."""

import json
import logging
from typing import Literal, Optional

from google import genai
from google.genai import types
from pydantic import BaseModel, Field, ValidationError

from src.config import settings

logger = logging.getLogger(__name__)

VOICE_COMMAND_ACTIONS = (
    "navigate",
    "create_quotation",
    "log_expense",
    "add_client",
    "add_ledger_entry",
    "mark_ledger_paid",
    "check_balance",
    "unknown",
)

EXPENSE_CATEGORIES = (
    "Materials",
    "Labour",
    "Transport",
    "Rent",
    "Utilities",
    "Equipment",
    "Office Supplies",
    "Professional Fees",
    "Other",
)


class VoiceCommand(BaseModel):
    """A single voice command, classified into one of a fixed set of app actions."""

    action: Literal[
        "navigate",
        "create_quotation",
        "log_expense",
        "add_client",
        "add_ledger_entry",
        "mark_ledger_paid",
        "check_balance",
        "unknown",
    ] = Field(..., description="Which supported action this command maps to")
    confirmation_text: str = Field(
        ..., description="A short, human-readable summary of what will happen, shown to the user to confirm"
    )
    # navigate
    page: Optional[str] = Field(None, description="dashboard|quotations|clients|expenses|ledger|profile")
    # create_quotation
    description: Optional[str] = Field(None, description="What the user said about the job, verbatim/summarized")
    # log_expense
    vendor: Optional[str] = Field(None, description="Who the expense was paid to")
    amount: Optional[float] = Field(None, description="Amount in INR")
    notes: Optional[str] = Field(None, description="Any extra notes mentioned")
    # add_client
    client_name: Optional[str] = Field(None, description="Client name (add_client, add_ledger_entry)")
    client_phone: Optional[str] = Field(None, description="Client phone number (add_client only)")
    # add_ledger_entry
    ledger_description: Optional[str] = Field(None, description="What the ledger entry is for")
    due_date: Optional[str] = Field(None, description="Due date if mentioned, as YYYY-MM-DD if determinable, else as said")
    # mark_ledger_paid, check_balance
    client_name_query: Optional[str] = Field(
        None, description="Name to search for among unpaid ledger entries (null in check_balance means 'everyone')"
    )


class LineItem(BaseModel):
    """A single line item in a quotation/invoice."""

    item_name: str = Field(..., description="Name of the item/service")
    quantity: float = Field(..., description="Quantity")
    rate: float = Field(..., description="Rate per unit in INR")
    unit: str = Field(default="qty", description="Unit of measurement (sq ft, hrs, etc)")
    amount: Optional[float] = Field(None, description="Total amount (auto-calculated)")
    hsn_code: Optional[str] = Field(None, description="HSN/SAC code, if printed on the bill")
    discount: Optional[float] = Field(None, description="Discount/coupon applied to this line, if printed on a bill")
    taxable_value: Optional[float] = Field(None, description="Value this line's tax is calculated on, if printed on a bill")
    sgst: Optional[float] = Field(None, description="SGST amount for this line, if printed on a bill")
    cgst: Optional[float] = Field(None, description="CGST amount for this line, if printed on a bill")
    igst: Optional[float] = Field(None, description="IGST amount for this line (interstate), if printed on a bill")
    line_total: Optional[float] = Field(None, description="Final amount for this line as printed on a bill, after discount and tax")

    def calculate_amount(self) -> float:
        """Calculate total amount for this item."""
        self.amount = self.quantity * self.rate
        return self.amount

    def display_amount(self) -> float:
        """Best amount to show for this line: the printed line total if we have
        one (accounts for discount/tax), otherwise the plain quantity*rate."""
        return self.line_total if self.line_total is not None else (self.amount or 0)


class ExtractedData(BaseModel):
    """Structured data extracted from voice note or bill image."""

    data_type: str = Field(..., description="Type: 'quotation', 'invoice', 'expense'")
    client_name: Optional[str] = Field(None, description="Client/Party name")
    items: list[LineItem] = Field(default_factory=list, description="Line items")
    notes: Optional[str] = Field(None, description="Additional notes/terms")
    gst_percentage: Optional[float] = Field(default=18.0, description="GST percentage - 0 if the bill shows no GST")
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
    expense_category: Optional[str] = Field(None, description="For expenses/invoices only: one of the fixed expense categories, inferred from vendor/items")


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

    def classify_voice_command(
        self, audio_bytes: bytes, audio_mime_type: str = "audio/webm"
    ) -> Optional[VoiceCommand]:
        """Classify a short spoken voice command into one of the app's fixed set of actions."""
        try:
            prompt = self._build_voice_command_prompt()
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type),
                    prompt,
                ],
            )
            return self._parse_voice_command_response(response.text)
        except Exception as e:
            logger.error(f"Failed to classify voice command: {e}")
            return None

    def classify_text_command(self, text: str) -> Optional[VoiceCommand]:
        """Classify a short text message into one of the app's fixed set of actions.
        Same classifier/prompt as classify_voice_command, just text input - used for
        WhatsApp ledger commands ('Ramesh owes me 2000', 'mark Ramesh as paid')."""
        try:
            prompt = self._build_voice_command_prompt()
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[text, prompt],
            )
            return self._parse_voice_command_response(response.text)
        except Exception as e:
            logger.error(f"Failed to classify text command: {e}")
            return None

    def _build_voice_command_prompt(self) -> str:
        return """You are a voice command classifier for BillBolo, a business app for Indian contractors and freelancers. The user may speak in English, Hindi, Bengali, Tamil, Telugu, Marathi, Hinglish, or a mix of English and any Indian language.

Classify this command into EXACTLY ONE of these 8 actions. Respond with ONLY a valid JSON object (no markdown, no extra text).

Actions and their JSON shape:

1. navigate - user wants to go to a screen. Example: "go to expenses", "show me the ledger"
   {"action": "navigate", "page": "dashboard|quotations|clients|expenses|ledger|profile", "confirmation_text": "..."}

2. create_quotation - user is describing a job to quote. Example: "quotation for Sharma Ji, 80 sq ft wardrobe at 1600 per sq ft"
   {"action": "create_quotation", "description": "<what they said about the job, verbatim or lightly cleaned up>", "confirmation_text": "..."}

3. log_expense - user wants to log money they spent. Example: "log an expense of 500 rupees for Hardware Store"
   {"action": "log_expense", "vendor": "...", "amount": 500, "notes": null, "confirmation_text": "..."}

4. add_client - user wants to save a new client. Example: "add a client named Rohit, phone 98765 43210"
   {"action": "add_client", "client_name": "...", "client_phone": "... or null", "confirmation_text": "..."}

5. add_ledger_entry - user wants to record that someone owes them money. Example: "add to ledger, Sharma owes me 2000 for tiling, due next Friday"
   {"action": "add_ledger_entry", "client_name": "...", "amount": 2000, "ledger_description": "...", "due_date": "YYYY-MM-DD or null if not mentioned", "confirmation_text": "..."}

6. mark_ledger_paid - user wants to mark an existing debt as paid. Example: "mark Sharma's payment as paid"
   {"action": "mark_ledger_paid", "client_name_query": "Sharma", "confirmation_text": "..."}

7. check_balance - user wants to know who owes them money, or how much a specific client owes. Example: "who owes me money", "what does Ramesh owe me", "total udhaar", "Suresh ka balance kitna hai"
   {"action": "check_balance", "client_name_query": "<name mentioned, or null if asking about everyone>", "confirmation_text": "..."}

8. unknown - the command doesn't clearly match any of the above, or audio is unclear
   {"action": "unknown", "confirmation_text": "Sorry, I didn't catch a command I can act on."}

Rules:
1. confirmation_text must be a short, clear, human-readable sentence describing exactly what will happen if confirmed (e.g. "Log a ₹500 expense for Hardware Store" or "Go to Expenses"). For mark_ledger_paid, phrase it as "Mark <name>'s ledger entry as paid".
2. For due_date, only set a specific date if you can confidently resolve it (e.g. "tomorrow", "15th October"); for vague terms like "next Friday" without today's date context, put the phrase as-is in ledger_description instead and leave due_date null.
3. Extract amounts as plain numbers (500, not "500 rupees" or "Rs. 500").
4. If the command is ambiguous or doesn't fit, use "unknown" rather than guessing.

Return ONLY the JSON object, no other text."""

    def _parse_voice_command_response(self, response_text: str) -> Optional[VoiceCommand]:
        try:
            json_text = response_text.strip()
            if json_text.startswith("```"):
                json_text = json_text.split("```")[1]
                if json_text.startswith("json"):
                    json_text = json_text[4:]
                json_text = json_text.strip()

            data_dict = json.loads(json_text)
            return VoiceCommand(**data_dict)
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Failed to parse voice command response: {e}")
            logger.debug(f"Raw response: {response_text}")
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
            "hsn_code": "HSN or SAC code printed next to this item on the bill (or null if not present)",
            "discount": "Discount/coupon amount printed on this line (or null if not present)",
            "taxable_value": "Taxable Value printed on this line (or null if not present)",
            "sgst": "SGST amount printed on this line (or null if not present)",
            "cgst": "CGST amount printed on this line (or null if not present)",
            "igst": "IGST amount printed on this line, for interstate bills (or null if not present)",
            "line_total": "This line's final Total column amount exactly as printed on the bill (or null if not present)"
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
    "order_date": "Order/invoice date printed on the bill, as written (or null)",
    "expense_category": "For an expense/invoice (not a quotation): one of Materials, Labour, Transport, Rent, Utilities, Equipment, Office Supplies, Professional Fees, Other - inferred from the vendor name and items (or null for a quotation)"
}}

Rules:
1. Extract EXACT quantities and rates mentioned (do not round)
2. If rate is in thousands (e.g., "1600 per sq ft"), use exact value
3. If items lack rates, set rate to 0 and mark for manual lookup
4. For a NEW quotation (voice/text describing work to be quoted), leave total_before_gst, gst_amount and grand_total as null - they will be calculated from the items automatically.
   For a bill/receipt/invoice IMAGE that already shows a printed final total (e.g. "Grand Total", "Total", "Amount Payable"), capture that printed amount EXACTLY as grand_total - do not recalculate it from the line items, since bills often include discounts, fees or tax treatments not fully captured in the item list.
5. Detect language and set language_detected field
6. For voice notes: user may say "Quotation for Sharma Ji, 80 sq ft wardrobe at 1,600 per sq ft" — parse exactly
7. If a bill/receipt shows an HSN or SAC code next to an item (common on GST invoices), capture it exactly as printed in that item's hsn_code field. Leave it null if the bill has no HSN/SAC codes.
8. If a bill/receipt shows the seller's business name, GSTIN, or address (usually printed at the top as the "Sold By" / letterhead), capture them in seller_name, seller_gstin, seller_address. Capture billing_address and shipping_address separately if both are printed (common on e-commerce invoices) - leave shipping_address null if only one address is shown. Capture order_date/invoice_date exactly as printed in order_date. Leave any of these null if not present on the bill - do not guess or invent them.
9. If a bill/receipt has a per-line breakdown (Discount, Taxable Value, SGST, CGST or IGST, Total columns), capture each of those exactly as printed in that item's discount, taxable_value, sgst, cgst, igst and line_total fields. These are per-line figures, not the bill's overall totals. The line_total values across all items, summed together, should add up to (or very closely match) the bill's overall grand_total - if your captured figures don't reconcile, re-check the columns before finalizing. Leave any of these fields null if the bill doesn't show that level of detail.
10. For an expense or invoice (data_type is "invoice" or "expense", not "quotation"), set expense_category to your best guess from EXACTLY one of: Materials, Labour, Transport, Rent, Utilities, Equipment, Office Supplies, Professional Fees, Other. Infer it from the seller/vendor name and the items - e.g. a hardware/building-materials shop is Materials, a petrol pump or vehicle-related bill is Transport, a mobile/electricity/internet bill is Utilities. If genuinely unclear, use "Other" rather than guessing wildly. Leave this null for quotations.

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

            if extracted.expense_category and extracted.expense_category not in EXPENSE_CATEGORIES:
                extracted.expense_category = "Other"

            if extracted.gst_percentage is None:
                extracted.gst_percentage = 0.0

            for item in extracted.items:
                item.calculate_amount()

            if extracted.data_type == "quotation":
                # New work being quoted - no existing total to read, so compute it.
                extracted.total_before_gst = sum(item.amount or 0 for item in extracted.items)
                extracted.gst_amount = extracted.total_before_gst * (extracted.gst_percentage / 100)
                extracted.grand_total = extracted.total_before_gst + extracted.gst_amount
            elif extracted.grand_total is None:
                # Invoice/expense extracted from an existing bill - trust the printed
                # total if we got one; only fall back to computing it if we didn't,
                # since re-deriving it from items double-applies GST that's often
                # already baked into the extracted rate.
                extracted.total_before_gst = sum(item.amount or 0 for item in extracted.items)
                extracted.gst_amount = extracted.total_before_gst * (extracted.gst_percentage / 100)
                extracted.grand_total = extracted.total_before_gst + extracted.gst_amount

            logger.info(f"Successfully extracted data: {extracted.data_type}")
            return extracted
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            logger.debug(f"Raw response: {response_text}")
            return None
