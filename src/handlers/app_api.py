"""Handlers for the authenticated web app API: auth, clients, quotations, expenses, ledger."""

import base64
import logging
import re
from datetime import datetime, timezone

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile

from src.services.ai_extractor import GeminiExtractor
from src.services.app_db import AppDBClient
from src.services.auth import create_jwt, hash_password, is_admin_phone, verify_password
from src.services.pdf_generator import PDFGenerator
from src.services.storage import PDFStorage

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_AUDIO_BYTES = 10 * 1024 * 1024

FREE_MONTHLY_QUOTATION_LIMIT = 5
FREE_MONTHLY_EXPENSE_LIMIT = 5

QUOTA_EXCEEDED_MESSAGE = (
    "You've reached this month's free limit. Message BillBolo on WhatsApp "
    "or upgrade your plan for unlimited use."
)


async def _check_quota(db: AppDBClient, user_id: str, entity_prefix: str, limit: int) -> Response | None:
    """Returns a 429 response if the user is over their free monthly quota for
    this entity type, or None if they're clear to proceed. Admin phone bypasses
    entirely - checked first to avoid a wasted DB read on every admin call."""
    user = db.get_user_by_id(user_id)
    if user and is_admin_phone(user.get("phone_number", "")):
        return None

    if db.count_this_month(user_id, entity_prefix) >= limit:
        return JSONResponse(status_code=429, content={"error": QUOTA_EXCEEDED_MESSAGE})
    return None


def _user_public(user: dict) -> dict:
    return {
        "user_id": user["user_id"],
        "email": user.get("email", ""),
        "phone_number": user.get("phone_number", ""),
        "business_name": user.get("business_name", ""),
    }


class AuthHandler:
    def __init__(self, db: AppDBClient):
        self.db = db

    async def signup(self, request: Request) -> Response:
        try:
            body = await request.json()
            email = (body.get("email") or "").strip().lower()
            phone_number = (body.get("phone_number") or "").strip()
            password = body.get("password") or ""
            business_name = (body.get("business_name") or "").strip()

            if not EMAIL_RE.match(email):
                return JSONResponse(status_code=400, content={"error": "Enter a valid email address."})
            if len(re.sub(r"\D", "", phone_number)) < 10:
                return JSONResponse(status_code=400, content={"error": "Enter a valid phone number."})
            if len(password) < 6:
                return JSONResponse(status_code=400, content={"error": "Password must be at least 6 characters."})
            if not business_name:
                return JSONResponse(status_code=400, content={"error": "Business name is required."})

            user_id = self.db.create_user(
                email=email,
                phone_number=phone_number,
                password_hash=hash_password(password),
                business_name=business_name,
            )
            if not user_id:
                return JSONResponse(
                    status_code=409,
                    content={"error": "An account with this email or phone number already exists."},
                )

            user = self.db.get_user_by_id(user_id)
            token = create_jwt(user_id)
            return JSONResponse(status_code=201, content={"token": token, "user": _user_public(user)})
        except Exception as e:
            logger.error(f"Signup failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def login(self, request: Request) -> Response:
        try:
            body = await request.json()
            identifier = (body.get("identifier") or "").strip()
            password = body.get("password") or ""

            if not identifier or not password:
                return JSONResponse(status_code=400, content={"error": "Enter your email/phone and password."})

            if "@" in identifier:
                user = self.db.get_user_by_email(identifier.lower())
            else:
                user = self.db.get_user_by_phone(identifier)

            if not user or not verify_password(password, user.get("password_hash", "")):
                return JSONResponse(status_code=401, content={"error": "Incorrect email/phone or password."})

            token = create_jwt(user["user_id"])
            return JSONResponse(status_code=200, content={"token": token, "user": _user_public(user)})
        except Exception as e:
            logger.error(f"Login failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})


class ClientsHandler:
    def __init__(self, db: AppDBClient):
        self.db = db

    async def list(self, user_id: str) -> Response:
        clients = self.db.list_clients(user_id)
        return JSONResponse(status_code=200, content={"clients": _strip_keys(clients)})

    async def create(self, user_id: str, request: Request) -> Response:
        try:
            body = await request.json()
            name = (body.get("name") or "").strip()
            if not name:
                return JSONResponse(status_code=400, content={"error": "Client name is required."})

            client_id = self.db.create_client(
                user_id=user_id,
                name=name,
                phone=(body.get("phone") or "").strip(),
                notes=(body.get("notes") or "").strip(),
            )
            if not client_id:
                return JSONResponse(status_code=500, content={"error": "Failed to save client."})
            return JSONResponse(status_code=201, content={"client_id": client_id})
        except Exception as e:
            logger.error(f"Create client failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def delete(self, user_id: str, client_id: str) -> Response:
        ok = self.db.delete_client(user_id, client_id)
        if not ok:
            return JSONResponse(status_code=500, content={"error": "Failed to delete client."})
        return JSONResponse(status_code=200, content={"success": True})


class QuotationsHandler:
    def __init__(
        self,
        db: AppDBClient,
        extractor: GeminiExtractor,
        pdf_gen: PDFGenerator,
        storage: PDFStorage,
    ):
        self.db = db
        self.extractor = extractor
        self.pdf_gen = pdf_gen
        self.storage = storage

    async def list(self, user_id: str) -> Response:
        quotations = self.db.list_quotations(user_id)
        for q in quotations:
            if q.get("pdf_s3_key"):
                q["pdf_url"] = self.storage.get_presigned_url(q["pdf_s3_key"])
        return JSONResponse(status_code=200, content={"quotations": _strip_keys(quotations)})

    async def create_from_text(self, user_id: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(
                self.db, user_id, "QUOTATION#", FREE_MONTHLY_QUOTATION_LIMIT
            )
            if quota_response:
                return quota_response

            body = await request.json()
            text = (body.get("text") or "").strip()
            if not text:
                return JSONResponse(status_code=400, content={"error": "Please describe what you'd like to quote."})
            if len(text) > 1000:
                return JSONResponse(status_code=400, content={"error": "Description is too long (max 1000 characters)."})

            extracted = self.extractor.extract_from_text(text)
            return await self._finish(user_id, extracted)
        except Exception as e:
            logger.error(f"Create quotation (text) failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def create_from_audio(self, user_id: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(
                self.db, user_id, "QUOTATION#", FREE_MONTHLY_QUOTATION_LIMIT
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

            mime_type = getattr(audio_file, "content_type", None) or "audio/webm"
            extracted = self.extractor.extract_from_audio(audio_bytes, audio_mime_type=mime_type)
            return await self._finish(user_id, extracted)
        except Exception as e:
            logger.error(f"Create quotation (audio) failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def _finish(self, user_id: str, extracted) -> Response:
        if not extracted:
            return JSONResponse(
                status_code=422,
                content={"error": "Couldn't understand that. Try describing items, quantities and rates clearly."},
            )

        pdf_bytes = self.pdf_gen.generate_quotation(
            extracted_data=extracted,
            business_info={"name": "BillBolo"},
            is_pro=True,
        )
        if not pdf_bytes:
            return JSONResponse(status_code=500, content={"error": "Failed to generate PDF. Please try again."})

        quotation_id, sort_key = self.db.create_quotation(
            user_id=user_id,
            client_name=extracted.client_name,
            items=[item.model_dump() for item in extracted.items],
            total_before_gst=extracted.total_before_gst,
            gst_amount=extracted.gst_amount,
            grand_total=extracted.grand_total,
        )
        if not quotation_id:
            return JSONResponse(status_code=500, content={"error": "Failed to save quotation."})

        pdf_s3_key = f"{user_id}/quotations/{quotation_id}.pdf"
        uploaded = self.storage.upload(pdf_bytes, pdf_s3_key)

        if uploaded:
            self.db.set_quotation_pdf_key(user_id, sort_key, pdf_s3_key)

        return JSONResponse(
            status_code=201,
            content={
                "quotation_id": quotation_id,
                "client_name": extracted.client_name,
                "items": [item.model_dump() for item in extracted.items],
                "total_before_gst": extracted.total_before_gst,
                "gst_amount": extracted.gst_amount,
                "grand_total": extracted.grand_total,
                "pdf_base64": base64.standard_b64encode(pdf_bytes).decode("utf-8"),
                "pdf_url": self.storage.get_presigned_url(pdf_s3_key) if uploaded else None,
            },
        )


class ExpensesHandler:
    def __init__(self, db: AppDBClient, extractor: GeminiExtractor):
        self.db = db
        self.extractor = extractor

    async def list(self, user_id: str) -> Response:
        expenses = self.db.list_expenses(user_id)
        return JSONResponse(status_code=200, content={"expenses": _strip_keys(expenses)})

    async def create(self, user_id: str, request: Request) -> Response:
        try:
            quota_response = await _check_quota(
                self.db, user_id, "EXPENSE#", FREE_MONTHLY_EXPENSE_LIMIT
            )
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

            expense_id = self.db.create_expense(
                user_id=user_id,
                vendor=extracted.client_name,
                amount=extracted.grand_total or 0,
                notes=extracted.notes,
            )
            if not expense_id:
                return JSONResponse(status_code=500, content={"error": "Failed to save expense."})

            return JSONResponse(
                status_code=201,
                content={
                    "expense_id": expense_id,
                    "vendor": extracted.client_name,
                    "amount": extracted.grand_total or 0,
                    "notes": extracted.notes,
                },
            )
        except Exception as e:
            logger.error(f"Create expense failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})


class LedgerHandler:
    def __init__(self, db: AppDBClient):
        self.db = db

    async def list(self, user_id: str) -> Response:
        entries = self.db.list_ledger_entries(user_id)
        owed_entries = [e for e in entries if e.get("status") == "owed"]
        total_owed = sum(float(e.get("amount", 0)) for e in owed_entries)
        today = datetime.now(timezone.utc).date().isoformat()
        overdue_count = sum(1 for e in owed_entries if e.get("due_date") and e["due_date"] < today)

        return JSONResponse(
            status_code=200,
            content={
                "entries": _strip_keys(entries),
                "summary": {"total_owed": total_owed, "overdue_count": overdue_count},
            },
        )

    async def create(self, user_id: str, request: Request) -> Response:
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

            ledger_id = self.db.create_ledger_entry(
                user_id=user_id,
                client_id=body.get("client_id"),
                client_name=client_name,
                amount=amount,
                description=(body.get("description") or "").strip(),
                due_date=(body.get("due_date") or "").strip() or None,
            )
            if not ledger_id:
                return JSONResponse(status_code=500, content={"error": "Failed to save ledger entry."})
            return JSONResponse(status_code=201, content={"ledger_id": ledger_id})
        except Exception as e:
            logger.error(f"Create ledger entry failed: {e}")
            return JSONResponse(status_code=500, content={"error": "Something went wrong. Please try again."})

    async def mark_paid(self, user_id: str, ledger_id: str) -> Response:
        ok = self.db.mark_ledger_paid(user_id, ledger_id)
        if not ok:
            return JSONResponse(status_code=404, content={"error": "Ledger entry not found."})
        return JSONResponse(status_code=200, content={"success": True})


def _strip_keys(items: list[dict]) -> list[dict]:
    """Drop internal DynamoDB keys (user_id, sort_key) before returning to the client."""
    return [{k: v for k, v in item.items() if k not in ("user_id", "sort_key")} for item in items]


def create_app_api_handlers():
    """Factory wiring all app API handlers with shared dependencies."""
    db = AppDBClient()
    extractor = GeminiExtractor()
    pdf_gen = PDFGenerator()
    storage = PDFStorage()

    return {
        "auth": AuthHandler(db),
        "clients": ClientsHandler(db),
        "quotations": QuotationsHandler(db, extractor, pdf_gen, storage),
        "expenses": ExpensesHandler(db, extractor),
        "ledger": LedgerHandler(db),
    }
