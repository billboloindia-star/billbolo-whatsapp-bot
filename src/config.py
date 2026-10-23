"""Application configuration and environment variable validation."""

import json
from typing import Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables with validation."""

    # WhatsApp Configuration
    whatsapp_api_token: str
    whatsapp_business_account_id: str
    whatsapp_phone_number_id: str
    whatsapp_webhook_verify_token: str
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"

    # Google Gemini API
    gemini_api_key: str

    # Sarvam AI API
    sarvam_api_key: str

    # Google Sheets API
    google_sheets_credentials_json: Optional[str] = None

    # Cashfree Payment
    cashfree_api_key: str
    cashfree_secret_key: str
    cashfree_webhook_secret: str

    # AWS
    aws_region: str = "us-east-1"
    dynamodb_table: str
    s3_bucket: str
    app_table: str = "billbolo-app-dev"

    # Web app auth
    jwt_secret: str

    # Application
    stage: str = "development"
    debug: bool = False
    log_level: str = "INFO"

    # Pricing
    free_credits: int = 5
    pro_monthly_price_inr: int = 499
    pro_yearly_price_inr: int = 4999

    # Bot
    bot_name: str = "BillBolo"
    bot_greeting_message: str = "Hi! I'm BillBolo, your WhatsApp billing assistant."

    class Config:
        env_file = ".env"
        case_sensitive = False

    def get_google_sheets_credentials(self) -> dict:
        """Parse Google Sheets service account credentials from JSON string."""
        if not self.google_sheets_credentials_json:
            raise ValueError("Google Sheets credentials not configured")
        try:
            return json.loads(self.google_sheets_credentials_json)
        except json.JSONDecodeError as e:
            raise ValueError("Invalid Google Sheets credentials JSON") from e

    def is_pro_user_unlocked(self, credits_used: int) -> bool:
        """Determine if user has exceeded free credits."""
        return credits_used >= self.free_credits


# Global settings instance
try:
    settings = Settings()
except Exception as e:
    raise RuntimeError(
        f"Failed to load configuration: {e}. "
        "Ensure all required environment variables are set."
    ) from e
