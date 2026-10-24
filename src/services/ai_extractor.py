"""Gemini Flash API multimodal parser for extracting structured data from audio/images."""

import json
import logging
from datetime import datetime
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
    # add_ledger_entry, mark_ledger_paid - only set when a list of known clients was given as context
    ambiguous_candidates: Optional[list[str]] = Field(
        None,
        description="If the named client could plausibly refer to 2+ of the known clients given as context "
        "(or to one of them vs. a new different person), list those candidate names here instead of "
        "guessing - the app will ask the user to confirm which one they meant",
    )


class LineItem(BaseModel):
    """A single line item in a quotation/invoice."""

    item_name: str = Field(..., description="Name of the item/service")
    quantity: float = Field(..., description="Quantity")
    rate: float = Field(..., description="Rate per unit in INR")
    unit: str = Field(default="qty", description="Unit of measurement (sq ft, hrs, etc)")
    amount: Optional[float] = Field(None, description="Total amount (auto-calculated)")
    hsn_code: Optional[str] = Field(None, description="HSN/SAC code, if printed on the bill")
    discount: Optional[float] = Field(
        None, description="Flat discount on this line, in INR - either printed on a bill, or requested when creating a new quotation"
    )
    discount_percent: Optional[float] = Field(
        None, description="Percentage discount requested on this line when creating a new quotation (e.g. '10% off this item')"
    )
    taxable_value: Optional[float] = Field(None, description="Value this line's tax is calculated on, if printed on a bill")
    sgst: Optional[float] = Field(None, description="SGST amount for this line, if printed on a bill")
    cgst: Optional[float] = Field(None, description="CGST amount for this line, if printed on a bill")
    igst: Optional[float] = Field(None, description="IGST amount for this line (interstate), if printed on a bill")
    line_total: Optional[float] = Field(None, description="Final amount for this line as printed on a bill, after discount and tax")
    item_date: Optional[str] = Field(
        None, description="This line's own transaction date (YYYY-MM-DD), only for a cumulative/periodic bill covering multiple dated transactions - null for a normal single-job quotation/invoice"
    )

    def calculate_amount(self) -> float:
        """Calculate total amount for this item, net of any discount -
        a flat discount if given, else one computed from discount_percent."""
        gross = self.quantity * self.rate
        discount = self.discount
        if discount is None and self.discount_percent:
            discount = gross * (self.discount_percent / 100)
            self.discount = discount
        self.amount = gross - (discount or 0)
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
    discount_percent: Optional[float] = Field(
        None, description="Percentage discount on the whole quotation/bill, e.g. '10% off the total' (not per-item)"
    )
    discount_amount: Optional[float] = Field(
        None, description="Flat discount on the whole quotation/bill, in INR - either requested, or printed on a bill as an overall discount"
    )
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
    due_date: Optional[str] = Field(None, description="For an invoice: payment due date, if mentioned (YYYY-MM-DD if determinable, else as said) - null for a quotation")
    payment_mode: Optional[str] = Field(None, description="For an invoice: how payment should be/was made - Cash, UPI, Bank Transfer, Cheque, Card - only if mentioned")
    amount_paid: Optional[float] = Field(None, description="For an invoice: advance or partial amount already received, if mentioned (e.g. 'paid 5000 advance') - null if nothing paid yet")
    period_from: Optional[str] = Field(None, description="Start date of the billing period, only for a cumulative/periodic bill covering multiple dated transactions - null otherwise")
    period_to: Optional[str] = Field(None, description="End date of the billing period, only for a cumulative/periodic bill - null otherwise")


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
        self, audio_bytes: bytes, audio_mime_type: str = "audio/webm", known_clients: Optional[list[str]] = None
    ) -> Optional[VoiceCommand]:
        """Classify a short spoken voice command into one of the app's fixed set of actions.
        Works in whatever language the person spoke - English, Hindi, Hinglish, etc."""
        try:
            prompt = self._build_voice_command_prompt(known_clients)
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

    def transcribe_audio(self, audio_bytes: bytes, audio_mime_type: str = "audio/webm") -> Optional[str]:
        """Plain transcription of a short recording, translated to English if
        spoken in another language. Used for a one-word/short-phrase reply
        (e.g. a name, confirming 'yes'/'cancel') where we just need the text,
        not full command classification."""
        try:
            prompt = (
                "Transcribe this short audio clip. If spoken in Hindi or another Indian language, "
                "translate it to English. Respond with ONLY the transcribed/translated text, "
                "nothing else - no quotes, no explanation."
            )
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[types.Part.from_bytes(data=audio_bytes, mime_type=audio_mime_type), prompt],
            )
            return (response.text or "").strip()
        except Exception as e:
            logger.error(f"Failed to transcribe audio: {e}")
            return None

    def classify_text_command(self, text: str, known_clients: Optional[list[str]] = None) -> Optional[VoiceCommand]:
        """Classify a short text message into one of the app's fixed set of actions.
        Same classifier/prompt as classify_voice_command, just text input - used for
        WhatsApp ledger commands ('Ramesh owes me 2000', 'mark Ramesh as paid').

        known_clients, if given, lets the model resolve a name against the
        business's actual existing clients (e.g. matching 'Ramesh' to an
        existing 'Ramesh Sisodia') or flag it as ambiguous between two of
        them, rather than the app doing blind substring matching."""
        try:
            prompt = self._build_voice_command_prompt(known_clients)
            response = self.client.models.generate_content(
                model=self.MODEL_NAME,
                contents=[text, prompt],
            )
            return self._parse_voice_command_response(response.text)
        except Exception as e:
            logger.error(f"Failed to classify text command: {e}")
            return None

    def _build_voice_command_prompt(self, known_clients: Optional[list[str]] = None) -> str:
        known_clients_block = ""
        if known_clients:
            names = ", ".join(sorted(set(known_clients)))
            known_clients_block = f"""

This business already has these clients on record: {names}.
Follow these rules EXACTLY when the command names a client - getting this wrong means money gets attributed to the wrong person, so be conservative:
1. If the name in the command is an EXACT match (case-insensitive) to one known client, use that known client's name as written in the list.
2. If the name in the command is a strict SHORTER/PARTIAL form of exactly ONE known client (e.g. "Ramesh" is the first word of "Ramesh Sisodia" and no other known client starts with "Ramesh"), match it to that one known client.
3. If the name in the command is a strict SHORTER/PARTIAL form of TWO OR MORE known clients (e.g. "Ramesh" when both "Ramesh Sisodia" and "Ramesh Verma" exist), do NOT guess - set ambiguous_candidates to those matching known-client names, and still put your best single guess in client_name/client_name_query as a fallback.
4. If the name in the command is a COMPLETE, SPECIFIC name (e.g. "Ramesh Verma") that does not exactly match any known client, treat it as a genuinely different, new person - even if a known client shares part of the name (e.g. "Ramesh Sisodia" also starting with "Ramesh"). Do NOT silently substitute a known client's name for a different specific name the user actually said. Use the name exactly as the user said it, and leave ambiguous_candidates null.
5. Never invent or assume a match that changes what the user explicitly said unless rule 2 or 3 applies."""

        return """You are a voice command classifier for BillBolo, a business app for Indian contractors and freelancers. The user may speak in English, Hindi, Bengali, Tamil, Telugu, Marathi, Hinglish, or a mix of English and any Indian language.""" + known_clients_block + """

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
   {"action": "add_ledger_entry", "client_name": "...", "amount": 2000, "ledger_description": "...", "due_date": "YYYY-MM-DD or null if not mentioned", "ambiguous_candidates": null, "confirmation_text": "..."}

6. mark_ledger_paid - user wants to mark an existing debt as paid. Example: "mark Sharma's payment as paid"
   {"action": "mark_ledger_paid", "client_name_query": "Sharma", "ambiguous_candidates": null, "confirmation_text": "..."}

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
        today = datetime.now().strftime("%Y-%m-%d (%A)")
        return f"""You are a professional bill/quotation extraction assistant for Indian businesses.

Today's date is {today}. Use this to resolve relative dates like "due in 15 days", "next Friday", or a date mentioned without a year.

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
            "discount": "Flat discount on this line in INR - printed on a bill, OR a flat rupee discount requested for a new quotation line (or null)",
            "discount_percent": "Percentage discount requested on this line for a NEW quotation, e.g. 10 for '10% off this item' (or null)",
            "taxable_value": "Taxable Value printed on this line (or null if not present)",
            "sgst": "SGST amount printed on this line (or null if not present)",
            "cgst": "CGST amount printed on this line (or null if not present)",
            "igst": "IGST amount printed on this line, for interstate bills (or null if not present)",
            "line_total": "This line's final Total column amount exactly as printed on the bill (or null if not present)",
            "item_date": "This line's own transaction date as YYYY-MM-DD, ONLY for a cumulative/periodic bill with multiple dated transactions (or null)"
        }}
    ],
    "notes": "Payment terms, delivery details, etc (or null)",
    "gst_percentage": 18,
    "discount_percent": "Percentage discount on the WHOLE quotation/bill (not per item), e.g. 5 for '5% off the total' (or null)",
    "discount_amount": "Flat discount on the WHOLE quotation/bill in INR, requested or printed as an overall discount (or null)",
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
    "expense_category": "For an expense/invoice (not a quotation): one of Materials, Labour, Transport, Rent, Utilities, Equipment, Office Supplies, Professional Fees, Other - inferred from the vendor name and items (or null for a quotation)",
    "due_date": "For a NEW invoice: payment due date if mentioned, e.g. 'due in 15 days' or 'due by 10th November' (YYYY-MM-DD if determinable, else as said, or null)",
    "payment_mode": "For a NEW invoice: Cash, UPI, Bank Transfer, Cheque, or Card, only if explicitly mentioned (or null)",
    "amount_paid": "For a NEW invoice: an advance or partial amount already received, e.g. 'he already paid 5000 advance' (or null if nothing mentioned as paid)",
    "period_from": "Start date of the billing period (YYYY-MM-DD), ONLY if this is a cumulative/periodic bill covering multiple dated transactions for a repeat client (or null)",
    "period_to": "End date of the billing period (YYYY-MM-DD), ONLY for a cumulative/periodic bill (or null)"
}}

Rules:
1. Extract EXACT quantities and rates mentioned (do not round)
2. If rate is in thousands (e.g., "1600 per sq ft"), use exact value
3. If items lack rates, set rate to 0 and mark for manual lookup
4. For a NEW document being created from a voice note or text description (a quotation OR an invoice you're billing for directly - e.g. "invoice Ramesh for the cabinet work, 5000 rupees"), leave total_before_gst, gst_amount and grand_total as null - they will be calculated from the items automatically, same as a quotation.
   For a bill/receipt/invoice IMAGE that already shows a printed final total (e.g. "Grand Total", "Total", "Amount Payable"), capture that printed amount EXACTLY as grand_total - do not recalculate it from the line items, since bills often include discounts, fees or tax treatments not fully captured in the item list.
5. Detect language and set language_detected field
6. For voice notes: user may say "Quotation for Sharma Ji, 80 sq ft wardrobe at 1,600 per sq ft" — parse exactly
7. If a bill/receipt shows an HSN or SAC code next to an item (common on GST invoices), capture it exactly as printed in that item's hsn_code field. Leave it null if the bill has no HSN/SAC codes.
8. If a bill/receipt shows the seller's business name, GSTIN, or address (usually printed at the top as the "Sold By" / letterhead), capture them in seller_name, seller_gstin, seller_address. Capture billing_address and shipping_address separately if both are printed (common on e-commerce invoices) - leave shipping_address null if only one address is shown. Capture order_date/invoice_date exactly as printed in order_date. Leave any of these null if not present on the bill - do not guess or invent them.
9. If a bill/receipt has a per-line breakdown (Discount, Taxable Value, SGST, CGST or IGST, Total columns), capture each of those exactly as printed in that item's discount, taxable_value, sgst, cgst, igst and line_total fields. These are per-line figures, not the bill's overall totals. The line_total values across all items, summed together, should add up to (or very closely match) the bill's overall grand_total - if your captured figures don't reconcile, re-check the columns before finalizing. Leave any of these fields null if the bill doesn't show that level of detail.
10. For an expense or invoice (data_type is "invoice" or "expense", not "quotation"), set expense_category to your best guess from EXACTLY one of: Materials, Labour, Transport, Rent, Utilities, Equipment, Office Supplies, Professional Fees, Other. Infer it from the seller/vendor name and the items - e.g. a hardware/building-materials shop is Materials, a petrol pump or vehicle-related bill is Transport, a mobile/electricity/internet bill is Utilities. If genuinely unclear, use "Other" rather than guessing wildly. Leave this null for quotations.
11. For a NEW quotation or invoice (voice/text), discounts can be mentioned per-item or for the whole job - tell these apart carefully:
    - A discount mentioned right after a specific item ("wardrobe 80 sq ft at 1600, give 10% off that" or "500 off the cabinet") belongs on that item's line: set discount_percent or discount on the matching item, not the document-level fields.
    - A discount mentioned for the job as a whole ("give him 10% off the total", "flat 1000 off everything", "overall discount of 5%") belongs at the document level: set the top-level discount_percent or discount_amount, and leave every item's discount/discount_percent null.
    - If no discount is mentioned anywhere, leave all discount fields null - do not invent one.
    - Prefer a percentage field when the user said "%", and a flat amount field when they said a rupee figure. Never set both discount and discount_percent on the same item, or both discount_amount and discount_percent at the document level - pick the one that matches what was actually said.
12. For a NEW invoice specifically (data_type "invoice", created from a voice note or text, not parsed from an image), also capture due_date, payment_mode, and amount_paid if mentioned - e.g. "invoice Ramesh for the cabinet work, 5000 rupees, due in 15 days, he already paid 2000 advance" sets due_date, amount_paid=2000. Leave any of these null if not mentioned - do not invent a due date or payment mode that wasn't said.
13. A cumulative/periodic bill is a special kind of invoice for a repeat client covering MULTIPLE separate transactions over a date range, rather than one job - e.g. "cumulative bill for Ramesh from 1st January to 16th February: Jan 1st, cement 10 bags at 350; Feb 2nd, tiles 20 pieces at 80". Only use this shape when the user clearly describes several dated transactions being billed together:
    - Set period_from and period_to at the document level.
    - Set item_date on EVERY item to that transaction's own date.
    - Leave period_from, period_to and item_date null for an ordinary single-job quotation or invoice - most invoices are NOT cumulative bills.

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

            if extracted.data_type == "quotation" or extracted.grand_total is None:
                # New work being quoted (always computed), or an invoice/expense
                # with no printed total we could trust directly - compute it,
                # applying any whole-bill discount before GST.
                subtotal = sum(item.amount or 0 for item in extracted.items)
                if extracted.discount_amount is None and extracted.discount_percent:
                    extracted.discount_amount = subtotal * (extracted.discount_percent / 100)
                if extracted.discount_amount:
                    subtotal -= extracted.discount_amount
                extracted.total_before_gst = subtotal
                extracted.gst_amount = extracted.total_before_gst * (extracted.gst_percentage / 100)
                extracted.grand_total = extracted.total_before_gst + extracted.gst_amount

            logger.info(f"Successfully extracted data: {extracted.data_type}")
            return extracted
        except (json.JSONDecodeError, ValidationError) as e:
            logger.error(f"Failed to parse Gemini response: {e}")
            logger.debug(f"Raw response: {response_text}")
            return None
