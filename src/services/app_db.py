"""DynamoDB client for the web app (users, clients, quotations, expenses, ledger).

Single-table design, separate from the WhatsApp bot's table (database.py) which
stays phone-number-keyed for the (currently paused) v2 bot work. Here everything
is keyed by user_id, with a sort_key marking the entity type:

    PK=user_id, SK=PROFILE                       -> account record
    PK=user_id, SK=CLIENT#<client_id>             -> saved client
    PK=user_id, SK=QUOTATION#<created_at>#<id>     -> quotation history
    PK=user_id, SK=EXPENSE#<created_at>#<id>       -> expense log
    PK=user_id, SK=LEDGER#<created_at>#<id>        -> udhaar/credit entry

GSI-Email (hash key: email) and GSI-Phone (hash key: phone_number) are sparse -
only PROFILE items carry those attributes, so only PROFILE items appear in them.
Used for login lookup by either identifier.
"""

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

logger = logging.getLogger(__name__)


def _to_decimal(value: Any) -> Any:
    """Recursively convert floats to Decimal - DynamoDB's put_item/update_item
    rejects native Python float outright."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_decimal(v) for v in value]
    return value


def _from_decimal(value: Any) -> Any:
    """Recursively convert DynamoDB's Decimal back to int/float for JSON responses."""
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, dict):
        return {k: _from_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_decimal(v) for v in value]
    return value


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AppDBClient:
    """Defensive DynamoDB wrapper for the web app's user-owned data."""

    def __init__(self):
        self.dynamodb = boto3.resource("dynamodb", region_name=settings.aws_region)
        self.table = self.dynamodb.Table(settings.app_table)

    # ---- Users ----------------------------------------------------------

    def create_user(
        self,
        email: str,
        phone_number: str,
        password_hash: str,
        business_name: str,
    ) -> Optional[str]:
        """Create a new user account. Returns the new user_id, or None if the
        email or phone is already registered."""
        try:
            if self.get_user_by_email(email) is not None:
                logger.warning(f"Signup attempted with existing email: {email}")
                return None
            if self.get_user_by_phone(phone_number) is not None:
                logger.warning(f"Signup attempted with existing phone: {phone_number}")
                return None

            user_id = str(uuid.uuid4())
            self.table.put_item(
                Item={
                    "user_id": user_id,
                    "sort_key": "PROFILE",
                    "email": email,
                    "phone_number": phone_number,
                    "password_hash": password_hash,
                    "business_name": business_name,
                    "created_at": _now(),
                },
                ConditionExpression="attribute_not_exists(user_id)",
            )
            logger.info(f"User created: {user_id}")
            return user_id
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create user: {e}")
            return None

    def get_user_by_id(self, user_id: str) -> Optional[dict]:
        try:
            response = self.table.get_item(Key={"user_id": user_id, "sort_key": "PROFILE"})
            return response.get("Item")
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to get user by id: {e}")
            return None

    def get_user_by_email(self, email: str) -> Optional[dict]:
        try:
            response = self.table.query(
                IndexName="GSI-Email",
                KeyConditionExpression=Key("email").eq(email),
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to get user by email: {e}")
            return None

    def get_user_by_phone(self, phone_number: str) -> Optional[dict]:
        try:
            response = self.table.query(
                IndexName="GSI-Phone",
                KeyConditionExpression=Key("phone_number").eq(phone_number),
                Limit=1,
            )
            items = response.get("Items", [])
            return items[0] if items else None
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to get user by phone: {e}")
            return None

    def update_business_name(self, user_id: str, business_name: str) -> bool:
        try:
            self.table.update_item(
                Key={"user_id": user_id, "sort_key": "PROFILE"},
                UpdateExpression="SET business_name = :b",
                ExpressionAttributeValues={":b": business_name},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update business name: {e}")
            return False

    def update_password_hash(self, user_id: str, password_hash: str) -> bool:
        try:
            self.table.update_item(
                Key={"user_id": user_id, "sort_key": "PROFILE"},
                UpdateExpression="SET password_hash = :p",
                ExpressionAttributeValues={":p": password_hash},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update password: {e}")
            return False

    # ---- Clients ----------------------------------------------------------

    def create_client(
        self, user_id: str, name: str, phone: str = "", notes: str = ""
    ) -> Optional[str]:
        try:
            client_id = str(uuid.uuid4())
            self.table.put_item(
                Item={
                    "user_id": user_id,
                    "sort_key": f"CLIENT#{client_id}",
                    "client_id": client_id,
                    "name": name,
                    "phone": phone,
                    "notes": notes,
                    "created_at": _now(),
                }
            )
            return client_id
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create client: {e}")
            return None

    def list_clients(self, user_id: str) -> list[dict]:
        return self._list_by_prefix(user_id, "CLIENT#")

    def delete_client(self, user_id: str, client_id: str) -> bool:
        try:
            self.table.delete_item(Key={"user_id": user_id, "sort_key": f"CLIENT#{client_id}"})
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to delete client: {e}")
            return False

    # ---- Quotations ---------------------------------------------------------

    def create_quotation(
        self,
        user_id: str,
        client_name: Optional[str],
        items: list[dict],
        total_before_gst: float,
        gst_amount: float,
        grand_total: float,
        pdf_s3_key: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[str]]:
        """Returns (quotation_id, sort_key), or (None, None) on failure."""
        try:
            quotation_id = str(uuid.uuid4())
            created_at = _now()
            sort_key = f"QUOTATION#{created_at}#{quotation_id}"
            self.table.put_item(
                Item={
                    "user_id": user_id,
                    "sort_key": sort_key,
                    "quotation_id": quotation_id,
                    "client_name": client_name or "",
                    "items": _to_decimal(items),
                    "total_before_gst": _to_decimal(total_before_gst),
                    "gst_amount": _to_decimal(gst_amount),
                    "grand_total": _to_decimal(grand_total),
                    "pdf_s3_key": pdf_s3_key or "",
                    "created_at": created_at,
                }
            )
            return quotation_id, sort_key
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create quotation: {e}")
            return None, None

    def set_quotation_pdf_key(self, user_id: str, sort_key: str, pdf_s3_key: str) -> bool:
        try:
            self.table.update_item(
                Key={"user_id": user_id, "sort_key": sort_key},
                UpdateExpression="SET pdf_s3_key = :k",
                ExpressionAttributeValues={":k": pdf_s3_key},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to set quotation PDF key: {e}")
            return False

    def list_quotations(self, user_id: str) -> list[dict]:
        return self._list_by_prefix(user_id, "QUOTATION#", reverse=True)

    def get_quotation(self, user_id: str, quotation_id: str) -> Optional[dict]:
        for item in self.list_quotations(user_id):
            if item.get("quotation_id") == quotation_id:
                return item
        return None

    # ---- Expenses -----------------------------------------------------------

    def create_expense(
        self,
        user_id: str,
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
    ) -> Optional[str]:
        try:
            expense_id = str(uuid.uuid4())
            created_at = _now()
            self.table.put_item(
                Item={
                    "user_id": user_id,
                    "sort_key": f"EXPENSE#{created_at}#{expense_id}",
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
                    "created_at": created_at,
                }
            )
            return expense_id
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create expense: {e}")
            return None

    def list_expenses(self, user_id: str) -> list[dict]:
        return self._list_by_prefix(user_id, "EXPENSE#", reverse=True)

    def update_expense(
        self,
        user_id: str,
        expense_id: str,
        vendor: str,
        amount: float,
        notes: str = "",
    ) -> bool:
        expense = next(
            (e for e in self.list_expenses(user_id) if e.get("expense_id") == expense_id),
            None,
        )
        if not expense:
            return False
        try:
            self.table.update_item(
                Key={"user_id": user_id, "sort_key": expense["sort_key"]},
                UpdateExpression="SET vendor = :v, amount = :a, notes = :n",
                ExpressionAttributeValues={
                    ":v": vendor,
                    ":a": _to_decimal(amount),
                    ":n": notes,
                },
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to update expense: {e}")
            return False

    def delete_expense(self, user_id: str, expense_id: str) -> bool:
        expense = next(
            (e for e in self.list_expenses(user_id) if e.get("expense_id") == expense_id),
            None,
        )
        if not expense:
            return False
        try:
            self.table.delete_item(Key={"user_id": user_id, "sort_key": expense["sort_key"]})
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to delete expense: {e}")
            return False

    # ---- Ledger (udhaar/credit tracking) -------------------------------------

    def create_ledger_entry(
        self,
        user_id: str,
        client_id: Optional[str],
        client_name: str,
        amount: float,
        description: str = "",
        due_date: Optional[str] = None,
    ) -> Optional[str]:
        try:
            ledger_id = str(uuid.uuid4())
            created_at = _now()
            self.table.put_item(
                Item={
                    "user_id": user_id,
                    "sort_key": f"LEDGER#{created_at}#{ledger_id}",
                    "ledger_id": ledger_id,
                    "client_id": client_id or "",
                    "client_name": client_name,
                    "amount": _to_decimal(amount),
                    "description": description,
                    "due_date": due_date or "",
                    "status": "owed",
                    "paid_at": "",
                    "created_at": created_at,
                }
            )
            return ledger_id
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to create ledger entry: {e}")
            return None

    def list_ledger_entries(self, user_id: str) -> list[dict]:
        return self._list_by_prefix(user_id, "LEDGER#", reverse=True)

    def mark_ledger_paid(self, user_id: str, ledger_id: str) -> bool:
        entry = next(
            (e for e in self.list_ledger_entries(user_id) if e.get("ledger_id") == ledger_id),
            None,
        )
        if not entry:
            return False
        try:
            self.table.update_item(
                Key={"user_id": user_id, "sort_key": entry["sort_key"]},
                UpdateExpression="SET #s = :paid, paid_at = :now",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":paid": "paid", ":now": _now()},
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to mark ledger entry paid: {e}")
            return False

    # ---- Shared helper --------------------------------------------------------

    def _list_by_prefix(self, user_id: str, prefix: str, reverse: bool = False) -> list[dict]:
        try:
            response = self.table.query(
                KeyConditionExpression=Key("user_id").eq(user_id)
                & Key("sort_key").begins_with(prefix),
                ScanIndexForward=not reverse,
            )
            return _from_decimal(response.get("Items", []))
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to list items with prefix {prefix}: {e}")
            return []

    def count_this_month(self, user_id: str, entity_prefix: str) -> int:
        """Count how many items of a given type (QUOTATION#, EXPENSE#, ...) a
        user has created in the current calendar month - used for Free-tier quotas."""
        month_prefix = f"{entity_prefix}{datetime.now(timezone.utc).strftime('%Y-%m')}"
        try:
            response = self.table.query(
                KeyConditionExpression=Key("user_id").eq(user_id)
                & Key("sort_key").begins_with(month_prefix),
                Select="COUNT",
            )
            return response.get("Count", 0)
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to count items for quota check ({entity_prefix}): {e}")
            return 0
