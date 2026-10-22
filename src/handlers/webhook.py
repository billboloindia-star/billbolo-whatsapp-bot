"""WhatsApp webhook handler for receiving and processing messages."""

import json
import logging
import uuid
from typing import Optional

from fastapi import Request, Response

from src.config import settings
from src.services.whatsapp import WhatsAppClient
from src.services.ai_extractor import GeminiExtractor
from src.services.pdf_generator import PDFGenerator
from src.services.database import DynamoDBClient
from src.services.sheets import GoogleSheetsClient
from src.services.cashfree import CashfreeClient

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
            # New user - ask for business name
            await self._send_reply(
                phone_number,
                f"Hi! I'm {settings.bot_name}. What's your business name?",
            )
            # Will be created when user replies with business name
        else:
            # Existing user
            if text.lower().startswith("quote:") or text.lower().startswith("q:"):
                # Edit mode: "Change wardrobe rate to 1,700"
                await self._handle_edit_request(phone_number, text)
            else:
                await self._send_reply(
                    phone_number,
                    "Send a voice note or bill image to generate a quotation.",
                )

    async def _handle_audio_message(self, phone_number: str, media_id: str) -> None:
        """Handle audio (voice note) message."""
        # Create user if doesn't exist
        user = self.db.get_user_profile(phone_number)
        if not user:
            self.db.create_user_profile(phone_number, "Unknown Business")
            user = self.db.get_user_profile(phone_number)

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

        # Download and process audio
        audio_bytes = self.whatsapp.download_media(media_id)
        if not audio_bytes:
            await self._send_reply(phone_number, "Failed to download audio. Please try again.")
            return

        # Extract data from audio
        extracted = self.extractor.extract_from_audio(audio_bytes)
        if not extracted:
            await self._send_reply(
                phone_number,
                "Failed to parse the voice note. Please speak clearly with item names and rates.",
            )
            return

        # Generate PDF
        pdf_bytes = self.pdf_gen.generate_quotation(
            extracted,
            {"name": user.get("business_name", "Business")},
            is_pro=user.get("is_pro", False),
        )
        if not pdf_bytes:
            await self._send_reply(phone_number, "Failed to generate PDF. Please try again.")
            return

        # Save to S3 and send back
        pdf_url = await self._save_pdf_to_s3(pdf_bytes, phone_number)
        await self.whatsapp.send_document(
            phone_number,
            pdf_url,
            caption=f"Quotation for {extracted.client_name or 'your client'}",
        )

        # Log to user's sheet
        if user.get("sheet_id"):
            self.sheets.append_quotation_to_sheet(user["sheet_id"], extracted, phone_number)

        # Increment credits
        self.db.increment_credits_used(phone_number)

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

        await self._send_reply(
            phone_number,
            f"Expense logged: ₹{extracted.grand_total:,.2f} - {extracted.notes or 'No description'}",
        )

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
