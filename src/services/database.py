"""DynamoDB client for user state management and session tracking."""

import json
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Any:
    """Recursively convert floats to Decimal - DynamoDB's put_item rejects
    native Python float outright."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


class DynamoDBClient:
    """Defensive DynamoDB wrapper with comprehensive error handling."""

    def __init__(self):
        self.dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        self.table = self.dynamodb.Table(settings.dynamodb_table)

    def create_user_profile(
        self,
        phone_number: str,
        business_name: str,
        sheet_id: Optional[str] = None,
    ) -> bool:
        """Create a new user profile with initial 5 free credits."""
        try:
            item = {
                "phone_number": phone_number,
                "created_at": datetime.utcnow().isoformat(),
                "business_name": business_name,
                "credits_used": 0,
                "is_pro": False,
                "sheet_id": sheet_id or "",
                "updated_at": datetime.utcnow().isoformat(),
                "expires_at": int(
                    (datetime.utcnow() + timedelta(days=365)).timestamp()
                ),
            }
            self.table.put_item(Item=item)
            logger.info(f"User profile created for {phone_number}")
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create user profile: {e}")
            return False

    def create_expense(
        self,
        phone_number: str,
        vendor: Optional[str],
        amount: float,
        notes: Optional[str] = None,
        items: Optional[list[dict]] = None,
        seller_name: Optional[str] = None,
        seller_gstin: Optional[str] = None,
        seller_address: Optional[str] = None,
        billing_address: Optional[str] = None,
        shipping_address: Optional[str] = None,
        order_date: Optional[str] = None,
        category: Optional[str] = None,
    ) -> bool:
        """Persist an expense logged via WhatsApp. Stored under a distinct
        partition key (expense#<phone>) so it doesn't collide with the
        user's own profile item, which uses the bare phone_number."""
        try:
            sort_key = f"{datetime.utcnow().isoformat()}#{uuid.uuid4().hex[:8]}"
            self.table.put_item(
                Item={
                    "phone_number": f"expense#{phone_number}",
                    "created_at": sort_key,
                    "vendor": vendor or "",
                    "amount": _to_decimal(amount),
                    "notes": notes or "",
                    "items": _to_decimal(items or []),
                    "seller_name": seller_name or "",
                    "seller_gstin": seller_gstin or "",
                    "seller_address": seller_address or "",
                    "billing_address": billing_address or "",
                    "shipping_address": shipping_address or "",
                    "order_date": order_date or "",
                    "category": category or "Other",
                    "expires_at": int((datetime.utcnow() + timedelta(days=730)).timestamp()),
                }
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create expense: {e}")
            return False

    def list_expenses(self, phone_number: str) -> list[dict]:
        """List all expenses logged via WhatsApp for this phone number, newest first."""
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn",
                ExpressionAttributeValues={":pn": f"expense#{phone_number}"},
                ScanIndexForward=False,
            )
            return response.get("Items", [])
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to list expenses: {e}")
            return []

    def get_user_profile(self, phone_number: str) -> Optional[dict]:
        """Retrieve user profile from DynamoDB."""
        try:
            response = self.table.get_item(
                Key={
                    "phone_number": phone_number,
                    "created_at": self._get_user_created_at(phone_number),
                }
            )
            return response.get("Item")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to get user profile: {e}")
            return None

    def increment_credits_used(self, phone_number: str) -> bool:
        """Increment credit counter for a user."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                logger.warning(f"User profile not found: {phone_number}")
                return False

            self.table.update_item(
                Key={
                    "phone_number": profile["phone_number"],
                    "created_at": profile["created_at"],
                },
                UpdateExpression="SET credits_used = credits_used + :inc, updated_at = :now",
                ExpressionAttributeValues={
                    ":inc": 1,
                    ":now": datetime.utcnow().isoformat(),
                },
            )
            logger.info(f"Credits incremented for {phone_number}")
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to increment credits: {e}")
            return False

    def upgrade_to_pro(self, phone_number: str) -> bool:
        """Upgrade user to Pro tier after payment."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False

            self.table.update_item(
                Key={
                    "phone_number": profile["phone_number"],
                    "created_at": profile["created_at"],
                },
                UpdateExpression="SET is_pro = :true, updated_at = :now, pro_activated_at = :now",
                ExpressionAttributeValues={
                    ":true": True,
                    ":now": datetime.utcnow().isoformat(),
                },
            )
            logger.info(f"User upgraded to Pro: {phone_number}")
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upgrade to Pro: {e}")
            return False

    def update_business_name(self, phone_number: str, business_name: str) -> bool:
        """Set the business name on a pending (placeholder) user profile."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False

            self.table.update_item(
                Key={
                    "phone_number": profile["phone_number"],
                    "created_at": profile["created_at"],
                },
                UpdateExpression="SET business_name = :name, updated_at = :now",
                ExpressionAttributeValues={
                    ":name": business_name,
                    ":now": datetime.utcnow().isoformat(),
                },
            )
            logger.info(f"Business name set for {phone_number}")
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update business name: {e}")
            return False

    def save_session_draft(
        self,
        phone_number: str,
        session_id: str,
        extracted_data: dict,
    ) -> bool:
        """Save an in-progress quotation draft for edit/update."""
        try:
            session_key = f"session#{session_id}"
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False

            self.table.update_item(
                Key={
                    "phone_number": profile["phone_number"],
                    "created_at": profile["created_at"],
                },
                UpdateExpression=f"SET #sk = :data, updated_at = :now",
                ExpressionAttributeNames={"#sk": session_key},
                ExpressionAttributeValues={
                    ":data": json.dumps(extracted_data),
                    ":now": datetime.utcnow().isoformat(),
                },
            )
            logger.info(f"Session draft saved: {session_id}")
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to save session draft: {e}")
            return False

    def get_session_draft(self, phone_number: str, session_id: str) -> Optional[dict]:
        """Retrieve an active session draft for editing."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return None

            session_key = f"session#{session_id}"
            if session_key in profile:
                return json.loads(profile[session_key])
            return None
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to get session draft: {e}")
            return None

    def check_rate_limit(self, identifier: str, limit: int) -> bool:
        """Atomically check and increment a daily rate-limit counter.

        Returns True if the request is allowed (and counts it), False if
        the identifier has hit its daily limit. Fails open (allows the
        request) on DynamoDB errors so an infra hiccup never blocks
        legitimate traffic - this is a soft abuse guard, not a security
        boundary.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        rate_key = f"ratelimit#{identifier}"
        try:
            self.table.update_item(
                Key={"phone_number": rate_key, "created_at": today},
                UpdateExpression=(
                    "SET request_count = if_not_exists(request_count, :zero) + :inc, "
                    "expires_at = :ttl"
                ),
                ConditionExpression=(
                    "attribute_not_exists(request_count) OR request_count < :limit"
                ),
                ExpressionAttributeValues={
                    ":zero": 0,
                    ":inc": 1,
                    ":limit": limit,
                    ":ttl": int((datetime.utcnow() + timedelta(days=2)).timestamp()),
                },
            )
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            logger.error(f"Rate limit check failed for {rate_key}: {e}")
            return True
        except BotoCoreError as e:
            logger.error(f"Rate limit check failed for {rate_key}: {e}")
            return True

    def _get_user_created_at(self, phone_number: str) -> str:
        """Helper to get user's creation timestamp (required for composite key)."""
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn",
                ExpressionAttributeValues={":pn": phone_number},
                Limit=1,
            )
            if response["Items"]:
                return response["Items"][0]["created_at"]
        except (BotoCoreError, ClientError) as e:
            logger.warning(f"Failed to query user creation time: {e}")
        return datetime.utcnow().isoformat()
