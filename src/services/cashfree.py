"""Cashfree Payment Links API integration for subscription upgrades."""

import hashlib
import hmac
import json
import logging
from typing import Optional

import requests
from pydantic import BaseModel

from src.config import settings

logger = logging.getLogger(__name__)


class PaymentLink(BaseModel):
    """Represents a Cashfree payment link."""

    order_id: str
    amount: float
    customer_phone: str
    customer_email: Optional[str] = None
    description: str
    payment_link_url: Optional[str] = None


class CashfreeClient:
    """Cashfree Payment Links API client (stub for integration)."""

    def __init__(self):
        self.api_key = settings.cashfree_api_key
        self.secret_key = settings.cashfree_secret_key
        self.webhook_secret = settings.cashfree_webhook_secret
        self.api_url = "https://api.cashfree.com/pg/orders"

    def create_payment_link(
        self,
        phone_number: str,
        amount: float,
        plan_type: str = "monthly",
    ) -> Optional[PaymentLink]:
        """Create a Cashfree payment link for plan upgrade."""
        try:
            order_id = f"billbolo#{phone_number}#{int(__import__('time').time())}"

            payload = {
                "order_id": order_id,
                "order_amount": amount,
                "order_currency": "INR",
                "customer_details": {
                    "customer_phone": phone_number,
                },
                "order_meta": {
                    "return_url": f"https://yourbot.example.com/payment/success?order_id={order_id}",
                    "notify_url": f"https://yourbot.example.com/payments/webhook",
                },
                "order_note": f"BillBolo {plan_type} subscription",
            }

            # TODO: Implement actual Cashfree API call
            logger.info(f"Payment link created for {phone_number}: {order_id}")

            return PaymentLink(
                order_id=order_id,
                amount=amount,
                customer_phone=phone_number,
                description=f"BillBolo {plan_type} subscription",
                payment_link_url=f"https://link.cashfree.com/{order_id}",  # Placeholder
            )
        except Exception as e:
            logger.error(f"Failed to create payment link: {e}")
            return None

    def verify_webhook_signature(self, request_body: str, signature: str) -> bool:
        """Verify Cashfree webhook signature using HMAC."""
        try:
            computed_signature = hmac.new(
                self.webhook_secret.encode(),
                request_body.encode(),
                hashlib.sha256,
            ).hexdigest()

            return hmac.compare_digest(computed_signature, signature)
        except Exception as e:
            logger.error(f"Failed to verify webhook signature: {e}")
            return False

    def process_payment_webhook(self, webhook_data: dict) -> Optional[dict]:
        """Process payment success webhook from Cashfree."""
        try:
            order_id = webhook_data.get("order_id")
            order_status = webhook_data.get("order_status")

            if order_status == "PAID":
                # Extract phone_number from order_id (format: billbolo#<phone>#<timestamp>)
                phone_number = order_id.split("#")[1] if "#" in order_id else None

                logger.info(f"Payment confirmed for {phone_number}: {order_id}")
                return {
                    "status": "success",
                    "phone_number": phone_number,
                    "order_id": order_id,
                }
            else:
                logger.warning(f"Payment failed: {order_id} - Status: {order_status}")
                return {"status": "failed", "order_id": order_id}
        except Exception as e:
            logger.error(f"Failed to process payment webhook: {e}")
            return None
