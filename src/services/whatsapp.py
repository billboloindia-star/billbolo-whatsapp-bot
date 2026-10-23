"""WhatsApp Cloud API client for sending/receiving messages and downloading media."""

import logging
from typing import Optional

import requests
from pydantic import BaseModel, Field

from src.config import settings

logger = logging.getLogger(__name__)


class WhatsAppMessage(BaseModel):
    """Represents a WhatsApp message."""

    message_id: str
    from_phone_number: str
    timestamp: str
    type: str  # text, image, audio, document
    content: Optional[str] = None
    media_id: Optional[str] = None
    media_url: Optional[str] = None


class WhatsAppClient:
    """WhatsApp Cloud API client with defensive error handling."""

    def __init__(self):
        self.api_token = settings.whatsapp_api_token
        self.business_account_id = settings.whatsapp_business_account_id
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.api_url = settings.whatsapp_api_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
            }
        )

    def send_text_message(self, phone_number: str, message_text: str) -> bool:
        """Send a text message to a WhatsApp user."""
        try:
            url = f"{self.api_url}/{self.phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "text",
                "text": {"body": message_text},
            }
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Text message sent to {phone_number}")
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send text message: {e}")
            return False

    def send_document(
        self, phone_number: str, document_url: str, caption: str = ""
    ) -> bool:
        """Send a document (PDF) to a WhatsApp user."""
        try:
            url = f"{self.api_url}/{self.phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone_number,
                "type": "document",
                "document": {
                    "link": document_url,
                    "caption": caption,
                },
            }
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Document sent to {phone_number}")
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to send document: {e}")
            return False

    def download_media(self, media_id: str) -> Optional[bytes]:
        """Download media file (audio, image) from WhatsApp."""
        try:
            # First, get the download URL
            url = f"{self.api_url}/{media_id}"
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            media_url = response.json().get("url")

            if not media_url:
                logger.error(f"No URL found for media {media_id}")
                return None

            # Download the actual file
            file_response = self.session.get(media_url, timeout=30)
            file_response.raise_for_status()
            logger.info(f"Media downloaded: {media_id}")
            return file_response.content
        except requests.RequestException as e:
            logger.error(f"Failed to download media {media_id}: {e}")
            return None

    def mark_message_as_read(self, message_id: str) -> bool:
        """Mark a message as read."""
        try:
            url = f"{self.api_url}/{self.phone_number_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
            }
            response = self.session.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Failed to mark message as read: {e}")
            return False
