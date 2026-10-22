"""Cashfree payment webhook handler for subscription upgrades."""

import json
import logging

from fastapi import Request, Response

from src.services.cashfree import CashfreeClient
from src.services.database import DynamoDBClient
from src.services.whatsapp import WhatsAppClient

logger = logging.getLogger(__name__)


class PaymentHandler:
    """Handles Cashfree payment webhooks with DIP and SRP."""

    def __init__(
        self,
        cashfree_client: CashfreeClient,
        db_client: DynamoDBClient,
        whatsapp_client: WhatsAppClient,
    ):
        self.cashfree = cashfree_client
        self.db = db_client
        self.whatsapp = whatsapp_client

    async def handle_webhook(self, request: Request) -> Response:
        """Process Cashfree payment success webhook."""
        try:
            request_body = await request.body()
            signature = request.headers.get("x-webhook-signature", "")

            # Verify webhook signature
            if not self.cashfree.verify_webhook_signature(request_body.decode(), signature):
                logger.warning("Invalid Cashfree webhook signature")
                return Response(content="Unauthorized", status_code=401)

            # Parse webhook data
            webhook_data = json.loads(request_body)
            result = self.cashfree.process_payment_webhook(webhook_data)

            if not result or result.get("status") != "success":
                logger.warning(f"Payment webhook processing failed: {result}")
                return Response(content="ok", status_code=200)

            # Upgrade user to Pro
            phone_number = result.get("phone_number")
            if phone_number and self.db.upgrade_to_pro(phone_number):
                # Send confirmation message
                self.whatsapp.send_text_message(
                    phone_number,
                    "🎉 Upgrade successful! You now have unlimited bills. Enjoy BillBolo Pro!",
                )
                logger.info(f"User upgraded to Pro: {phone_number}")
            else:
                logger.error(f"Failed to upgrade user: {phone_number}")

            return Response(content="ok", status_code=200)
        except json.JSONDecodeError:
            logger.error("Invalid JSON in payment webhook")
            return Response(content="Bad Request", status_code=400)
        except Exception as e:
            logger.error(f"Payment webhook processing failed: {e}")
            return Response(content="Internal Server Error", status_code=500)


# Factory function for dependency injection
def create_payment_handler() -> PaymentHandler:
    """Create PaymentHandler with all dependencies injected."""
    return PaymentHandler(
        cashfree_client=CashfreeClient(),
        db_client=DynamoDBClient(),
        whatsapp_client=WhatsAppClient(),
    )
