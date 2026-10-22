"""FastAPI application entry point for BillBolo WhatsApp bot."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mangum import Mangum

from src.config import settings
from src.handlers.webhook import create_webhook_handler
from src.handlers.payments import create_payment_handler

# Configure logging
logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# Initialize handlers
webhook_handler = create_webhook_handler()
payment_handler = create_payment_handler()


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
