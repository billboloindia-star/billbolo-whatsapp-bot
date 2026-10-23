"""FastAPI application entry point for BillBolo WhatsApp bot."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.config import settings
from src.handlers.webhook import create_webhook_handler
from src.handlers.payments import create_payment_handler
from src.handlers.web_demo import create_web_demo_handler

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
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
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
