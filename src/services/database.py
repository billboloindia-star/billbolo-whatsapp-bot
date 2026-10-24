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
        ttl_days: int = 365,
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
                    (datetime.utcnow() + timedelta(days=ttl_days)).timestamp()
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

    def create_ledger_entry(
        self,
        phone_number: str,
        client_name: str,
        amount: float,
        description: Optional[str] = None,
        due_date: Optional[str] = None,
        ttl_days: Optional[int] = None,
    ) -> Optional[dict]:
        """Record that a client owes the business owner money, via WhatsApp.
        Stored under a distinct partition key (ledger#<phone>), same pattern
        as expenses. No TTL by default - an unpaid debt shouldn't silently
        vanish; ttl_days is only passed for throwaway demo data."""
        try:
            sort_key = f"{datetime.utcnow().isoformat()}#{uuid.uuid4().hex[:8]}"
            item = {
                "phone_number": f"ledger#{phone_number}",
                "created_at": sort_key,
                "client_name": client_name,
                "client_name_lower": client_name.lower(),
                "amount": _to_decimal(amount),
                "description": description or "",
                "due_date": due_date or "",
                "status": "owed",
                "paid_at": "",
            }
            if ttl_days is not None:
                item["expires_at"] = int((datetime.utcnow() + timedelta(days=ttl_days)).timestamp())
            self.table.put_item(Item=item)
            return item
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create ledger entry: {e}")
            return None

    def list_ledger_entries(self, phone_number: str, status: Optional[str] = None) -> list[dict]:
        """List all ledger entries for this phone number, newest first.
        Optionally filtered to a status ("owed" or "paid")."""
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn",
                ExpressionAttributeValues={":pn": f"ledger#{phone_number}"},
                ScanIndexForward=False,
            )
            items = response.get("Items", [])
            if status:
                items = [i for i in items if i.get("status") == status]
            return items
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to list ledger entries: {e}")
            return []

    def mark_ledger_entries_paid(self, phone_number: str, client_name: str) -> list[dict]:
        """Mark all unpaid entries for a client as paid (clears their whole
        balance, not just the latest entry - matches how 'mark X as paid'
        is usually meant). Matches by exact client name (case-insensitive)
        only - this is a destructive, money-moving action, so any fuzzy
        disambiguation between similarly-named clients (e.g. 'Ramesh' vs
        'Ramesh Sisodia') must happen upstream, with user confirmation,
        before this is called with the resolved exact name.
        Returns the entries that were marked paid."""
        query_lower = client_name.strip().lower()
        unpaid = self.list_ledger_entries(phone_number, status="owed")
        matches = [e for e in unpaid if e.get("client_name_lower", "") == query_lower]
        paid_now = []
        for entry in matches:
            try:
                self.table.update_item(
                    Key={"phone_number": entry["phone_number"], "created_at": entry["created_at"]},
                    UpdateExpression="SET #s = :paid, paid_at = :ts",
                    ExpressionAttributeNames={"#s": "status"},
                    ExpressionAttributeValues={":paid": "paid", ":ts": datetime.utcnow().isoformat()},
                )
                paid_now.append(entry)
            except (BotoCoreError, ClientError) as e:
                logger.error(f"Failed to mark ledger entry paid: {e}")
        return paid_now

    def get_ledger_balances(self, phone_number: str, client_name_query: Optional[str] = None) -> list[dict]:
        """Aggregate unpaid ledger entries into a per-client balance list,
        sorted by amount owed descending. If client_name_query is given,
        filtered to clients matching it (same fuzzy substring match as
        mark_ledger_entries_paid)."""
        unpaid = self.list_ledger_entries(phone_number, status="owed")
        if client_name_query:
            query_lower = client_name_query.strip().lower()
            unpaid = [
                e
                for e in unpaid
                if query_lower in e.get("client_name_lower", "") or e.get("client_name_lower", "") in query_lower
            ]

        balances: dict[str, dict] = {}
        for entry in unpaid:
            name = entry.get("client_name", "Unknown")
            bucket = balances.setdefault(name, {"client_name": name, "total": Decimal("0"), "entry_count": 0})
            bucket["total"] += entry.get("amount", Decimal("0"))
            bucket["entry_count"] += 1

        return sorted(balances.values(), key=lambda b: b["total"], reverse=True)

    PENDING_LEDGER_ACTION_TTL_MINUTES = 10

    def set_pending_ledger_action(self, phone_number: str, action: dict) -> bool:
        """Stash a ledger action awaiting user confirmation (e.g. an ambiguous
        client name match) on the user's own profile item, so the next text
        message from them can be checked as a reply to it."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False
            payload = dict(action)
            payload["created_at_ts"] = datetime.utcnow().isoformat()
            self.table.update_item(
                Key={"phone_number": profile["phone_number"], "created_at": profile["created_at"]},
                UpdateExpression="SET pending_ledger_action = :a",
                ExpressionAttributeValues={":a": _to_decimal(payload)},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to set pending ledger action: {e}")
            return False

    def get_pending_ledger_action(self, phone_number: str) -> Optional[dict]:
        """Return the pending ledger action for this user, if any and not
        expired (so an old, abandoned confirmation can't hijack an unrelated
        message days later)."""
        profile = self.get_user_profile(phone_number)
        if not profile:
            return None
        action = profile.get("pending_ledger_action")
        if not action:
            return None
        try:
            created = datetime.fromisoformat(action["created_at_ts"])
        except (KeyError, ValueError):
            return None
        if datetime.utcnow() - created > timedelta(minutes=self.PENDING_LEDGER_ACTION_TTL_MINUTES):
            return None
        return action

    def clear_pending_ledger_action(self, phone_number: str) -> bool:
        """Remove any pending ledger action on this user's profile."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False
            self.table.update_item(
                Key={"phone_number": profile["phone_number"], "created_at": profile["created_at"]},
                UpdateExpression="REMOVE pending_ledger_action",
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to clear pending ledger action: {e}")
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
        """Atomically check and increment a monthly rate-limit counter.

        Returns True if the request is allowed (and counts it), False if
        the identifier has hit its limit for the current calendar month.
        Fails open (allows the request) on DynamoDB errors so an infra
        hiccup never blocks legitimate traffic - this is a soft abuse
        guard, not a security boundary.
        """
        this_month = datetime.utcnow().strftime("%Y-%m")
        rate_key = f"ratelimit#{identifier}"
        try:
            self.table.update_item(
                Key={"phone_number": rate_key, "created_at": this_month},
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
                    ":ttl": int((datetime.utcnow() + timedelta(days=35)).timestamp()),
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
