"""DynamoDB client for user state management and session tracking."""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

logger = logging.getLogger(__name__)


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
