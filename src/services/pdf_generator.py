"""Professional PDF generation for quotations and invoices using ReportLab."""

import hashlib
import io
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from reportlab.graphics.shapes import Drawing, Path, Rect
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

FOOTER_HEIGHT = 0.5 * inch


@dataclass
class QuotationTheme:
    """A named visual treatment for generated quotations/invoices.

    `header_style` picks which letterhead/items-header layout branch the
    generator uses - themes differing only in color don't need their own
    layout code, only ones that are structurally different do.
    """

    key: str
    name: str
    brand_primary: str
    brand_dark: str
    header_style: str  # "minimal" | "band" | "formal" | "compact"
    page_bg_tint: Optional[str] = None  # None = plain white page
    font_scale: float = 1.0


THEME_MINIMAL = QuotationTheme(
    key="minimal", name="Minimal",
    brand_primary="#0284C7", brand_dark="#0369A1",
    header_style="minimal",
)
THEME_BOLD = QuotationTheme(
    key="bold", name="Bold",
    brand_primary="#0284C7", brand_dark="#0369A1",
    header_style="band",
)
THEME_FORMAL = QuotationTheme(
    key="formal", name="Formal",
    brand_primary="#334155", brand_dark="#1E293B",
    header_style="formal", page_bg_tint="#F1F5F9",
)
THEME_COMPACT = QuotationTheme(
    key="compact", name="Compact",
    brand_primary="#0284C7", brand_dark="#0369A1",
    header_style="compact", font_scale=0.88,
)

QUOTATION_THEMES = {
    t.key: t for t in [THEME_MINIMAL, THEME_BOLD, THEME_FORMAL, THEME_COMPACT]
}


class PDFGenerator:
    """Generate clean, professional quotations, themeable via QuotationTheme
    (thin colored accents on a white page by default, with bolder/formal/
    compact variants available)."""

    BG_TINT = colors.HexColor("#F8FAFC")
    BORDER = colors.HexColor("#E2E8F0")
    TEXT_DARK = colors.HexColor("#0F172A")
    TEXT_MUTED = colors.HexColor("#64748B")
    TEXT_FAINT = colors.HexColor("#94A3B8")

    def __init__(self, business_name: str = "BillBolo", theme: QuotationTheme = THEME_MINIMAL):
        self.business_name = business_name
        self.theme = theme
        self.BRAND_PRIMARY = colors.HexColor(theme.brand_primary)
        self.BRAND_DARK = colors.HexColor(theme.brand_dark)
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()

    def _setup_custom_styles(self):
        """Configure custom paragraph styles, scaled by the active theme."""
        s = self.styles
        fs = self.theme.font_scale
        header_white = self.theme.header_style == "band"
        header_text_color = colors.white if header_white else self.TEXT_DARK
        header_meta_color = colors.HexColor("#DBEAFE") if header_white else self.TEXT_MUTED

        s.add(ParagraphStyle(
            name="BizName", parent=s["Normal"], fontSize=16 * fs,
            textColor=header_text_color, fontName="Helvetica-Bold", leading=19 * fs,
        ))
        s.add(ParagraphStyle(
            name="BizMeta", parent=s["Normal"], fontSize=8.5 * fs,
            textColor=header_meta_color, leading=13 * fs,
        ))
        s.add(ParagraphStyle(
            name="DocTitle", parent=s["Normal"], fontSize=22 * fs,
            textColor=(colors.white if header_white else self.BRAND_DARK), fontName="Helvetica-Bold",
            alignment=2, leading=24 * fs,
        ))
        s.add(ParagraphStyle(
            name="DocMeta", parent=s["Normal"], fontSize=9 * fs,
            textColor=header_meta_color, alignment=2, leading=13 * fs,
        ))
        s.add(ParagraphStyle(
            name="Label", parent=s["Normal"], fontSize=7.5 * fs,
            textColor=self.TEXT_FAINT, fontName="Helvetica-Bold", leading=10 * fs,
        ))
        s.add(ParagraphStyle(
            name="LabelRight", parent=s["Label"], alignment=2,
        ))
        s.add(ParagraphStyle(
            name="ClientName", parent=s["Normal"], fontSize=12.5 * fs,
            textColor=self.TEXT_DARK, fontName="Helvetica-Bold", leading=15 * fs,
        ))
        s.add(ParagraphStyle(
            name="ValueRight", parent=s["Normal"], fontSize=9.5 * fs,
            textColor=self.TEXT_DARK, alignment=2, leading=13 * fs,
        ))
        s.add(ParagraphStyle(
            name="NotesBody", parent=s["Normal"], fontSize=9 * fs,
            textColor=self.TEXT_MUTED, leading=13 * fs,
        ))
        s.add(ParagraphStyle(
            name="TermsBody", parent=s["Normal"], fontSize=8 * fs, leading=12.5 * fs,
            textColor=self.TEXT_MUTED,
        ))
        s.add(ParagraphStyle(
            name="SignLine", parent=s["Normal"], fontSize=9 * fs, alignment=2,
            textColor=self.TEXT_MUTED,
        ))
        s.add(ParagraphStyle(
            name="ItemHeader", parent=s["Normal"], fontSize=8 * fs,
            textColor=(colors.white if self.theme.header_style in ("formal",) else self.BRAND_DARK),
            fontName="Helvetica-Bold",
        ))

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
                rightMargin=0.65 * inch,
                leftMargin=0.65 * inch,
                topMargin=0.6 * inch,
                bottomMargin=FOOTER_HEIGHT + 0.35 * inch,
            )

            quote_ref = self._build_reference(extracted_data)
            valid_until = self._business_days_from_now(15)
            fs = self.theme.font_scale
            is_band = self.theme.header_style == "band"
            story = []

            story.append(self._build_letterhead(business_info, quote_ref))
            story.append(Spacer(1, 14 * fs))
            if not is_band:
                story.append(self._build_rule())
                story.append(Spacer(1, 20 * fs))
            else:
                story.append(Spacer(1, 10 * fs))
            story.append(self._build_bill_to_row(extracted_data, valid_until))
            story.append(Spacer(1, 22 * fs))
            story.append(self._build_items_table(extracted_data))
            story.append(Spacer(1, 14 * fs))
            story.append(self._build_totals_table(extracted_data))

            if extracted_data.notes:
                story.append(Spacer(1, 18 * fs))
                story.append(Paragraph(f"<b>Notes</b><br/>{extracted_data.notes}", self.styles["NotesBody"]))

            story.append(Spacer(1, 26 * fs))
            story.append(self._build_terms_and_signature(business_info))

            def draw_chrome(c, _doc):
                self._draw_page_bg(c)
                self._draw_page_border(c)
                self._draw_watermark(c)
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

    def _business_days_from_now(self, days: int) -> str:
        from datetime import timedelta
        return (datetime.now() + timedelta(days=days)).strftime("%d %b %Y")

    def _make_logo_drawing(self, size: float = 30, force_white_bg: bool = False) -> Drawing:
        """Build the BillBolo brand mark (speech bubble + checkmark) as a
        small vector Drawing - mirrors assets/logo-icon.svg. When placed on
        a solid colored band (Bold theme), the mark square stays white so
        it doesn't disappear into a same-color background."""
        d = Drawing(size, size)

        bg = Rect(0, 0, size, size, rx=size * 0.22, ry=size * 0.22)
        bg.fillColor = colors.white if force_white_bg else self.BRAND_PRIMARY
        bg.strokeColor = None
        d.add(bg)

        bubble_w, bubble_h = size * 0.6, size * 0.44
        bubble_x = size * 0.19
        bubble_y = size * 0.32

        bubble = Path(fillColor=(self.BRAND_PRIMARY if force_white_bg else colors.white), strokeColor=None)
        r = size * 0.09
        bx, by, bw, bh = bubble_x, bubble_y, bubble_w, bubble_h
        bubble.moveTo(bx + r, by)
        bubble.lineTo(bx + bw - r, by)
        bubble.curveTo(bx + bw, by, bx + bw, by, bx + bw, by + r)
        bubble.lineTo(bx + bw, by + bh - r)
        bubble.curveTo(bx + bw, by + bh, bx + bw, by + bh, bx + bw - r, by + bh)
        bubble.lineTo(bx + bw * 0.42, by + bh)
        bubble.lineTo(bx + bw * 0.22, by + bh + size * 0.12)
        bubble.lineTo(bx + bw * 0.30, by + bh)
        bubble.lineTo(bx + r, by + bh)
        bubble.curveTo(bx, by + bh, bx, by + bh, bx, by + bh - r)
        bubble.lineTo(bx, by + r)
        bubble.curveTo(bx, by, bx, by, bx + r, by)
        bubble.closePath()
        d.add(bubble)

        check = Path(
            fillColor=None,
            strokeColor=self.BRAND_DARK,
            strokeWidth=size * 0.065,
            strokeLineCap=1,
            strokeLineJoin=1,
        )
        check.moveTo(bx + bw * 0.20, by + bh * 0.50)
        check.lineTo(bx + bw * 0.40, by + bh * 0.26)
        check.lineTo(bx + bw * 0.80, by + bh * 0.72)
        d.add(check)

        return d

    def _build_letterhead(self, business_info: dict, quote_ref: str) -> Table:
        """Business identity (left) and document title/meta (right).
        In "band" theme (Bold), this renders on a solid color background
        with white text instead of a plain page."""
        is_band = self.theme.header_style == "band"
        logo = self._make_logo_drawing(30, force_white_bg=is_band)

        name = business_info.get("name", self.business_name)
        phone = business_info.get("phone", "Virtual Munshi inside WhatsApp")
        gst_number = business_info.get("gst_number")

        meta_lines = [phone]
        if gst_number:
            meta_lines.append(f"GSTIN: {gst_number}")

        biz_block = Table(
            [[logo, Paragraph(f"<b>{name}</b>", self.styles["BizName"])]],
            colWidths=[0.45 * inch, 3.5 * inch],
        )
        biz_block.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (0, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))

        left_cell = [biz_block, Spacer(1, 6), Paragraph("<br/>".join(meta_lines), self.styles["BizMeta"])]

        right_cell = [
            Paragraph("QUOTATION", self.styles["DocTitle"]),
            Spacer(1, 4),
            Paragraph(f"{quote_ref}<br/>{datetime.now().strftime('%d %b %Y')}", self.styles["DocMeta"]),
        ]

        header = Table([[left_cell, right_cell]], colWidths=[4 * inch, 2.4 * inch])
        band_style = [
            ("BACKGROUND", (0, 0), (-1, -1), self.BRAND_PRIMARY),
            ("LEFTPADDING", (0, 0), (0, 0), 14),
            ("RIGHTPADDING", (-1, 0), (-1, 0), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 14),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
        ] if is_band else [
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]
        header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")] + band_style))
        return header

    def _build_rule(self) -> Table:
        """A thin colored divider line spanning the page width."""
        rule = Table([[""]], colWidths=[6.4 * inch], rowHeights=[2])
        rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), self.BRAND_PRIMARY)]))
        return rule

    def _build_bill_to_row(self, extracted_data: ExtractedData, valid_until: str) -> Table:
        """'Bill To' details alongside quote validity, no heavy background."""
        bill_to = [
            Paragraph("BILL TO", self.styles["Label"]),
            Spacer(1, 3),
            Paragraph(extracted_data.client_name or "Client", self.styles["ClientName"]),
        ]
        validity = [
            Paragraph("VALID UNTIL", self.styles["LabelRight"]),
            Spacer(1, 3),
            Paragraph(valid_until, self.styles["ValueRight"]),
        ]

        table = Table([[bill_to, validity]], colWidths=[4 * inch, 2.4 * inch])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (0, 0), 12),
            ("LINEBEFORE", (0, 0), (0, 0), 2.5, self.BRAND_PRIMARY),
            ("RIGHTPADDING", (1, 0), (1, 0), 0),
            ("LEFTPADDING", (1, 0), (1, 0), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    def _build_items_table(self, extracted_data: ExtractedData) -> Table:
        """Build the line items table. "Formal" theme gets a solid colored
        header row (white text); other themes keep thin rules, no heavy fill."""
        fs = self.theme.font_scale
        is_formal = self.theme.header_style == "formal"
        table_data = [["ITEM", "QTY", "RATE (Rs.)", "UNIT", "AMOUNT (Rs.)"]]
        for item in extracted_data.items:
            table_data.append([
                item.item_name,
                f"{item.quantity:,.2f}",
                f"{item.rate:,.2f}",
                item.unit,
                f"{item.amount:,.2f}",
            ])

        table = Table(
            table_data,
            colWidths=[2.6 * inch, 0.75 * inch, 1 * inch, 0.65 * inch, 1.4 * inch],
        )
        n_rows = len(table_data)
        row_pad = 8 if fs >= 1 else 5.5
        style = [
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white if is_formal else self.BRAND_DARK),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8 * fs),
            ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
            ("ALIGN", (0, 0), (0, -1), "LEFT"),
            ("LINEBELOW", (0, 1), (-1, n_rows - 1), 0.5, self.BORDER),
            ("FONT", (0, 1), (-1, -1), "Helvetica", 9.5 * fs),
            ("TEXTCOLOR", (0, 1), (-1, -1), self.TEXT_DARK),
            ("TOPPADDING", (0, 0), (-1, -1), row_pad),
            ("BOTTOMPADDING", (0, 0), (-1, -1), row_pad),
            ("LEFTPADDING", (0, 0), (0, -1), 6 if is_formal else 0),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 6 if is_formal else 0),
        ]
        if is_formal:
            style.append(("BACKGROUND", (0, 0), (-1, 0), self.BRAND_DARK))
        else:
            style.append(("LINEBELOW", (0, 0), (-1, 0), 1.5, self.BRAND_PRIMARY))
        table.setStyle(TableStyle(style))
        return table

    def _build_totals_table(self, extracted_data: ExtractedData) -> Table:
        """Right-aligned totals block with the grand total as a confined
        colored callout, not a full-width band."""
        rows = [
            ["Subtotal", f"Rs. {extracted_data.total_before_gst:,.2f}"],
            [f"GST @ {extracted_data.gst_percentage}%", f"Rs. {extracted_data.gst_amount:,.2f}"],
            ["Grand Total", f"Rs. {extracted_data.grand_total:,.2f}"],
        ]
        totals = Table(rows, colWidths=[1.6 * inch, 1.6 * inch])
        totals.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("FONT", (0, 0), (-1, 1), "Helvetica", 9),
            ("TEXTCOLOR", (0, 0), (-1, 1), self.TEXT_MUTED),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, self.BORDER),
            ("LINEBELOW", (0, 1), (-1, 1), 0.5, self.BORDER),
            ("FONT", (0, 2), (-1, 2), "Helvetica-Bold", 11.5),
            ("TEXTCOLOR", (0, 2), (-1, 2), self.BRAND_DARK),
            ("LINEABOVE", (0, 2), (-1, 2), 1.5, self.BRAND_PRIMARY),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 2), (-1, 2), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))

        wrapper = Table([[totals]], colWidths=[6.4 * inch])
        wrapper.setStyle(TableStyle([
            ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return wrapper

    def _build_terms_and_signature(self, business_info: dict) -> Table:
        """Standard quotation footer content: terms + signature line."""
        terms = Paragraph(
            "<b>Terms</b><br/>"
            "This quotation is valid for 15 days from the date of issue. "
            "Prices are subject to change after expiry. Payment terms to be "
            "confirmed before work begins.",
            self.styles["TermsBody"],
        )
        signature = [
            Spacer(1, 26),
            Paragraph("_____________________________", self.styles["SignLine"]),
            Paragraph(
                f"Authorized Signatory, {business_info.get('name', self.business_name)}",
                self.styles["SignLine"],
            ),
        ]

        table = Table([[terms, signature]], colWidths=[3.8 * inch, 2.6 * inch])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return table

    def _draw_page_bg(self, c):
        """Fill the full page with the theme's background tint, if any
        (Formal theme uses a light slate tint instead of plain white)."""
        if not self.theme.page_bg_tint:
            return
        page_width, page_height = letter
        c.saveState()
        c.setFillColor(colors.HexColor(self.theme.page_bg_tint))
        c.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        c.restoreState()

    def _draw_page_border(self, c):
        """Thin border frame around the page. Bottom edge sits below the
        footer content with its own clearance so the two never collide."""
        page_width, page_height = letter
        side_margin = 0.3 * inch
        bottom_margin = 0.12 * inch

        c.saveState()
        c.setStrokeColor(self.BORDER)
        c.setLineWidth(0.75)
        c.rect(
            side_margin,
            bottom_margin,
            page_width - 2 * side_margin,
            page_height - side_margin - bottom_margin,
            fill=0,
            stroke=1,
        )
        c.restoreState()

    def _draw_watermark(self, c):
        """Very light, large diagonal BillBolo watermark behind the content."""
        page_width, page_height = letter
        c.saveState()
        c.setFont("Helvetica-Bold", 90)
        c.setFillColor(self.BRAND_PRIMARY, alpha=0.05)
        c.translate(page_width / 2, page_height / 2)
        c.rotate(40)
        c.drawCentredString(0, 0, "BillBolo")
        c.restoreState()

    def _draw_footer(self, c, is_pro: bool):
        """Draw a thin, understated footer on the canvas."""
        page_width, _ = letter
        c.saveState()
        c.setStrokeColor(self.BORDER)
        c.setLineWidth(0.5)
        c.line(0.65 * inch, FOOTER_HEIGHT, page_width - 0.65 * inch, FOOTER_HEIGHT)

        c.setFont("Helvetica-Bold", 8)
        c.setFillColor(self.BRAND_PRIMARY)
        c.drawCentredString(page_width / 2, FOOTER_HEIGHT - 0.2 * inch, "Generated in 5 seconds on BillBolo")

        if not is_pro:
            c.setFont("Helvetica", 7)
            c.setFillColor(self.TEXT_FAINT)
            c.drawCentredString(
                page_width / 2,
                FOOTER_HEIGHT - 0.33 * inch,
                "Make your own free bills on WhatsApp — wa.me/918123422932",
            )
        c.restoreState()
