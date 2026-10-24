"""WhatsApp webhook handler for receiving and processing messages."""

import json
import logging
import uuid
from typing import Optional

from fastapi import Request, Response

from src.config import settings
from src.services.whatsapp import WhatsAppClient
from src.services.ai_extractor import GeminiExtractor, ExtractedData
from src.services.pdf_generator import PDFGenerator
from src.services.database import DynamoDBClient
from src.services.sheets import GoogleSheetsClient
from src.services.cashfree import CashfreeClient
from src.services.ledger_assistant import LedgerAssistant

logger = logging.getLogger(__name__)


class WebhookHandler:
    """Orchestrates WhatsApp message processing with dependency injection."""

    def __init__(
        self,
        whatsapp_client: WhatsAppClient,
        extractor: GeminiExtractor,
        pdf_gen: PDFGenerator,
        db_client: DynamoDBClient,
        sheets_client: GoogleSheetsClient,
        cashfree_client: CashfreeClient,
    ):
        self.whatsapp = whatsapp_client
        self.extractor = extractor
        self.pdf_gen = pdf_gen
        self.db = db_client
        self.sheets = sheets_client
        self.cashfree = cashfree_client
        self.ledger = LedgerAssistant(db_client, extractor)

    async def handle_get(self, request: Request) -> Response:
        """Handle webhook verification (GET request from Meta)."""
        try:
            token = request.query_params.get("hub.verify_token")
            challenge = request.query_params.get("hub.challenge")

            if token == settings.whatsapp_webhook_verify_token:
                logger.info("Webhook verified successfully")
                return Response(content=challenge, status_code=200)
            else:
                logger.warning("Invalid webhook verification token")
                return Response(content="Forbidden", status_code=403)
        except Exception as e:
            logger.error(f"Webhook verification failed: {e}")
            return Response(content="Internal Server Error", status_code=500)

    async def handle_post(self, request: Request) -> Response:
        """Handle incoming WhatsApp messages (POST request from Meta)."""
        try:
            body = await request.json()
            logger.debug(f"Received webhook payload: {json.dumps(body)}")

            # Extract message details
            message_obj = self._extract_message(body)
            if not message_obj:
                return Response(content="ok", status_code=200)

            phone_number = message_obj["from"]
            message_type = message_obj["type"]

            # Process message based on type
            if message_type == "text":
                await self._handle_text_message(phone_number, message_obj["text"])
            elif message_type == "audio":
                await self._handle_audio_message(phone_number, message_obj["media_id"])
            elif message_type == "image":
                await self._handle_image_message(phone_number, message_obj["media_id"])
            else:
                await self._send_reply(phone_number, "Sorry, I only process text, audio, and images.")

            return Response(content="ok", status_code=200)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in webhook payload")
            return Response(content="Bad Request", status_code=400)
        except Exception as e:
            logger.error(f"Webhook processing failed: {e}")
            return Response(content="Internal Server Error", status_code=500)

    async def _handle_text_message(self, phone_number: str, text: str) -> None:
        """Handle text message (greeting, commands, edits)."""
        user = self.db.get_user_profile(phone_number)

        if not user:
            # First contact - create a pending profile and ask for business name.
            # The *next* text message from this number is treated as the reply.
            self.db.create_user_profile(phone_number, business_name="")
            await self._send_reply(
                phone_number,
                f"Hi! I'm {settings.bot_name}. What's your business name?",
            )
        elif not user.get("business_name"):
            # This message is the reply to "what's your business name?"
            self.db.update_business_name(phone_number, text.strip())
            await self._send_reply(
                phone_number,
                f"Great, {text.strip()}! Send me a voice note or bill photo to get started.",
            )
        else:
            # Existing user
            if self.db.get_pending_ledger_action(phone_number):
                # Reply to an earlier "did you mean X or Y?" confirmation - always
                # routes through the ledger assistant, ahead of any other mode.
                await self._try_handle_ledger_command(phone_number, text)
            elif self.db.get_pending_conversion(phone_number):
                await self._resolve_pending_conversion(phone_number, text)
            elif text.lower().startswith("quote:") or text.lower().startswith("q:"):
                # Edit mode: "Change wardrobe rate to 1,700"
                await self._handle_edit_request(phone_number, text)
            elif await self._try_handle_ledger_command(phone_number, text):
                pass
            elif await self._try_handle_convert_command(phone_number, text):
                pass
            else:
                await self._generate_quotation(
                    phone_number,
                    user,
                    lambda: self.extractor.extract_from_text(text),
                    not_understood_message=(
                        "I couldn't find item names and rates in that message. "
                        "Try something like 'quotation for Sharma Ji, 80 sq ft wardrobe at 1600 per sq ft', "
                        "or send a voice note or bill image instead."
                    ),
                )

    async def _try_handle_ledger_command(self, phone_number: str, text: str) -> bool:
        """If this text message is a ledger command (add entry/mark paid/check
        balance, or a reply confirming an earlier ambiguous one), handle it
        and return True. Otherwise return False so the caller falls through
        to normal quotation generation."""
        reply = self.ledger.handle(phone_number, text)
        if reply is None:
            return False
        await self._send_reply(phone_number, reply)
        return True

    CONVERT_KEYWORDS = ("convert", "estimate")

    async def _try_handle_convert_command(self, phone_number: str, text: str) -> bool:
        """If this text message asks to convert an open quotation to an
        invoice, handle it and return True. Otherwise return False so the
        caller falls through to normal quotation generation."""
        lower = text.lower()
        looks_like_convert = any(kw in lower for kw in self.CONVERT_KEYWORDS) or (
            "invoice" in lower and ("quotation" in lower or "estimate" in lower or "job" in lower)
        )
        if not looks_like_convert:
            return False

        open_quotes = self.db.list_quotations(phone_number, status="open")
        known_clients = list(dict.fromkeys(q["client_name"] for q in open_quotes))
        command = self.extractor.classify_text_command(text, known_clients=known_clients)
        if not command or command.action != "convert_quotation_to_invoice":
            return False

        candidates = list(dict.fromkeys(command.ambiguous_candidates or []))
        if len(candidates) >= 2:
            self.db.set_pending_conversion(
                phone_number,
                {"candidates": candidates, "due_date": command.due_date, "payment_mode": command.payment_mode},
            )
            await self._send_reply(
                phone_number,
                f"Did you mean {self.ledger.format_candidates(candidates)}? Reply with the name to confirm.",
            )
            return True

        await self._convert_quotation(phone_number, command.client_name_query, command.due_date, command.payment_mode)
        return True

    async def _resolve_pending_conversion(self, phone_number: str, text: str) -> None:
        """Handle a reply to an earlier ambiguous-name conversion confirmation."""
        pending = self.db.get_pending_conversion(phone_number)
        reply_lower = text.strip().lower()
        candidates = pending.get("candidates", [])

        if reply_lower in ("cancel", "no", "neither", "none"):
            self.db.clear_pending_conversion(phone_number)
            await self._send_reply(phone_number, "Cancelled.")
            return

        resolved = next((c for c in candidates if c.lower() == reply_lower), None)
        if not resolved:
            resolved = next((c for c in candidates if reply_lower in c.lower() or c.lower() in reply_lower), None)
        if not resolved:
            resolved = text.strip()

        self.db.clear_pending_conversion(phone_number)
        await self._convert_quotation(phone_number, resolved, pending.get("due_date"), pending.get("payment_mode"))

    async def _convert_quotation(
        self, phone_number: str, client_name_query: Optional[str], due_date: Optional[str], payment_mode: Optional[str]
    ) -> None:
        """Look up an open quotation (most recent, or by exact client name)
        and generate a real invoice from it - same client, items and
        totals, no re-entering anything."""
        quotation = self.db.find_open_quotation(phone_number, client_name_query)
        if not quotation:
            target = f" for {client_name_query}" if client_name_query else ""
            await self._send_reply(phone_number, f"I couldn't find an open quotation{target} to convert.")
            return

        user = self.db.get_user_profile(phone_number)
        extracted = ExtractedData(
            data_type="invoice",
            client_name=quotation["client_name"],
            items=quotation.get("items", []),
            notes=quotation.get("notes") or None,
            gst_percentage=float(quotation.get("gst_percentage") or 0),
            discount_amount=float(quotation["discount_amount"]) if quotation.get("discount_amount") else None,
            discount_percent=float(quotation["discount_percent"]) if quotation.get("discount_percent") else None,
            total_before_gst=float(quotation.get("total_before_gst") or 0),
            gst_amount=float(quotation.get("gst_amount") or 0),
            grand_total=float(quotation.get("grand_total") or 0),
            due_date=due_date,
            payment_mode=payment_mode,
        )

        doc_ref = self.pdf_gen.build_reference(extracted, "invoice")
        pdf_bytes = self.pdf_gen.generate_invoice(
            extracted,
            {"name": user.get("business_name", "Business") if user else "Business"},
            is_pro=user.get("is_pro", False) if user else False,
            doc_ref=doc_ref,
        )
        if not pdf_bytes:
            await self._send_reply(phone_number, "Failed to generate the invoice. Please try again.")
            return

        pdf_url = await self._save_pdf_to_s3(pdf_bytes, phone_number)
        await self.whatsapp.send_document(
            phone_number,
            pdf_url,
            caption=f"Invoice for {quotation['client_name']} (converted from {quotation['quote_number']})",
        )
        self.db.mark_quotation_converted(phone_number, quotation)

    async def _handle_audio_message(self, phone_number: str, media_id: str) -> None:
        """Handle audio (voice note) message."""
        user = self.db.get_user_profile(phone_number)
        if not user:
            self.db.create_user_profile(phone_number, "Unknown Business")
            user = self.db.get_user_profile(phone_number)

        audio_bytes = self.whatsapp.download_media(media_id)
        if not audio_bytes:
            await self._send_reply(phone_number, "Failed to download audio. Please try again.")
            return

        await self._generate_quotation(
            phone_number,
            user,
            lambda: self.extractor.extract_from_audio(audio_bytes),
            not_understood_message="Failed to parse the voice note. Please speak clearly with item names and rates.",
        )

    async def _generate_quotation(self, phone_number: str, user: dict, extract_fn, not_understood_message: str) -> None:
        """Shared quotation generation flow: credit check, extract, PDF, send, log, bill."""
        # Check credits
        if not user.get("is_pro") and user.get("credits_used", 0) >= settings.free_credits:
            payment_link = self.cashfree.create_payment_link(
                phone_number, settings.pro_monthly_price_inr
            )
            if payment_link:
                await self._send_reply(
                    phone_number,
                    f"You've used your {settings.free_credits} free credits! "
                    f"Unlock unlimited bills for ₹{settings.pro_monthly_price_inr}/month: {payment_link.payment_link_url}",
                )
            return

        extracted = extract_fn()
        if not extracted or not extracted.items:
            await self._send_reply(phone_number, not_understood_message)
            return

        is_invoice = extracted.data_type == "invoice"
        gen_fn = self.pdf_gen.generate_invoice if is_invoice else self.pdf_gen.generate_quotation
        doc_type = "invoice" if is_invoice else "quotation"
        doc_ref = self.pdf_gen.build_reference(extracted, doc_type)
        pdf_bytes = gen_fn(
            extracted,
            {"name": user.get("business_name", "Business")},
            is_pro=user.get("is_pro", False),
            doc_ref=doc_ref,
        )
        if not pdf_bytes:
            await self._send_reply(phone_number, "Failed to generate PDF. Please try again.")
            return

        pdf_url = await self._save_pdf_to_s3(pdf_bytes, phone_number)
        doc_label = "Invoice" if is_invoice else "Quotation"
        await self.whatsapp.send_document(
            phone_number,
            pdf_url,
            caption=f"{doc_label} for {extracted.client_name or 'your client'}",
        )

        if user.get("sheet_id"):
            self.sheets.append_quotation_to_sheet(user["sheet_id"], extracted, phone_number)

        self.db.increment_credits_used(phone_number)

        if not is_invoice and extracted.client_name:
            # Persist so it can later be looked up and converted to an
            # invoice (Estimates -> Invoice) - invoices created directly
            # aren't estimates, so they're not saved here.
            self.db.create_quotation(
                phone_number,
                quote_number=doc_ref,
                client_name=extracted.client_name,
                items=[item.model_dump() for item in extracted.items],
                total_before_gst=extracted.total_before_gst or 0,
                gst_amount=extracted.gst_amount or 0,
                gst_percentage=extracted.gst_percentage or 0,
                grand_total=extracted.grand_total or 0,
                discount_amount=extracted.discount_amount,
                discount_percent=extracted.discount_percent,
                notes=extracted.notes,
            )

    async def _handle_image_message(self, phone_number: str, media_id: str) -> None:
        """Handle image (bill/receipt) message."""
        # Similar flow to audio: download, extract, generate PDF, send back
        user = self.db.get_user_profile(phone_number)
        if not user:
            self.db.create_user_profile(phone_number, "Unknown Business")

        # Check credits
        if not user.get("is_pro") and user.get("credits_used", 0) >= settings.free_credits:
            await self._send_reply(
                phone_number,
                f"You've used your {settings.free_credits} free credits!",
            )
            return

        # Download and process image
        image_bytes = self.whatsapp.download_media(media_id)
        if not image_bytes:
            await self._send_reply(phone_number, "Failed to download image.")
            return

        # Extract data from image
        extracted = self.extractor.extract_from_image(image_bytes)
        if not extracted:
            await self._send_reply(phone_number, "Failed to parse the bill image.")
            return

        # Log as expense
        if user.get("sheet_id"):
            self.sheets.append_expense_to_sheet(user["sheet_id"], extracted, phone_number)

        self.db.create_expense(
            phone_number,
            vendor=extracted.seller_name or extracted.client_name,
            amount=extracted.grand_total or 0,
            notes=extracted.notes,
            items=[item.model_dump() for item in extracted.items],
            seller_name=extracted.seller_name,
            seller_gstin=extracted.seller_gstin,
            seller_address=extracted.seller_address,
            billing_address=extracted.billing_address,
            shipping_address=extracted.shipping_address,
            order_date=extracted.order_date,
            category=extracted.expense_category,
        )

        if extracted.items:
            lines = []
            for item in extracted.items:
                if not item.item_name:
                    continue
                line = f"- {item.item_name}: ₹{item.display_amount():,.2f}"
                breakdown = []
                if item.discount:
                    breakdown.append(f"discount ₹{item.discount:,.2f}")
                if item.taxable_value is not None:
                    breakdown.append(f"taxable ₹{item.taxable_value:,.2f}")
                if item.sgst:
                    breakdown.append(f"SGST ₹{item.sgst:,.2f}")
                if item.cgst:
                    breakdown.append(f"CGST ₹{item.cgst:,.2f}")
                if item.igst:
                    breakdown.append(f"IGST ₹{item.igst:,.2f}")
                if breakdown:
                    line += f"\n  ({', '.join(breakdown)})"
                lines.append(line)
            itemized = "\n".join(lines)
            category_line = f"\nCategory: {extracted.expense_category}" if extracted.expense_category else ""
            reply = f"Expense logged: ₹{extracted.grand_total:,.2f}\n{itemized}{category_line}"
        else:
            description = extracted.seller_name or extracted.notes or "No description"
            category_suffix = f" ({extracted.expense_category})" if extracted.expense_category else ""
            reply = f"Expense logged: ₹{extracted.grand_total:,.2f} - {description}{category_suffix}"

        await self._send_reply(phone_number, reply)

        self.db.increment_credits_used(phone_number)

    async def _handle_edit_request(self, phone_number: str, edit_text: str) -> None:
        """Handle edit request for a previously generated quotation."""
        await self._send_reply(
            phone_number,
            "Edit mode coming soon! For now, send a new voice note with the updated details.",
        )

    async def _send_reply(self, phone_number: str, message: str) -> bool:
        """Send a reply message to the user."""
        return self.whatsapp.send_text_message(phone_number, message)

    async def _save_pdf_to_s3(self, pdf_bytes: bytes, phone_number: str) -> str:
        """Save PDF to S3 and return shareable URL."""
        # TODO: Implement S3 upload with boto3
        # For now, return placeholder
        import hashlib

        pdf_hash = hashlib.md5(pdf_bytes).hexdigest()[:8]
        return f"https://{settings.s3_bucket}.s3.amazonaws.com/{phone_number}/quotation_{pdf_hash}.pdf"

    def _extract_message(self, webhook_body: dict) -> Optional[dict]:
        """Extract message data from WhatsApp webhook payload."""
        try:
            messages = webhook_body.get("entry", [{}])[0].get("changes", [{}])[0].get("value", {}).get("messages", [])
            if not messages:
                return None

            msg = messages[0]
            from_number = msg.get("from")
            msg_type = msg.get("type")

            result = {"from": from_number, "type": msg_type}

            if msg_type == "text":
                result["text"] = msg.get("text", {}).get("body", "")
            elif msg_type == "audio":
                result["media_id"] = msg.get("audio", {}).get("id")
            elif msg_type == "image":
                result["media_id"] = msg.get("image", {}).get("id")

            return result
        except (KeyError, IndexError) as e:
            logger.error(f"Failed to extract message from webhook: {e}")
            return None


# Factory function for dependency injection
def create_webhook_handler() -> WebhookHandler:
    """Create WebhookHandler with all dependencies injected."""
    return WebhookHandler(
        whatsapp_client=WhatsAppClient(),
        extractor=GeminiExtractor(),
        pdf_gen=PDFGenerator(),
        db_client=DynamoDBClient(),
        sheets_client=GoogleSheetsClient(),
        cashfree_client=CashfreeClient(),
    )
