"""Handlers for the authenticated web app API: auth, clients, quotations, expenses, ledger.

Identity is unified with the WhatsApp bot here: a user's phone number (not a
random UUID) is the primary key, normalized the same way on both sides
(normalize_phone), so a quotation made on WhatsApp shows up in the web app
and vice versa - one account, two channels. A profile created by a first
WhatsApp message has no password; signing up on the web with that same
number "claims" it by adding one, rather than creating a second, orphaned
account.
"""

import base64
import logging
import re
from datetime import datetime, timezone

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from src.services.ai_extractor import GeminiExtractor, EXPENSE_CATEGORIES
from src.services.database import DynamoDBClient, clean_timestamp, from_decimal, normalize_phone
from src.services.auth import create_jwt, hash_password, is_admin_phone, verify_password
from src.services.pdf_generator import PDFGenerator
from src.services.storage import PDFStorage

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_AUDIO_BYTES = 10 * 1024 * 1024

FREE_MONTHLY_QUOTATION_LIMIT = 5
FREE_MONTHLY_EXPENSE_LIMIT = 5
FREE_MONTHLY_VOICE_COMMAND_LIMIT = 30

QUOTA_EXCEEDED_MESSAGE = (
    "You've reached this month's free limit. Message BillBolo on WhatsApp "
    "or upgrade your plan for unlimited use."
)

VOICE_NAV_PAGES = {"dashboard", "quotations", "clients", "expenses", "ledger", "profile"}
VOICE_UNKNOWN_MESSAGE = "Sorry, I didn't catch a command I can act on."


async def _check_quota(db: DynamoDBClient, phone_number: str, entity_prefix: str, limit: int) -> Response | None:
    """Returns a 429 response if the user is over their free monthly quota for
    this entity type, or None if they're clear to proceed. Admin phone bypasses
    entirely - checked first to avoid a wasted DB read on every admin call."""
    if is_admin_phone(phone_number):
        return None
    if db.count_this_month(phone_number, entity_prefix) >= limit:
        return JSONResponse(status_code=429, content={"error": QUOTA_EXCEEDED_MESSAGE})
    return None


def _user_public(profile: dict) -> dict:
    # Key stays "user_id" for frontend compatibility - the value is now the
    # phone number, which is the real identity.
    return {
        "user_id": profile["phone_number"],
        "email": profile.get("email", ""),
        "phone_number": profile["phone_number"],
        "business_name": profile.get("business_name", ""),
    }


class AuthHandler:
    def __init__(self, db: DynamoDBClient):
        self.db = db

    async def signup(self, request: Request) -> Response:
        try:
            body = await request.json()
            email = (body.get("email") or "").strip().lower()
            phone_number = normalize_phone(body.get("phone_number") or "")
            password = body.get("password") or ""
            business_name = (body.get("business_name") or "").strip()

            if email and not EMAIL_RE.match(email):
                return JSONResponse(status_code=400, content={"error": "Enter a valid email address."})
            if not phone_number:
                return JSONResponse(status_code=400, content={"error": "Enter a valid Indian mobile number."})
            if len(password) < 6:
                return JSONResponse(status_code=400, content={"error": "Password must be at least 6 characters."})
            if not business_name:
                return JSONResponse(status_code=400, content={"error": "Business name is required."})

            password_hash = hash_password(password)
            existing = self.db.get_user_profile(phone_number)

            if existing and existing.get("password_hash"):
                return JSONResponse(
                    status_code=409, content={"error": "An account with this phone number already exists."}
                )
            if existing:
                # A WhatsApp-only profile for this number already exists -
                # claim it with a password instead of creating a duplicate.
                self.db.update_profile_credentials(phone_number, email=email or None, password_hash=password_hash)
                if business_name and business_name != existing.get("business_name"):
                    self.db.update_business_name(phone_number, business_name)
            else:
                ok = self.db.create_user_profile(
                    phone_number, business_name, email=email or None, password_hash=password_hash
                )
                if not ok:
                    return JSONResponse(status_code=500, content={"error": "Failed to create account."})

            profile = self.db.get_user_profile(phone_number)
            token = create_jwt(phone_number)
            return JSONResponse(status_code=201, content={"token": token, "user": _user_public(profile)})
        except Exception as e:
            logger.error(f"Signup failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def login(self, request: Request) -> Response:
        try:
            body = await request.json()
            phone_number = normalize_phone((body.get("identifier") or body.get("phone_number") or "").strip())
            password = body.get("password") or ""

            if not phone_number or not password:
                return JSONResponse(status_code=400, content={"error": "Enter your phone number and password."})

            profile = self.db.get_user_profile(phone_number)
            if not profile or not verify_password(password, profile.get("password_hash", "")):
                return JSONResponse(status_code=401, content={"error": "Incorrect phone number or password."})

            token = create_jwt(phone_number)
            return JSONResponse(status_code=200, content={"token": token, "user": _user_public(profile)})
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})


class ClientsHandler:
    def __init__(self, db: DynamoDBClient):
        self.db = db

    async def list(self, phone_number: str) -> Response:
        clients = self.db.list_clients(phone_number)
        return JSONResponse(status_code=200, content={"clients": _strip_keys(clients)})

    async def create(self, phone_number: str, request: Request) -> Response:
        try:
            body = await request.json()
            name = (body.get("name") or "").strip()
            if not name:
                return JSONResponse(status_code=400, content={"error": "Client name is required."})

            client = self.db.create_client(
                phone_number,
                name=name,
                client_phone=(body.get("phone") or "").strip(),
                notes=(body.get("notes") or "").strip(),
            )
            if not client:
                return JSONResponse(status_code=500, content={"error": "Failed to save client."})
            return JSONResponse(status_code=201, content={"client_id": client["client_id"]})
        except Exception as e:
            logger.error(f"Create client failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def delete(self, phone_number: str, client_id: str) -> Response:
        ok = self.db.delete_client(phone_number, client_id)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to delete client."})
        return JSONResponse(status_code=200, content={"success": True})


class QuotationsHandler:
    def __init__(
        self,
        db: DynamoDBClient,
        extractor: GeminiExtractor,
        pdf_gen: PDFGenerator,
        storage: PDFStorage,
    ):
        self.db = db
        self.extractor = extractor
        self.pdf_gen = pdf_gen
        self.storage = storage

    async def list(self, phone_number: str) -> Response:
        quotations = self.db.list_quotations(phone_number)
        return JSONResponse(status_code=200, content={"quotations": [self._public(q) for q in quotations]})

    def _public(self, q: dict) -> dict:
        pdf_url = self.storage.get_presigned_url(q["pdf_s3_key"]) if q.get("pdf_s3_key") else None
        return from_decimal({
            "quotation_id": q["quote_number"],
            "client_name": q.get("client_name", ""),
            "items": q.get("items", []),
            "total_before_gst": q.get("total_before_gst", 0),
            "gst_amount": q.get("gst_amount", 0),
            "grand_total": q.get("grand_total", 0),
            "pdf_url": pdf_url,
            "created_at": clean_timestamp(q.get("created_at", "")),
        })

    async def create_from_text(self, phone_number: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(self.db, phone_number, "quotation", FREE_MONTHLY_QUOTATION_LIMIT)
            if quota_response:
                return quota_response

            body = await request.json()
            text = (body.get("text") or "").strip()
            if not text:
                return JSONResponse(status_code=400, content={"error": "Please describe what you'd like to quote."})
            if len(text) > 1000:
                return JSONResponse(status_code=400, content={"error": "Description is too long (max 1000 characters)."})

            extracted = self.extractor.extract_from_text(text)
            return await self._finish(phone_number, extracted)
        except Exception as e:
            logger.error(f"Create quotation (text) failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def create_from_audio(self, phone_number: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(self.db, phone_number, "quotation", FREE_MONTHLY_QUOTATION_LIMIT)
            if quota_response:
                return quota_response

            form = await request.form()
            audio_file = form.get("audio")

            if not isinstance(audio_file, UploadFile):
                return JSONResponse(status_code=400, content={"error": "No audio file received."})

            audio_bytes = await audio_file.read()
            if not audio_bytes or len(audio_bytes) > MAX_AUDIO_BYTES:
                return JSONResponse(status_code=400, content={"error": "Invalid or too-long recording."})

            mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
            extracted = self.extractor.extract_from_audio(audio_bytes, audio_mime_type=mime_type)
            return await self._finish(phone_number, extracted)
        except Exception as e:
            logger.error(f"Create quotation (audio) failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def _finish(self, phone_number: str, extracted) -> Response:
        if not extracted or not extracted.items:
            return JSONResponse(
                status_code=422,
                content={"error": "Couldn't understand that. Try describing items, quantities and rates clearly."},
            )

        is_invoice = extracted.data_type == "invoice"
        gen_fn = self.pdf_gen.generate_invoice if is_invoice else self.pdf_gen.generate_quotation
        doc_ref = self.pdf_gen.build_reference(extracted, "invoice" if is_invoice else "quotation")
        pdf_bytes = gen_fn(
            extracted_data=extracted,
            business_info={"name": "BillBolo"},
            is_pro=True,
            doc_ref=doc_ref,
        )
        if not pdf_bytes:
            return JSONResponse(status_code=500, content={"error": "Failed to generate PDF. Please try again."})

        quotation = None
        if not is_invoice:
            quotation = self.db.create_quotation(
                phone_number,
                quote_number=doc_ref,
                client_name=extracted.client_name or "Client",
                items=[item.model_dump() for item in extracted.items],
                total_before_gst=extracted.total_before_gst or 0,
                gst_amount=extracted.gst_amount or 0,
                gst_percentage=extracted.gst_percentage or 0,
                grand_total=extracted.grand_total or 0,
                discount_amount=extracted.discount_amount,
                discount_percent=extracted.discount_percent,
                notes=extracted.notes,
            )
            if not quotation:
                return JSONResponse(status_code=500, content={"error": "Failed to save quotation."})

            pdf_s3_key = f"{phone_number}/quotations/{doc_ref}.pdf"
            if self.storage.upload(pdf_bytes, pdf_s3_key):
                self.db.set_quotation_pdf_key(quotation, pdf_s3_key)
                quotation["pdf_s3_key"] = pdf_s3_key

        return JSONResponse(
            status_code=201,
            content={
                "quotation_id": doc_ref,
                "client_name": extracted.client_name,
                "items": [item.model_dump() for item in extracted.items],
                "total_before_gst": extracted.total_before_gst,
                "gst_amount": extracted.gst_amount,
                "grand_total": extracted.grand_total,
                "pdf_base64": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                "pdf_url": self.storage.get_presigned_url(quotation["pdf_s3_key"])
                if quotation and quotation.get("pdf_s3_key")
                else None,
            },
        )


class ExpensesHandler:
    def __init__(self, db: DynamoDBClient, extractor: GeminiExtractor):
        self.db = db
        self.extractor = extractor

    async def list(self, phone_number: str) -> Response:
        expenses = self.db.list_expenses(phone_number)
        return JSONResponse(status_code=200, content={"expenses": [self._public(e) for e in expenses]})

    def _public(self, e: dict) -> dict:
        return from_decimal({k: v for k, v in e.items() if k != "phone_number"} | {"created_at": clean_timestamp(e.get("created_at", ""))})

    async def create(self, phone_number: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(self.db, phone_number, "expense", FREE_MONTHLY_EXPENSE_LIMIT)
            if quota_response:
                return quota_response

            form = await request.form()
            image_file = form.get("image")

            if not isinstance(image_file, UploadFile):
                return JSONResponse(status_code=400, content={"error": "No image file received."})

            image_bytes = await image_file.read()
            if not image_bytes:
                return JSONResponse(status_code=400, content={"error": "Empty image."})

            mime_type = getattr(image_file, "content_type", None) or "image/jpeg"
            extracted = self.extractor.extract_from_image(image_bytes, image_mime_type=mime_type)
            if not extracted:
                return JSONResponse(status_code=422, content={"error": "Couldn't read that bill/receipt image."})

            items = [item.model_dump() for item in extracted.items]
            vendor = extracted.seller_name or extracted.client_name
            expense = self.db.create_expense(
                phone_number,
                vendor=vendor,
                amount=extracted.grand_total or 0,
                notes=extracted.notes,
                items=items,
                seller_name=extracted.seller_name,
                seller_gstin=extracted.seller_gstin,
                seller_address=extracted.seller_address,
                billing_address=extracted.billing_address,
                shipping_address=extracted.shipping_address,
                order_date=extracted.order_date,
                category=extracted.expense_category,
            )
            if not expense:
                return JSONResponse(status_code=500, content={"error": "Failed to save expense."})

            return JSONResponse(status_code=201, content=self._public(expense))
        except Exception as e:
            logger.error(f"Create expense failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def update(self, phone_number: str, expense_id: str, request: Request) -> Response:
        try:
            body = await request.json()
            vendor = (body.get("vendor") or "").strip()
            amount = body.get("amount")
            notes = (body.get("notes") or "").strip()
            category = (body.get("category") or "").strip() or None
            if category and category not in EXPENSE_CATEGORIES:
                return JSONResponse(status_code=400, content={"error": "Invalid category."})

            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"error": "Enter a valid amount."})
            if amount <= 0:
                return JSONResponse(status_code=400, content={"error": "Amount must be greater than zero."})

            ok = self.db.update_expense(phone_number, expense_id, vendor, amount, notes, category)
            if not ok:
                return JSONResponse(status_code=404, content={"error": "Expense not found."})
            return JSONResponse(status_code=200, content={"success": True})
        except Exception as e:
            logger.error(f"Update expense failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def delete(self, phone_number: str, expense_id: str) -> Response:
        ok = self.db.delete_expense(phone_number, expense_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Expense not found."})
        return JSONResponse(status_code=200, content={"success": True})


class LedgerHandler:
    def __init__(self, db: DynamoDBClient):
        self.db = db

    async def list(self, phone_number: str) -> Response:
        entries = self.db.list_ledger_entries(phone_number)
        owed_entries = [e for e in entries if e.get("status") == "owed"]
        total_owed = sum(float(e.get("amount", 0)) for e in owed_entries)
        today = datetime.now(timezone.utc).date().isoformat()
        overdue_count = sum(1 for e in owed_entries if e.get("due_date") and e["due_date"] < today)

        public_entries = [
            from_decimal({k: v for k, v in e.items() if k != "phone_number"} | {"created_at": clean_timestamp(e.get("created_at", ""))})
            for e in entries
        ]
        return JSONResponse(
            status_code=200,
            content={
                "entries": public_entries,
                "summary": {"total_owed": total_owed, "overdue_count": overdue_count},
            },
        )

    async def create(self, phone_number: str, request: Request) -> Response:
        try:
            body = await request.json()
            client_name = (body.get("client_name") or "").strip()
            amount = body.get("amount")

            if not client_name:
                return JSONResponse(status_code=400, content={"error": "Client name is required."})
            try:
                amount = float(amount)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"error": "Enter a valid amount."})
            if amount <= 0:
                return JSONResponse(status_code=400, content={"error": "Amount must be greater than zero."})

            entry = self.db.create_ledger_entry(
                phone_number,
                client_name=client_name,
                amount=amount,
                description=(body.get("description") or "").strip(),
                due_date=(body.get("due_date") or "").strip() or None,
            )
            if not entry:
                return JSONResponse(status_code=500, content={"error": "Failed to save ledger entry."})
            return JSONResponse(status_code=201, content={"ledger_id": entry["ledger_id"]})
        except Exception as e:
            logger.error(f"Create ledger entry failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def mark_paid(self, phone_number: str, ledger_id: str) -> Response:
        ok = self.db.mark_ledger_entry_by_id(phone_number, ledger_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Ledger entry not found."})
        return JSONResponse(status_code=200, content={"success": True})


class ProfileHandler:
    def __init__(self, db: DynamoDBClient):
        self.db = db

    async def get(self, phone_number: str) -> Response:
        profile = self.db.get_user_profile(phone_number)
        if not profile:
            return JSONResponse(status_code=404, content={"error": "Account not found."})

        admin = is_admin_phone(phone_number)
        quotations_used = self.db.count_this_month(phone_number, "quotation")
        expenses_used = self.db.count_this_month(phone_number, "expense")

        return JSONResponse(
            status_code=200,
            content={
                "user": _user_public(profile),
                "plan": {
                    "name": "Admin" if admin else "Free",
                    "unlimited": admin,
                    "quotations_used": quotations_used,
                    "quotations_limit": None if admin else FREE_MONTHLY_QUOTATION_LIMIT,
                    "expenses_used": expenses_used,
                    "expenses_limit": None if admin else FREE_MONTHLY_EXPENSE_LIMIT,
                },
            },
        )

    async def update(self, phone_number: str, request: Request) -> Response:
        try:
            body = await request.json()
            business_name = (body.get("business_name") or "").strip()
            if not business_name:
                return JSONResponse(status_code=400, content={"error": "Business name is required."})

            ok = self.db.update_business_name(phone_number, business_name)
            if not ok:
                return JSONResponse(status_code=500, content={"error": "Failed to update profile."})
            return JSONResponse(status_code=200, content={"success": True})
        except Exception as e:
            logger.error(f"Update profile failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def change_password(self, phone_number: str, request: Request) -> Response:
        try:
            body = await request.json()
            current_password = body.get("current_password") or ""
            new_password = body.get("new_password") or ""

            if len(new_password) < 6:
                return JSONResponse(
                    status_code=400, content={"error": "New password must be at least 6 characters."}
                )

            profile = self.db.get_user_profile(phone_number)
            if not profile or not verify_password(current_password, profile.get("password_hash", "")):
                return JSONResponse(status_code=401, content={"error": "Current password is incorrect."})

            ok = self.db.update_profile_credentials(phone_number, password_hash=hash_password(new_password))
            if not ok:
                return JSONResponse(status_code=500, content={"error": "Failed to update password."})
            return JSONResponse(status_code=200, content={"success": True})
        except Exception as e:
            logger.error(f"Change password failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})


def _strip_keys(items: list[dict]) -> list[dict]:
    """Drop internal DynamoDB keys before returning to the client, and clean
    up the composite created_at (iso timestamp + id suffix) for display."""
    return [
        from_decimal({k: v for k, v in item.items() if k != "phone_number"} | {"created_at": clean_timestamp(item.get("created_at", ""))})
        for item in items
    ]


class VoiceCommandHandler:
    """Tight v1 voice assistant: a fixed set of commands, each requiring explicit
    confirmation before anything is written (except navigate, which is read-only)."""

    def __init__(self, db: DynamoDBClient, extractor: GeminiExtractor, quotations_handler: "QuotationsHandler"):
        self.db = db
        self.extractor = extractor
        self.quotations = quotations_handler

    async def classify(self, phone_number: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(
                self.db, phone_number, "voicecmd", FREE_MONTHLY_VOICE_COMMAND_LIMIT
            )
            if quota_response:
                return quota_response

            form = await request.form()
            audio_file = form.get("audio")
            if not isinstance(audio_file, UploadFile):
                return JSONResponse(status_code=400, content={"error": "No audio file received."})

            audio_bytes = await audio_file.read()
            if not audio_bytes or len(audio_bytes) > MAX_AUDIO_BYTES:
                return JSONResponse(status_code=400, content={"error": "Invalid or too-long recording."})

            self.db.log_voice_command_usage(phone_number)

            mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
            known_clients = [b["client_name"] for b in self.db.get_ledger_balances(phone_number)]
            command = self.extractor.classify_voice_command(audio_bytes, audio_mime_type=mime_type, known_clients=known_clients)
            if not command:
                return JSONResponse(status_code=422, content={"error": VOICE_UNKNOWN_MESSAGE})

            if command.action == "navigate":
                page = (command.page or "").strip().lower()
                if page not in VOICE_NAV_PAGES:
                    return JSONResponse(
                        status_code=200, content={"action": "unknown", "confirmation_text": VOICE_UNKNOWN_MESSAGE}
                    )
                return JSONResponse(
                    status_code=200,
                    content={"action": "navigate", "page": page, "confirmation_text": command.confirmation_text},
                )

            if command.action == "mark_ledger_paid":
                return await self._resolve_mark_paid(phone_number, command)

            return JSONResponse(status_code=200, content=command.model_dump())
        except Exception as e:
            logger.error(f"Voice command classify failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def _resolve_mark_paid(self, phone_number: str, command) -> Response:
        query = (command.client_name_query or "").strip().lower()
        if not query:
            return JSONResponse(
                status_code=200,
                content={"action": "unknown", "confirmation_text": "I couldn't tell who to mark as paid."},
            )

        entries = [
            e
            for e in self.db.list_ledger_entries(phone_number, status="owed")
            if query in e.get("client_name", "").lower()
        ]

        if not entries:
            return JSONResponse(
                status_code=200,
                content={
                    "action": "unknown",
                    "confirmation_text": f"Couldn't find an unpaid ledger entry matching \"{command.client_name_query}\".",
                },
            )
        distinct_names = sorted({e["client_name"] for e in entries})
        if len(distinct_names) > 1:
            names = ", ".join(distinct_names)
            return JSONResponse(
                status_code=200,
                content={
                    "action": "unknown",
                    "confirmation_text": f"Found multiple matches ({names}) - please be more specific.",
                },
            )

        entry = entries[0]
        return JSONResponse(
            status_code=200,
            content={
                "action": "mark_ledger_paid",
                "ledger_id": entry["ledger_id"],
                "confirmation_text": f"Mark {entry['client_name']}'s ₹{entry['amount']} entry as paid?",
            },
        )

    async def execute(self, phone_number: str, request: Request) -> Response:
        try:
            body = await request.json()
            action = body.get("action")

            if action == "create_quotation":
                description = (body.get("description") or "").strip()
                if not description:
                    return JSONResponse(status_code=400, content={"error": "Missing description."})
                quota_response = await _check_quota(self.db, phone_number, "quotation", FREE_MONTHLY_QUOTATION_LIMIT)
                if quota_response:
                    return quota_response
                extracted = self.extractor.extract_from_text(description)
                return await self.quotations._finish(phone_number, extracted)

            if action == "log_expense":
                vendor = (body.get("vendor") or "").strip()
                try:
                    amount = float(body.get("amount"))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": "Invalid amount."})
                if amount <= 0:
                    return JSONResponse(status_code=400, content={"error": "Amount must be greater than zero."})
                notes = (body.get("notes") or "").strip()

                quota_response = await _check_quota(self.db, phone_number, "expense", FREE_MONTHLY_EXPENSE_LIMIT)
                if quota_response:
                    return quota_response

                expense = self.db.create_expense(phone_number, vendor=vendor, amount=amount, notes=notes)
                if not expense:
                    return JSONResponse(status_code=500, content={"error": "Failed to log expense."})
                return JSONResponse(status_code=201, content={"success": True, "expense_id": expense["expense_id"]})

            if action == "add_client":
                name = (body.get("client_name") or "").strip()
                if not name:
                    return JSONResponse(status_code=400, content={"error": "Missing client name."})
                phone = (body.get("client_phone") or "").strip()
                client = self.db.create_client(phone_number, name=name, client_phone=phone)
                if not client:
                    return JSONResponse(status_code=500, content={"error": "Failed to add client."})
                return JSONResponse(status_code=201, content={"success": True, "client_id": client["client_id"]})

            if action == "add_ledger_entry":
                client_name = (body.get("client_name") or "").strip()
                if not client_name:
                    return JSONResponse(status_code=400, content={"error": "Missing client name."})
                try:
                    amount = float(body.get("amount"))
                except (TypeError, ValueError):
                    return JSONResponse(status_code=400, content={"error": "Invalid amount."})
                if amount <= 0:
                    return JSONResponse(status_code=400, content={"error": "Amount must be greater than zero."})
                description = (body.get("ledger_description") or "").strip()
                due_date = (body.get("due_date") or "").strip() or None

                entry = self.db.create_ledger_entry(
                    phone_number,
                    client_name=client_name,
                    amount=amount,
                    description=description,
                    due_date=due_date,
                )
                if not entry:
                    return JSONResponse(status_code=500, content={"error": "Failed to add ledger entry."})
                return JSONResponse(status_code=201, content={"success": True, "ledger_id": entry["ledger_id"]})

            if action == "mark_ledger_paid":
                ledger_id = body.get("ledger_id")
                if not ledger_id:
                    return JSONResponse(status_code=400, content={"error": "Missing ledger_id."})
                ok = self.db.mark_ledger_entry_by_id(phone_number, ledger_id)
                if not ok:
                    return JSONResponse(status_code=404, content={"error": "Ledger entry not found."})
                return JSONResponse(status_code=200, content={"success": True})

            return JSONResponse(status_code=400, content={"error": "Unsupported action."})
        except Exception as e:
            logger.error(f"Voice command execute failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})


def create_app_api_handlers():
    """Factory wiring all app API handlers with shared dependencies."""
    db = DynamoDBClient()
    extractor = GeminiExtractor()
    pdf_gen = PDFGenerator()
    storage = PDFStorage()
    quotations_handler = QuotationsHandler(db, extractor, pdf_gen, storage)

    return {
        "auth": AuthHandler(db),
        "clients": ClientsHandler(db),
        "quotations": quotations_handler,
        "expenses": ExpensesHandler(db, extractor),
        "ledger": LedgerHandler(db),
        "profile": ProfileHandler(db),
        "voice": VoiceCommandHandler(db, extractor, quotations_handler),
    }
