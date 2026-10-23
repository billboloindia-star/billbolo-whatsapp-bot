"""FastAPI application entry point for BillBolo WhatsApp bot."""

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.config import settings
from src.handlers.app_api import create_app_api_handlers
from src.handlers.webhook import create_webhook_handler
from src.handlers.payments import create_payment_handler
from src.handlers.web_demo import create_web_demo_handler
from src.services.auth import get_current_user_id

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Initialize handlers
webhook_handler = create_webhook_handler()
payment_handler = create_payment_handler()
web_demo_handler = create_web_demo_handler()
app_api = create_app_api_handlers()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle manager."""
    logger.info(f"BillBolo Bot starting (Stage: {settings.stage})")
    yield
    logger.info("BillBolo Bot shutting down")


# Create FastAPI app
app = FastAPI(
    title="BillBolo WhatsApp Bot",
    description="Virtual Munshi for Indian SMBs",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://billbolo.in",
        "https://app.billbolo.in",
        "https://billboloindia-star.github.io",
        "http://localhost:3000",
    ],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
async def health_check() -> dict:
    """Health check endpoint for load balancers."""
    return {
        "status": "healthy",
        "service": "billbolo-whatsapp-bot",
        "stage": settings.stage,
    }


@app.get("/webhook")
async def webhook_get(request: Request):
    """WhatsApp webhook verification endpoint."""
    logger.debug("Webhook GET request received")
    return await webhook_handler.handle_get(request)


@app.post("/webhook")
async def webhook_post(request: Request):
    """WhatsApp webhook message endpoint."""
    logger.debug("Webhook POST request received")
    return await webhook_handler.handle_post(request)


@app.post("/payments/webhook")
async def payment_webhook(request: Request):
    """Cashfree payment webhook endpoint."""
    logger.debug("Payment webhook received")
    return await payment_handler.handle_webhook(request)


@app.post("/api/web/quotation")
async def web_demo_quotation(request: Request):
    """Public web demo endpoint - generate a sample quotation from text input."""
    logger.debug("Web demo quotation request received")
    return await web_demo_handler.handle_generate(request)


@app.post("/api/web/quotation/audio")
async def web_demo_quotation_audio(request: Request):
    """Public web demo endpoint - generate a sample quotation from a recorded voice note."""
    logger.debug("Web demo audio quotation request received")
    return await web_demo_handler.handle_generate_audio(request)


@app.post("/api/auth/signup")
async def auth_signup(request: Request):
    """Create a new web app account."""
    return await app_api["auth"].signup(request)


@app.post("/api/auth/login")
async def auth_login(request: Request):
    """Log into the web app with email/phone + password."""
    return await app_api["auth"].login(request)


@app.get("/api/clients")
async def list_clients(user_id: str = Depends(get_current_user_id)):
    return await app_api["clients"].list(user_id)


@app.post("/api/clients")
async def create_client(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["clients"].create(user_id, request)


@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str, user_id: str = Depends(get_current_user_id)):
    return await app_api["clients"].delete(user_id, client_id)


@app.get("/api/quotations")
async def list_quotations(user_id: str = Depends(get_current_user_id)):
    return await app_api["quotations"].list(user_id)


@app.post("/api/quotations")
async def create_quotation(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["quotations"].create_from_text(user_id, request)


@app.post("/api/quotations/audio")
async def create_quotation_audio(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["quotations"].create_from_audio(user_id, request)


@app.get("/api/expenses")
async def list_expenses(user_id: str = Depends(get_current_user_id)):
    return await app_api["expenses"].list(user_id)


@app.post("/api/expenses")
async def create_expense(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["expenses"].create(user_id, request)


@app.patch("/api/expenses/{expense_id}")
async def update_expense(expense_id: str, request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["expenses"].update(user_id, expense_id, request)


@app.delete("/api/expenses/{expense_id}")
async def delete_expense(expense_id: str, user_id: str = Depends(get_current_user_id)):
    return await app_api["expenses"].delete(user_id, expense_id)


@app.get("/api/ledger")
async def list_ledger(user_id: str = Depends(get_current_user_id)):
    return await app_api["ledger"].list(user_id)


@app.post("/api/ledger")
async def create_ledger_entry(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["ledger"].create(user_id, request)


@app.patch("/api/ledger/{ledger_id}/paid")
async def mark_ledger_paid(ledger_id: str, user_id: str = Depends(get_current_user_id)):
    return await app_api["ledger"].mark_paid(user_id, ledger_id)


@app.get("/api/profile")
async def get_profile(user_id: str = Depends(get_current_user_id)):
    return await app_api["profile"].get(user_id)


@app.patch("/api/profile")
async def update_profile(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["profile"].update(user_id, request)


@app.post("/api/profile/change-password")
async def change_password(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["profile"].change_password(user_id, request)


@app.post("/api/voice-command")
async def voice_command_classify(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["voice"].classify(user_id, request)


@app.post("/api/voice-command/execute")
async def voice_command_execute(request: Request, user_id: str = Depends(get_current_user_id)):
    return await app_api["voice"].execute(user_id, request)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler for unhandled errors."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"},
    )


# Lambda handler using Mangum
handler = Mangum(app, lifespan="auto")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )
