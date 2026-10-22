"""Professional PDF generation for quotations and invoices using ReportLab."""

import io
import logging
from datetime import datetime
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
    PageTemplate,
    Frame,
)
from reportlab.pdfgen import canvas

from src.services.ai_extractor import ExtractedData

logger = logging.getLogger(__name__)


class PDFGenerator:
    """Generate professional GST invoices/quotations with watermark support."""

    BRAND_COLOR = colors.HexColor("#1E40AF")
    ACCENT_COLOR = colors.HexColor("#DC2626")

    def __init__(self, business_name: str = "BillBolo"):
        self.business_name = business_name
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Configure custom paragraph styles."""
        self.styles.add(
            ParagraphStyle(
                name="CustomTitle",
                parent=self.styles["Heading1"],
                fontSize=24,
                textColor=self.BRAND_COLOR,
                spaceAfter=12,
                alignment=1,  # Center
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="InvoiceLabel",
                parent=self.styles["Normal"],
                fontSize=10,
                textColor=colors.grey,
            )
        )

    def generate_quotation(
        self,
        extracted_data: ExtractedData,
        business_info: dict,
        is_pro: bool = False,
    ) -> Optional[bytes]:
        """Generate a professional quotation PDF."""
        try:
            buffer = io.BytesIO()
            doc = SimpleDocTemplate(
                buffer,
                pagesize=letter,
                rightMargin=0.5 * inch,
                leftMargin=0.5 * inch,
                topMargin=0.75 * inch,
                bottomMargin=0.5 * inch,
            )

            story = []

            # Header
            story.append(self._build_header(business_info))
            story.append(Spacer(1, 0.2 * inch))

            # Document type and details
            story.append(
                Paragraph(
                    "QUOTATION",
                    self.styles["CustomTitle"],
                )
            )
            story.append(Spacer(1, 0.1 * inch))

            # Quotation details (date, ref, etc)
            details_data = [
                ["Date", datetime.now().strftime("%d-%b-%Y")],
                ["To", extracted_data.client_name or "Client"],
            ]
            details_table = Table(details_data, colWidths=[1.5 * inch, 4 * inch])
            details_table.setStyle(
                TableStyle([("FONT", (0, 0), (-1, -1), "Helvetica", 9)])
            )
            story.append(details_table)
            story.append(Spacer(1, 0.2 * inch))

            # Items table
            story.append(self._build_items_table(extracted_data))
            story.append(Spacer(1, 0.1 * inch))

            # Totals
            story.append(self._build_totals_table(extracted_data))
            story.append(Spacer(1, 0.2 * inch))

            # Notes
            if extracted_data.notes:
                story.append(
                    Paragraph(
                        f"<b>Notes:</b> {extracted_data.notes}",
                        self.styles["Normal"],
                    )
                )
                story.append(Spacer(1, 0.1 * inch))

            # Footer with watermark (free tier only)
            if not is_pro:
                story.append(Spacer(1, 0.2 * inch))
                footer_text = (
                    '<font size=8 color="gray"><u>Made in 5s on BillBolo '
                    '— Tap here to make free bills</u></font>'
                )
                story.append(Paragraph(footer_text, self.styles["Normal"]))

            # Build PDF
            doc.build(story)
            buffer.seek(0)
            logger.info("Quotation PDF generated successfully")
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Failed to generate quotation PDF: {e}")
            return None

    def _build_header(self, business_info: dict) -> Paragraph:
        """Build the document header with business name."""
        header_text = f"""
        <b><font size=16 color="{self.BRAND_COLOR.hexval()}">{business_info.get('name', self.business_name)}</font></b><br/>
        {business_info.get('phone', 'Contact us on WhatsApp')}<br/>
        <font size=8 color="gray">{business_info.get('gst_number', 'GST Registration Pending')}</font>
        """
        return Paragraph(header_text, self.styles["Normal"])

    def _build_items_table(self, extracted_data: ExtractedData) -> Table:
        """Build the line items table."""
        table_data = [
            ["Item", "Qty", "Rate (₹)", "Unit", "Amount (₹)"],
        ]

        for item in extracted_data.items:
            table_data.append(
                [
                    item.item_name,
                    f"{item.quantity:,.2f}",
                    f"{item.rate:,.2f}",
                    item.unit,
                    f"{item.amount:,.2f}",
                ]
            )

        table = Table(
            table_data,
            colWidths=[2.5 * inch, 0.8 * inch, 1 * inch, 0.7 * inch, 1 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.BRAND_COLOR),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 10),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 9),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
                ]
            )
        )
        return table

    def _build_totals_table(self, extracted_data: ExtractedData) -> Table:
        """Build the totals summary table."""
        totals_data = [
            ["Subtotal", f"₹{extracted_data.total_before_gst:,.2f}"],
            [f"GST @ {extracted_data.gst_percentage}%", f"₹{extracted_data.gst_amount:,.2f}"],
            ["GRAND TOTAL", f"₹{extracted_data.grand_total:,.2f}"],
        ]

        table = Table(totals_data, colWidths=[4.5 * inch, 1.5 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("FONT", (0, 0), (-1, 1), "Helvetica", 9),
                    ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 11),
                    ("BACKGROUND", (0, 2), (-1, 2), self.ACCENT_COLOR),
                    ("TEXTCOLOR", (0, 2), (-1, 2), colors.whitesmoke),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ]
            )
        )
        return table
