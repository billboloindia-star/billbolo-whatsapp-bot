"""Professional PDF generation for quotations and invoices using ReportLab."""

import hashlib
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
)

from src.services.ai_extractor import ExtractedData

logger = logging.getLogger(__name__)

HEADER_HEIGHT = 1.55 * inch
FOOTER_HEIGHT = 0.55 * inch


class PDFGenerator:
    """Generate professional GST invoices/quotations with watermark support."""

    BRAND_DARK = colors.HexColor("#0369A1")
    BRAND_PRIMARY = colors.HexColor("#0284C7")
    BRAND_LIGHT = colors.HexColor("#38BDF8")
    BG_TINT = colors.HexColor("#F0F9FF")
    BORDER_TINT = colors.HexColor("#E0F2FE")
    TEXT_DARK = colors.HexColor("#0F172A")
    TEXT_MUTED = colors.HexColor("#64748B")
    ROW_ALT = colors.HexColor("#F8FAFC")

    def __init__(self, business_name: str = "BillBolo"):
        self.business_name = business_name
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Configure custom paragraph styles."""
        self.styles.add(
            ParagraphStyle(
                name="SectionLabel",
                parent=self.styles["Normal"],
                fontSize=8,
                textColor=self.TEXT_MUTED,
                fontName="Helvetica-Bold",
                spaceAfter=2,
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="SectionValue",
                parent=self.styles["Normal"],
                fontSize=11,
                textColor=self.TEXT_DARK,
                fontName="Helvetica-Bold",
            )
        )
        self.styles.add(
            ParagraphStyle(
                name="NotesBody",
                parent=self.styles["Normal"],
                fontSize=9,
                textColor=self.TEXT_MUTED,
                leading=13,
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
                rightMargin=0.55 * inch,
                leftMargin=0.55 * inch,
                topMargin=HEADER_HEIGHT + 0.3 * inch,
                bottomMargin=FOOTER_HEIGHT + 0.3 * inch,
            )

            quote_ref = self._build_reference(extracted_data)
            story = []

            story.append(self._build_bill_to_row(extracted_data, quote_ref))
            story.append(Spacer(1, 0.3 * inch))
            story.append(self._build_items_table(extracted_data))
            story.append(Spacer(1, 0.18 * inch))
            story.append(self._build_totals_table(extracted_data))

            if extracted_data.notes:
                story.append(Spacer(1, 0.25 * inch))
                story.append(
                    Paragraph(
                        f'<b>Notes</b><br/>{extracted_data.notes}',
                        self.styles["NotesBody"],
                    )
                )

            def draw_chrome(c, _doc):
                self._draw_header(c, business_info, quote_ref)
                self._draw_footer(c, is_pro)

            doc.build(story, onFirstPage=draw_chrome, onLaterPages=draw_chrome)
            buffer.seek(0)
            logger.info("Quotation PDF generated successfully")
            return buffer.getvalue()
        except Exception as e:
            logger.error(f"Failed to generate quotation PDF: {e}")
            return None

    def _build_reference(self, extracted_data: ExtractedData) -> str:
        """Build a short, readable quotation reference number."""
        seed = f"{extracted_data.client_name}{datetime.now().isoformat()}"
        short_hash = hashlib.md5(seed.encode()).hexdigest()[:5].upper()
        return f"Q-{datetime.now().strftime('%y%m%d')}-{short_hash}"

    def _draw_header(self, c, business_info: dict, quote_ref: str):
        """Draw the branded header band directly on the canvas."""
        page_width, page_height = letter
        band_top = page_height
        band_bottom = page_height - HEADER_HEIGHT

        c.saveState()
        c.setFillColor(self.BRAND_DARK)
        c.rect(0, band_bottom, page_width, HEADER_HEIGHT, fill=1, stroke=0)
        c.setFillColor(self.BRAND_PRIMARY)
        c.rect(0, band_bottom, page_width, HEADER_HEIGHT * 0.55, fill=1, stroke=0)

        # Logo mark: circle with "B"
        logo_cx = 0.85 * inch
        logo_cy = band_top - 0.62 * inch
        c.setFillColor(colors.white)
        c.circle(logo_cx, logo_cy, 0.26 * inch, fill=1, stroke=0)
        c.setFillColor(self.BRAND_DARK)
        c.setFont("Helvetica-Bold", 18)
        c.drawCentredString(logo_cx, logo_cy - 0.08 * inch, "B")

        # Business name + tagline
        name_x = 1.25 * inch
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(name_x, band_top - 0.55 * inch, business_info.get("name", self.business_name))
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#E0F2FE"))
        c.drawString(name_x, band_top - 0.78 * inch, business_info.get("phone", "Virtual Munshi inside WhatsApp"))
        c.setFont("Helvetica", 7.5)
        c.drawString(name_x, band_top - 0.96 * inch, business_info.get("gst_number", "GST Registration Pending"))

        # QUOTATION label + ref, right aligned
        right_x = page_width - 0.55 * inch
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 22)
        c.drawRightString(right_x, band_top - 0.55 * inch, "QUOTATION")
        c.setFont("Helvetica", 9)
        c.setFillColor(colors.HexColor("#E0F2FE"))
        c.drawRightString(right_x, band_top - 0.78 * inch, quote_ref)
        c.drawRightString(right_x, band_top - 0.94 * inch, datetime.now().strftime("%d %b %Y"))

        c.restoreState()

    def _draw_footer(self, c, is_pro: bool):
        """Draw the footer directly on the canvas."""
        page_width, _ = letter
        c.saveState()
        c.setStrokeColor(self.BORDER_TINT)
        c.setLineWidth(1)
        c.line(0.55 * inch, FOOTER_HEIGHT, page_width - 0.55 * inch, FOOTER_HEIGHT)

        c.setFont("Helvetica-Bold", 8.5)
        c.setFillColor(self.BRAND_PRIMARY)
        c.drawCentredString(page_width / 2, FOOTER_HEIGHT - 0.22 * inch, "Made in 5 seconds on BillBolo")

        if not is_pro:
            c.setFont("Helvetica", 7.5)
            c.setFillColor(self.TEXT_MUTED)
            c.drawCentredString(
                page_width / 2,
                FOOTER_HEIGHT - 0.36 * inch,
                "Tap here to make free bills on WhatsApp — wa.me/918123422932",
            )
        c.restoreState()

    def _build_bill_to_row(self, extracted_data: ExtractedData, quote_ref: str) -> Table:
        """Build the 'Bill To' info card."""
        bill_to = Paragraph(
            f'<font size=8 color="{self.TEXT_MUTED.hexval()}"><b>BILL TO</b></font>'
            f'<br/><font size=12 color="{self.TEXT_DARK.hexval()}"><b>'
            f'{extracted_data.client_name or "Client"}</b></font>',
            self.styles["Normal"],
        )
        meta = Paragraph(
            f'<font size=8 color="{self.TEXT_MUTED.hexval()}"><b>REFERENCE</b></font>'
            f'<br/><font size=10 color="{self.TEXT_DARK.hexval()}">{quote_ref}</font>',
            self.styles["Normal"],
        )

        table = Table([[bill_to, meta]], colWidths=[4.4 * inch, 2.5 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), self.BG_TINT),
                    ("BOX", (0, 0), (-1, -1), 1, self.BORDER_TINT),
                    ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (0, 0), 14),
                    ("RIGHTPADDING", (1, 0), (1, 0), 14),
                    ("TOPPADDING", (0, 0), (-1, -1), 12),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
                ]
            )
        )
        return table

    def _build_items_table(self, extracted_data: ExtractedData) -> Table:
        """Build the line items table."""
        table_data = [
            ["ITEM", "QTY", "RATE (Rs.)", "UNIT", "AMOUNT (Rs.)"],
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
            colWidths=[2.6 * inch, 0.75 * inch, 1 * inch, 0.65 * inch, 1 * inch],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), self.BRAND_DARK),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("ALIGN", (0, 0), (0, -1), "LEFT"),
                    ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
                    ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5),
                    ("TEXTCOLOR", (0, 1), (-1, -1), self.TEXT_DARK),
                    ("LINEBELOW", (0, 0), (-1, 0), 0, colors.white),
                    ("LINEBELOW", (0, 1), (-1, -2), 0.5, self.BORDER_TINT),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, self.ROW_ALT]),
                    ("BOX", (0, 0), (-1, -1), 1, self.BORDER_TINT),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
                    ("LEFTPADDING", (0, 0), (0, -1), 12),
                    ("RIGHTPADDING", (-1, 0), (-1, -1), 12),
                ]
            )
        )
        return table

    def _build_totals_table(self, extracted_data: ExtractedData) -> Table:
        """Build the totals summary table."""
        totals_data = [
            ["Subtotal", f"Rs. {extracted_data.total_before_gst:,.2f}"],
            [f"GST @ {extracted_data.gst_percentage}%", f"Rs. {extracted_data.gst_amount:,.2f}"],
            ["GRAND TOTAL", f"Rs. {extracted_data.grand_total:,.2f}"],
        ]

        table = Table(totals_data, colWidths=[4.4 * inch, 1.6 * inch])
        table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                    ("FONT", (0, 0), (-1, 1), "Helvetica", 9.5),
                    ("TEXTCOLOR", (0, 0), (-1, 1), self.TEXT_MUTED),
                    ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 12.5),
                    ("BACKGROUND", (0, 2), (-1, 2), self.BRAND_PRIMARY),
                    ("TEXTCOLOR", (0, 2), (-1, 2), colors.white),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("TOPPADDING", (0, 2), (-1, 2), 11),
                    ("BOTTOMPADDING", (0, 2), (-1, 2), 11),
                    ("RIGHTPADDING", (-1, 0), (-1, -1), 12),
                    ("LINEBELOW", (0, 0), (-1, 0), 0.5, self.BORDER_TINT),
                    ("LINEBELOW", (0, 1), (-1, 1), 0.5, self.BORDER_TINT),
                ]
            )
        )
        return table
