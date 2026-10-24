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


def from_decimal(value: Any) -> Any:
    """Recursively convert DynamoDB's Decimal back to int/float - needed
    before any DB item (or anything nested inside it, like a quotation's
    items list) goes into a JSON response."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_decimal(v) for v in value]
    return value


def normalize_phone(raw: str) -> Optional[str]:
    """Normalize an Indian phone number to the bot's canonical format - digits
    only, E.164 without the leading '+' (e.g. "919876543210"), the exact
    format the WhatsApp Cloud API sends as message["from"]. This is the
    shared identity key between the WhatsApp bot and the web app, so any
    number entered on the web (any punctuation/spacing/+prefix) has to
    normalize to the same string a real WhatsApp message would carry.
    Returns None if it doesn't look like a valid Indian mobile number."""
    digits = "".join(c for c in (raw or "") if c.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    elif len(digits) == 12 and digits.startswith("91"):
        pass
    else:
        return None
    # A valid Indian mobile number is 91 + a 10-digit number starting 6-9.
    if len(digits) != 12 or digits[2] not in "6789":
        return None
    return digits


def clean_timestamp(composite_created_at: str) -> str:
    """Most entity sort keys are '<iso timestamp>#<id>' so items for the same
    phone_number/second don't collide. Strip the '#<id>' suffix for display -
    the web app just wants a plain timestamp, not DynamoDB's key encoding."""
    return (composite_created_at or "").split("#")[0]


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
        email: Optional[str] = None,
        password_hash: Optional[str] = None,
    ) -> bool:
        """Create a new user profile with initial 5 free credits. email/
        password_hash are only ever set by web app signup - a profile
        created by a first WhatsApp message has neither, and can later be
        "claimed" with a password via update_profile_credentials() so the
        same phone number is one identity on both sides."""
        try:
            item = {
                "phone_number": phone_number,
                "created_at": datetime.utcnow().isoformat(),
                "business_name": business_name,
                "credits_used": 0,
                "is_pro": False,
                "sheet_id": sheet_id or "",
                "email": email or "",
                "password_hash": password_hash or "",
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

    def update_profile_credentials(
        self, phone_number: str, email: Optional[str] = None, password_hash: Optional[str] = None
    ) -> bool:
        """Set/update email and/or password_hash on an existing profile -
        used both to "claim" an existing WhatsApp-only profile with a web
        login, and to change password later."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False
            updates, values, names = [], {}, {}
            if email is not None:
                updates.append("email = :e")
                values[":e"] = email
            if password_hash is not None:
                updates.append("password_hash = :p")
                values[":p"] = password_hash
            if not updates:
                return True
            self.table.update_item(
                Key={"phone_number": profile["phone_number"], "created_at": profile["created_at"]},
                UpdateExpression="SET " + ", ".join(updates),
                ExpressionAttributeValues=values,
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update profile credentials: {e}")
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
    ) -> Optional[dict]:
        """Persist an expense logged via WhatsApp or the web app. Stored
        under a distinct partition key (expense#<phone>) so it doesn't
        collide with the user's own profile item, which uses the bare
        phone_number. Returns the created item (with expense_id), or None
        on failure."""
        try:
            expense_id = uuid.uuid4().hex
            sort_key = f"{datetime.utcnow().isoformat()}#{expense_id[:8]}"
            item = {
                "phone_number": f"expense#{phone_number}",
                "created_at": sort_key,
                "expense_id": expense_id,
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
            self.table.put_item(Item=item)
            return item
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create expense: {e}")
            return None

    def update_expense(
        self, phone_number: str, expense_id: str, vendor: str, amount: float, notes: str = "", category: Optional[str] = None
    ) -> bool:
        expense = next((e for e in self.list_expenses(phone_number) if e.get("expense_id") == expense_id), None)
        if not expense:
            return False
        try:
            self.table.update_item(
                Key={"phone_number": expense["phone_number"], "created_at": expense["created_at"]},
                UpdateExpression="SET vendor = :v, amount = :a, notes = :n, category = :c",
                ExpressionAttributeValues={
                    ":v": vendor,
                    ":a": _to_decimal(amount),
                    ":n": notes,
                    ":c": category or expense.get("category", "Other"),
                },
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update expense: {e}")
            return False

    def delete_expense(self, phone_number: str, expense_id: str) -> bool:
        expense = next((e for e in self.list_expenses(phone_number) if e.get("expense_id") == expense_id), None)
        if not expense:
            return False
        try:
            self.table.delete_item(Key={"phone_number": expense["phone_number"], "created_at": expense["created_at"]})
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to delete expense: {e}")
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
            ledger_id = uuid.uuid4().hex
            sort_key = f"{datetime.utcnow().isoformat()}#{ledger_id[:8]}"
            item = {
                "phone_number": f"ledger#{phone_number}",
                "created_at": sort_key,
                "ledger_id": ledger_id,
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

    def mark_ledger_entry_by_id(self, phone_number: str, ledger_id: str) -> bool:
        """Mark a single ledger entry as paid by its id - the web app's
        version of 'mark as paid' (one entry, picked explicitly in the UI),
        as opposed to the WhatsApp assistant's 'mark this whole client's
        balance as paid' behavior."""
        entry = next((e for e in self.list_ledger_entries(phone_number) if e.get("ledger_id") == ledger_id), None)
        if not entry:
            return False
        try:
            self.table.update_item(
                Key={"phone_number": entry["phone_number"], "created_at": entry["created_at"]},
                UpdateExpression="SET #s = :paid, paid_at = :ts",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":paid": "paid", ":ts": datetime.utcnow().isoformat()},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to mark ledger entry paid: {e}")
            return False

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

    def set_pending_conversion(self, phone_number: str, action: dict) -> bool:
        """Stash a quotation-to-invoice conversion awaiting user confirmation
        (e.g. an ambiguous client name match), independent of the ledger's
        own pending-action field so the two never collide."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False
            payload = dict(action)
            payload["created_at_ts"] = datetime.utcnow().isoformat()
            self.table.update_item(
                Key={"phone_number": profile["phone_number"], "created_at": profile["created_at"]},
                UpdateExpression="SET pending_conversion = :a",
                ExpressionAttributeValues={":a": _to_decimal(payload)},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to set pending conversion: {e}")
            return False

    def get_pending_conversion(self, phone_number: str) -> Optional[dict]:
        """Return the pending conversion for this user, if any and not expired."""
        profile = self.get_user_profile(phone_number)
        if not profile:
            return None
        action = profile.get("pending_conversion")
        if not action:
            return None
        try:
            created = datetime.fromisoformat(action["created_at_ts"])
        except (KeyError, ValueError):
            return None
        if datetime.utcnow() - created > timedelta(minutes=self.PENDING_LEDGER_ACTION_TTL_MINUTES):
            return None
        return action

    def clear_pending_conversion(self, phone_number: str) -> bool:
        """Remove any pending conversion on this user's profile."""
        try:
            profile = self.get_user_profile(phone_number)
            if not profile:
                return False
            self.table.update_item(
                Key={"phone_number": profile["phone_number"], "created_at": profile["created_at"]},
                UpdateExpression="REMOVE pending_conversion",
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to clear pending conversion: {e}")
            return False

    def create_quotation(
        self,
        phone_number: str,
        quote_number: str,
        client_name: str,
        items: list[dict],
        total_before_gst: float,
        gst_amount: float,
        gst_percentage: float,
        grand_total: float,
        discount_amount: Optional[float] = None,
        discount_percent: Optional[float] = None,
        notes: Optional[str] = None,
        ttl_days: Optional[int] = None,
    ) -> Optional[dict]:
        """Persist a generated quotation, via WhatsApp, so it can later be
        looked up and converted to an invoice (Estimates -> Invoice). Stored
        under a distinct partition key (quotation#<phone>), same pattern as
        the ledger. No TTL by default - an open estimate shouldn't silently
        vanish; ttl_days is only passed for throwaway demo data."""
        try:
            sort_key = f"{datetime.utcnow().isoformat()}#{uuid.uuid4().hex[:8]}"
            item = {
                "phone_number": f"quotation#{phone_number}",
                "created_at": sort_key,
                "quote_number": quote_number,
                "client_name": client_name,
                "client_name_lower": client_name.lower(),
                "items": _to_decimal(items),
                "total_before_gst": _to_decimal(total_before_gst),
                "gst_amount": _to_decimal(gst_amount),
                "gst_percentage": _to_decimal(gst_percentage),
                "grand_total": _to_decimal(grand_total),
                "discount_amount": _to_decimal(discount_amount),
                "discount_percent": _to_decimal(discount_percent),
                "notes": notes or "",
                "status": "open",
                "converted_at": "",
            }
            if ttl_days is not None:
                item["expires_at"] = int((datetime.utcnow() + timedelta(days=ttl_days)).timestamp())
            self.table.put_item(Item=item)
            return item
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create quotation: {e}")
            return None

    def list_quotations(self, phone_number: str, status: Optional[str] = None) -> list[dict]:
        """List all quotations generated via WhatsApp for this phone number,
        newest first. Optionally filtered to a status ("open" or "converted")."""
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn",
                ExpressionAttributeValues={":pn": f"quotation#{phone_number}"},
                ScanIndexForward=False,
            )
            items = response.get("Items", [])
            if status:
                items = [i for i in items if i.get("status") == status]
            return items
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to list quotations: {e}")
            return []

    def find_open_quotation(self, phone_number: str, client_name: Optional[str] = None) -> Optional[dict]:
        """Find the most recent open (not yet converted) quotation, optionally
        narrowed to an exact (case-insensitive) client name match. Exact
        match only - any fuzzy disambiguation between similarly-named
        clients must happen upstream, with user confirmation, before this
        is called with a resolved name."""
        open_quotes = self.list_quotations(phone_number, status="open")
        if client_name:
            query_lower = client_name.strip().lower()
            open_quotes = [q for q in open_quotes if q.get("client_name_lower") == query_lower]
        return open_quotes[0] if open_quotes else None

    def mark_quotation_converted(self, phone_number: str, quotation: dict) -> bool:
        """Mark a quotation as converted to an invoice, so it can't be
        converted again."""
        try:
            self.table.update_item(
                Key={"phone_number": quotation["phone_number"], "created_at": quotation["created_at"]},
                UpdateExpression="SET #s = :converted, converted_at = :ts",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":converted": "converted", ":ts": datetime.utcnow().isoformat()},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to mark quotation converted: {e}")
            return False

    def set_quotation_pdf_key(self, quotation: dict, pdf_s3_key: str) -> bool:
        """Attach the S3 key for a quotation's generated PDF, so the web app
        can later re-fetch it via a presigned URL without regenerating it."""
        try:
            self.table.update_item(
                Key={"phone_number": quotation["phone_number"], "created_at": quotation["created_at"]},
                UpdateExpression="SET pdf_s3_key = :k",
                ExpressionAttributeValues={":k": pdf_s3_key},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to set quotation PDF key: {e}")
            return False

    def create_client(self, phone_number: str, name: str, client_phone: str = "", notes: str = "") -> Optional[dict]:
        """Save a client explicitly (via the web app's Add Client form, or a
        WhatsApp 'add a client' voice command) - distinct from client_name
        strings that just appear on ledger entries/quotations."""
        try:
            client_id = uuid.uuid4().hex
            item = {
                "phone_number": f"client#{phone_number}",
                "created_at": f"{datetime.utcnow().isoformat()}#{client_id[:8]}",
                "client_id": client_id,
                "name": name,
                "phone": client_phone,
                "notes": notes,
            }
            self.table.put_item(Item=item)
            return item
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create client: {e}")
            return None

    def list_clients(self, phone_number: str) -> list[dict]:
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn",
                ExpressionAttributeValues={":pn": f"client#{phone_number}"},
                ScanIndexForward=False,
            )
            return response.get("Items", [])
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to list clients: {e}")
            return []

    def delete_client(self, phone_number: str, client_id: str) -> bool:
        client = next((c for c in self.list_clients(phone_number) if c.get("client_id") == client_id), None)
        if not client:
            return False
        try:
            self.table.delete_item(Key={"phone_number": client["phone_number"], "created_at": client["created_at"]})
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to delete client: {e}")
            return False

    def count_this_month(self, phone_number: str, entity_prefix: str) -> int:
        """Count how many items of a given type (expense/quotation/ledger/
        voicecmd) a user has created in the current calendar month - used
        for the web app's free-tier quotas. entity_prefix is the bare
        prefix used elsewhere (e.g. "expense", "quotation")."""
        month = datetime.utcnow().strftime("%Y-%m")
        try:
            response = self.table.query(
                KeyConditionExpression="phone_number = :pn AND begins_with(created_at, :m)",
                ExpressionAttributeValues={":pn": f"{entity_prefix}#{phone_number}", ":m": month},
                Select="COUNT",
            )
            return response.get("Count", 0)
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to count items for quota check ({entity_prefix}): {e}")
            return 0

    def log_voice_command_usage(self, phone_number: str) -> bool:
        """Write a minimal marker item so count_this_month(..., 'voicecmd')
        can rate-limit web app voice classification calls."""
        try:
            sort_key = f"{datetime.utcnow().isoformat()}#{uuid.uuid4().hex[:8]}"
            self.table.put_item(Item={"phone_number": f"voicecmd#{phone_number}", "created_at": sort_key})
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to log voice command usage: {e}")
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
