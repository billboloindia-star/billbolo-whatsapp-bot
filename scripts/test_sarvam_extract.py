"""First Sarvam AI Document Intelligence (Extract) call.

Generates a small sample quotation PDF, sends it to Sarvam's Extract
endpoint with a schema matching BillBolo's quotation fields, and
prints the structured result.

Run from the project root with the local venv:
    .venv/bin/python scripts/test_sarvam_extract.py
"""

import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sarvamai import SarvamAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SAMPLE_PDF_PATH = Path(__file__).parent / "sample_quotation.pdf"


def make_sample_pdf(path: Path) -> None:
    """Create a minimal sample quotation PDF to test extraction against."""
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 800, "Quotation")
    c.setFont("Helvetica", 12)
    c.drawString(50, 770, "Client: Sharma Ji")
    c.drawString(50, 750, "Item: Bathroom Tiling, 80 sq ft @ Rs. 1600/sq ft")
    c.drawString(50, 730, "Total: Rs. 128,000")
    c.save()


def main() -> None:
    make_sample_pdf(SAMPLE_PDF_PATH)
    print(f"Sample PDF created at {SAMPLE_PDF_PATH}")

    api_key = os.environ["SARVAM_API_KEY"]
    client = SarvamAI(api_subscription_key=api_key)

    schema = {
        "type": "object",
        "properties": {
            "client_name": {"type": "string", "description": "Name of the client/party"},
            "item_description": {"type": "string", "description": "Description of the item or service"},
            "total_amount": {"type": "number", "description": "Total quoted amount in INR"},
        },
    }

    with open(SAMPLE_PDF_PATH, "rb") as f:
        job = client.doc_ai.extract(
            file=[(SAMPLE_PDF_PATH.name, f, "application/pdf")],
            schema=json.dumps(schema),
            output_format="json",
        )

    print(f"Job submitted: job_id={job.job_id} status={job.status}")

    while True:
        status = client.doc_ai.get_status(job_id=job.job_id)
        print(f"Status: {status.status}")
        if status.status in ("completed", "partially_completed", "failed", "rejected"):
            break
        time.sleep(2)

    if status.status in ("completed", "partially_completed"):
        result = client.doc_ai.get_results(job_id=job.job_id)
        print("\nExtracted result:")
        print(result.result)
    else:
        print(f"\nJob did not complete successfully: {status.status}")


if __name__ == "__main__":
    main()
