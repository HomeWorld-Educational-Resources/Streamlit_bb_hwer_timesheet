"""
BaoBunny Timesheet PDF Generator
Black × Gold brand aesthetic, A4 portrait.
"""
import io
from datetime import datetime, timedelta
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor, white, black
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, Table, TableStyle

# ── register CJK font for Chinese tagline ────────────────────────────────────
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

# ── brand palette ─────────────────────────────────────────────────────────────
GOLD       = HexColor('#C9A84C')
DARK_GOLD  = HexColor('#9B7B2A')
BLACK_INK  = HexColor('#1A1A1A')
CHARCOAL   = HexColor('#2D2D2D')
LIGHT_GRAY = HexColor('#F5F5F3')
MID_GRAY   = HexColor('#DEDEDE')
TEXT_GRAY  = HexColor('#6B6B6B')
GOLD_TINT  = HexColor('#FBF5E6')

# ── page geometry ─────────────────────────────────────────────────────────────
PAGE_W, PAGE_H = A4          # 595.27 × 841.89 pt
ML, MR, MT, MB = 40, 40, 30, 50   # margins
CW = PAGE_W - ML - MR        # content width = 515


# ── paragraph styles ──────────────────────────────────────────────────────────
def _ps(name, font='Helvetica', size=8, leading=11, color=BLACK_INK, **kw):
    return ParagraphStyle(name, fontName=font, fontSize=size,
                          leading=leading, textColor=color, **kw)

BODY8      = _ps('body8')
BODY8G     = _ps('body8g',   color=TEXT_GRAY)
BOLD8      = _ps('bold8',    font='Helvetica-Bold')
BOLD8GOLD  = _ps('bold8g',   font='Helvetica-Bold', color=GOLD)
BODY9      = _ps('body9',    size=9, leading=12)
BOLD9      = _ps('bold9',    font='Helvetica-Bold', size=9, leading=12)


# ── canvas helpers ─────────────────────────────────────────────────────────────

def _sf(v, d=0.0):
    if v is None: return d
    try:
        s = str(v).strip()
        return float(s) if s not in ('', 'None', 'nan') else d
    except ValueError:
        return d


def _hours(v):
    return _sf(v, 0.0)


class _Draw:
    """Thin wrapper around the reportlab canvas to track the y cursor."""

    def __init__(self, c: pdf_canvas.Canvas):
        self.c = c
        self.y = PAGE_H - MT

    def rule(self, thickness=0.5, color=MID_GRAY, x=ML, w=CW):
        self.c.setStrokeColor(color)
        self.c.setLineWidth(thickness)
        self.c.line(x, self.y, x + w, self.y)

    def text(self, x, txt, font='Helvetica', size=10, color=BLACK_INK, align='left'):
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        s = str(txt)
        if align == 'center':
            self.c.drawCentredString(x, self.y, s)
        elif align == 'right':
            self.c.drawRightString(x, self.y, s)
        else:
            self.c.drawString(x, self.y, s)

    def place_table(self, table, x=ML):
        _, h = table.wrapOn(self.c, CW, PAGE_H)
        table.drawOn(self.c, x, self.y - h)
        self.y -= h

    def section_label(self, label, pad_above=14, pad_below=6):
        self.y -= pad_above
        self.text(ML, label, font='Helvetica-Bold', size=8, color=GOLD)
        # short gold rule to the right of label
        lw = self.c.stringWidth(label, 'Helvetica-Bold', 8)
        self.c.setStrokeColor(GOLD)
        self.c.setLineWidth(0.5)
        self.c.line(ML + lw + 6, self.y + 3, ML + CW, self.y + 3)
        self.y -= pad_below

    def new_page(self):
        self.c.showPage()
        self.y = PAGE_H - MT
        # repeat thin gold top rule on continuation pages
        self.c.setStrokeColor(GOLD)
        self.c.setLineWidth(2)
        self.c.line(ML, PAGE_H - 20, ML + CW, PAGE_H - 20)
        self.y = PAGE_H - 28


# ── table builders ─────────────────────────────────────────────────────────────

def _act_table(activities):
    """Build the activities table; returns (Table, total_hours)."""
    # column widths: Activity, Mon‥Fri, Total, Output
    day_w  = 28
    tot_w  = 40
    out_w  = 150
    act_w  = CW - 5 * day_w - tot_w - out_w   # ~185

    header = [
        Paragraph('<b>Activity</b>', BOLD8GOLD),
        Paragraph('<b>Mon</b>', BOLD8GOLD),
        Paragraph('<b>Tue</b>', BOLD8GOLD),
        Paragraph('<b>Wed</b>', BOLD8GOLD),
        Paragraph('<b>Thu</b>', BOLD8GOLD),
        Paragraph('<b>Fri</b>', BOLD8GOLD),
        Paragraph('<b>Total</b>', BOLD8GOLD),
        Paragraph('<b>Output Description</b>', BOLD8GOLD),
    ]
    rows = [header]
    total_hours = 0.0

    for a in activities:
        mon = _hours(a.get('mon'))
        tue = _hours(a.get('tue'))
        wed = _hours(a.get('wed'))
        thu = _hours(a.get('thu'))
        fri = _hours(a.get('fri'))
        row_total = mon + tue + wed + thu + fri
        total_hours += row_total

        def _n(v): return str(v) if v else ''

        rows.append([
            Paragraph(a.get('activity', '') or '', BODY8),
            _n(mon), _n(tue), _n(wed), _n(thu), _n(fri),
            str(row_total) if row_total else '',
            Paragraph(a.get('output_description', '') or '', BODY8),
        ])

    # Totals row
    rows.append([
        Paragraph('<b>Total Hours</b>', BOLD8),
        '', '', '', '', '',
        Paragraph(f'<b>{total_hours}</b>', BOLD8),
        '',
    ])

    col_widths = [act_w, day_w, day_w, day_w, day_w, day_w, tot_w, out_w]
    t = Table(rows, colWidths=col_widths, repeatRows=1)

    cmd = [
        # Header
        ('BACKGROUND',   (0, 0), (-1, 0), BLACK_INK),
        ('LINEBELOW',    (0, 0), (-1, 0), 1.5, GOLD),
        ('ALIGN',        (0, 0), (-1, 0), 'CENTER'),
        ('VALIGN',       (0, 0), (-1, -1), 'MIDDLE'),
        # Body alignment
        ('ALIGN',        (1, 1), (6, -1), 'CENTER'),
        ('ALIGN',        (0, 1), (0, -1), 'LEFT'),
        ('ALIGN',        (7, 1), (7, -1), 'LEFT'),
        # Totals row
        ('BACKGROUND',   (0, -1), (-1, -1), GOLD_TINT),
        ('LINEABOVE',    (0, -1), (-1, -1), 0.75, GOLD),
        ('TEXTCOLOR',    (6, -1), (6, -1), DARK_GOLD),
        # Grid
        ('GRID',         (0, 0), (-1, -1), 0.25, MID_GRAY),
        # Padding
        ('TOPPADDING',   (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING',(0, 0), (-1, -1), 4),
        ('LEFTPADDING',  (0, 0), (-1, -1), 5),
        ('RIGHTPADDING', (0, 0), (-1, -1), 5),
    ]
    # Alternating row backgrounds
    for i in range(1, len(rows) - 1):
        bg = white if i % 2 == 1 else LIGHT_GRAY
        cmd.append(('BACKGROUND', (0, i), (-1, i), bg))

    t.setStyle(TableStyle(cmd))
    return t, total_hours


def _summary_boxes(d: _Draw, items, is_dark_last=True, box_gap=4):
    """Draw a row of KPI boxes."""
    n = len(items)
    bw = (CW - box_gap * (n - 1)) / n
    bh = 46
    for i, (label, value) in enumerate(items):
        bx = ML + i * (bw + box_gap)
        by = d.y - bh
        dark = is_dark_last and (i == n - 1)
        bg = BLACK_INK if dark else LIGHT_GRAY
        fg_val = white if dark else BLACK_INK
        d.c.setFillColor(bg)
        d.c.roundRect(bx, by, bw, bh, 5, fill=1, stroke=0)
        # gold top accent line on each box
        d.c.setStrokeColor(GOLD)
        d.c.setLineWidth(1.5)
        d.c.line(bx + 6, by + bh, bx + bw - 6, by + bh)
        # label
        d.c.setFont('Helvetica-Bold', 6.5)
        d.c.setFillColor(GOLD)
        d.c.drawCentredString(bx + bw / 2, by + bh - 13, label.upper())
        # value
        d.c.setFont('Helvetica-Bold', 13)
        d.c.setFillColor(fg_val)
        d.c.drawCentredString(bx + bw / 2, by + 14, str(value))
    d.y -= bh + 6


def _payment_table(payments):
    """Build the payment history table. Returns Table or None."""
    active = [p for p in payments if _sf(p.get('amount'), None) is not None]
    if not active:
        return None, 0.0

    total_paid = sum(_sf(p['amount']) for p in active)

    col_w = [28, 80, 92, CW - 28 - 80 - 92]
    header = [
        Paragraph('<b>#</b>', BOLD8GOLD),
        Paragraph('<b>Amount</b>', BOLD8GOLD),
        Paragraph('<b>Payment Date</b>', BOLD8GOLD),
        Paragraph('<b>Notes</b>', BOLD8GOLD),
    ]
    rows = [header]
    for i, p in enumerate(active, 1):
        rows.append([
            str(i),
            f"${_sf(p['amount']):,.2f}",
            p.get('payment_date', '') or '',
            Paragraph(p.get('notes', '') or '', BODY8),
        ])
    rows.append([
        '',
        Paragraph(f'<b>${total_paid:,.2f}</b>', BOLD8),
        Paragraph('<b>Total Paid</b>', BOLD8),
        '',
    ])

    t = Table(rows, colWidths=col_w)
    cmd = [
        ('BACKGROUND',    (0, 0), (-1, 0), BLACK_INK),
        ('LINEBELOW',     (0, 0), (-1, 0), 1.5, GOLD),
        ('ALIGN',         (0, 0), (2, -1), 'CENTER'),
        ('ALIGN',         (3, 0), (3, -1), 'LEFT'),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
        ('BACKGROUND',    (0, -1), (-1, -1), GOLD_TINT),
        ('LINEABOVE',     (0, -1), (-1, -1), 0.75, GOLD),
        ('TEXTCOLOR',     (1, -1), (1, -1), DARK_GOLD),
        ('GRID',          (0, 0), (-1, -1), 0.25, MID_GRAY),
        ('TOPPADDING',    (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
        ('LEFTPADDING',   (0, 0), (-1, -1), 5),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 5),
    ]
    for i in range(1, len(rows) - 1):
        bg = white if i % 2 == 1 else LIGHT_GRAY
        cmd.append(('BACKGROUND', (0, i), (-1, i), bg))
    t.setStyle(TableStyle(cmd))
    return t, total_paid


# ── main entry ────────────────────────────────────────────────────────────────

def generate_pdf(customer_name, company, week_dt, week_number,
                 rate, prior_bal, max_spend, contract_note, footnote,
                 activities, payments, logo_path=None):
    """
    activities  – list of dicts: activity, mon, tue, wed, thu, fri, output_description
    payments    – list of dicts: amount, payment_date, notes
    Returns bytes.
    """
    buf = io.BytesIO()
    c = pdf_canvas.Canvas(buf, pagesize=A4)
    c.setTitle(f'{customer_name} – Timesheet')
    c.setAuthor('BaoBunny')
    d = _Draw(c)

    # ── thin gold top bar ─────────────────────────────────────────────────────
    c.setFillColor(BLACK_INK)
    c.rect(0, PAGE_H - 48, PAGE_W, 48, fill=1, stroke=0)
    c.setFillColor(GOLD)
    c.setLineWidth(0)
    c.rect(0, PAGE_H - 50, PAGE_W, 2, fill=1, stroke=0)

    # Logo inside the black bar
    logo_rendered = False
    if logo_path and Path(logo_path).exists():
        try:
            logo_size = 44
            c.drawImage(str(logo_path), ML, PAGE_H - 48 + 2,
                        width=logo_size, height=logo_size,
                        preserveAspectRatio=True, anchor='sw', mask='auto')
            logo_rendered = True
        except Exception:
            pass

    logo_offset = 52 if logo_rendered else 0

    # "TIMESHEET" in the black bar
    c.setFont('Helvetica-Bold', 22)
    c.setFillColor(white)
    c.drawString(ML + logo_offset, PAGE_H - 32, 'TIMESHEET')
    c.setFont('Helvetica', 9)
    c.setFillColor(GOLD)
    c.drawString(ML + logo_offset, PAGE_H - 44, f'{week_dt.strftime("%B %Y")}  ·  Week {week_number}')

    # Customer name (right side of bar)
    c.setFont('Helvetica-Bold', 11)
    c.setFillColor(white)
    c.drawRightString(PAGE_W - MR, PAGE_H - 28, customer_name)
    if company:
        c.setFont('Helvetica', 8)
        c.setFillColor(GOLD)
        c.drawRightString(PAGE_W - MR, PAGE_H - 41, company)

    d.y = PAGE_H - 58  # below the black bar

    # ── customer info block ───────────────────────────────────────────────────
    d.y -= 12

    end_dt = week_dt + timedelta(days=4)
    week_range = f"{week_dt.strftime('%b %d')} – {end_dt.strftime('%b %d, %Y')}"

    left_items = [
        ('Week',        f'Week {week_number}  ·  {week_range}'),
        ('Hourly Rate', f'${rate:,.2f} / hr'),
        ('Prior Balance', f'${prior_bal:,.2f}'),
    ]
    if max_spend:
        left_items.append(('Max Contract Spend', f'${max_spend:,.2f}'))

    right_items = []
    if contract_note:
        right_items.append(('Contract Note', contract_note))
    if footnote:
        right_items.append(('Footnote', footnote))

    def _info_rows(items, x, col_w):
        iy = d.y
        line_h = 11
        for label, val in items:
            c.setFont('Helvetica-Bold', 6.5)
            c.setFillColor(GOLD)
            c.drawString(x, iy, label.upper())
            c.setFont('Helvetica', 9)
            c.setFillColor(BLACK_INK)
            lines = simpleSplit(str(val), 'Helvetica', 9, col_w) or ['']
            for j, line in enumerate(lines):
                c.drawString(x, iy - 12 - j * line_h, line)
            iy -= 14 + len(lines) * line_h
        return iy

    col1_w = CW * 0.52
    col2_w = CW * 0.46
    y_left  = _info_rows(left_items,  ML,             col1_w)
    y_right = _info_rows(right_items, ML + col1_w + 6, col2_w)

    d.y = min(y_left, y_right) - 8
    d.rule(thickness=1.5, color=GOLD)

    # ── activities ────────────────────────────────────────────────────────────
    d.section_label('Activities')
    act_table, total_hours = _act_table(activities)
    d.place_table(act_table)

    # ── week summary ──────────────────────────────────────────────────────────
    amount_week = total_hours * rate
    total_paid_global = sum(_sf(p.get('amount')) for p in payments
                            if _sf(p.get('amount'), None) is not None)
    total_due = max(0.0, prior_bal + amount_week - total_paid_global)

    d.section_label('Week Summary', pad_above=16)
    _summary_boxes(d, [
        ('Total Hours',     f'{total_hours:g} hrs'),
        ('Hourly Rate',     f'${rate:,.2f}'),
        ('Amount This Week', f'${amount_week:,.2f}'),
        ('Total Due',       f'${total_due:,.2f}'),
    ])

    # ── payment history ───────────────────────────────────────────────────────
    pmt_table, total_paid = _payment_table(payments)
    if pmt_table:
        # Check if enough space; if not, start a new page
        _, pmt_h = pmt_table.wrapOn(c, CW, PAGE_H)
        need = pmt_h + 130  # payments + balance boxes + footer
        if d.y - need < MB + 20:
            d.new_page()

        d.rule(thickness=0.75, color=GOLD)
        d.section_label('Payment History', pad_above=14)
        d.place_table(pmt_table)

        # Balance summary
        d.section_label('Balance Summary', pad_above=14)
        _summary_boxes(d, [
            ('Total Paid',      f'${total_paid:,.2f}'),
            ('Amount This Week', f'${amount_week:,.2f}'),
            ('Prior Balance',   f'${prior_bal:,.2f}'),
            ('Outstanding',     f'${total_due:,.2f}'),
        ])

    # ── footer ────────────────────────────────────────────────────────────────
    fy = MB + 24
    c.setStrokeColor(GOLD)
    c.setLineWidth(1.5)
    c.line(ML, fy, ML + CW, fy)

    # Footer note: show both contract_note and footnote if they differ, else just one
    footer_lines = []
    if contract_note and footnote and contract_note != footnote:
        footer_lines += simpleSplit(f'Note: {contract_note}', 'Helvetica', 7.5, CW * 0.72)
        footer_lines += simpleSplit(f'Footnote: {footnote}', 'Helvetica', 7.5, CW * 0.72)
    elif footnote:
        footer_lines = simpleSplit(footnote, 'Helvetica', 7.5, CW * 0.72)
    elif contract_note:
        footer_lines = simpleSplit(contract_note, 'Helvetica', 7.5, CW * 0.72)

    note_line_h = 10
    c.setFont('Helvetica', 7.5)
    c.setFillColor(TEXT_GRAY)
    for i, line in enumerate(footer_lines):
        c.drawString(ML, fy - 12 - i * note_line_h, line)

    # Chinese tagline aligned with first footer line
    try:
        c.setFont('STSong-Light', 9)
        c.setFillColor(GOLD)
        c.drawRightString(PAGE_W - MR, fy - 12, '少走弯路 · 更快向前')
    except Exception:
        c.setFont('Helvetica', 8)
        c.setFillColor(GOLD)
        c.drawRightString(PAGE_W - MR, fy - 12, 'BaoBunny')

    # BaoBunny brand — below all note lines
    brand_y = fy - 12 - max(len(footer_lines) - 1, 0) * note_line_h - 14
    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(MID_GRAY)
    c.drawCentredString(PAGE_W / 2, brand_y,
                        f'Generated by BaoBunny  ·  {datetime.now().strftime("%Y-%m-%d %H:%M")}')

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
