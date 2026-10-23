"""S3 storage for generated PDFs (web app quotation history)."""

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from src.config import settings

logger = logging.getLogger(__name__)

PRESIGNED_URL_EXPIRY_SECONDS = 3600


class PDFStorage:
    """Uploads quotation PDFs to S3 and issues time-limited download links."""

    def __init__(self):
        self.s3 = boto3.client("s3", region_name=settings.aws_region)
        self.bucket = settings.s3_bucket

    def upload(self, pdf_bytes: bytes, key: str) -> bool:
        try:
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=pdf_bytes,
                ContentType="application/pdf",
            )
            return True
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to upload PDF to S3 ({key}): {e}")
            return False

    def get_presigned_url(self, key: str) -> str | None:
        try:
            return self.s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=PRESIGNED_URL_EXPIRY_SECONDS,
            )
        except (BotoCoreError, ClientError) as e:
            logger.error(f"Failed to presign PDF URL ({key}): {e}")
            return None
