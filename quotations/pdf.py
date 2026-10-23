from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.conf import settings
from num2words import num2words
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from .serializers import money


def build_pdf(document, approved=False):
    output = BytesIO()
    width = A4[0] - 28 * mm
    normal = ParagraphStyle('normal', fontName='Helvetica', fontSize=9, leading=12, spaceAfter=2, wordWrap='CJK')
    small = ParagraphStyle('small', parent=normal, fontSize=8, leading=11)
    bold = ParagraphStyle('bold', parent=normal, fontName='Helvetica-Bold')
    right = ParagraphStyle('right', parent=normal, alignment=TA_RIGHT)
    center = ParagraphStyle('center', parent=bold, alignment=TA_CENTER)

    def p(value, style=normal):
        return Paragraph(escape(str(value)).replace('\n', '<br/>'), style)

    def table(rows, widths, grid=True):
        result = Table(rows, colWidths=widths, hAlign='LEFT', splitInRow=1)
        rules = [('VALIGN', (0, 0), (-1, -1), 'TOP'), ('LEFTPADDING', (0, 0), (-1, -1), 5), ('RIGHTPADDING', (0, 0), (-1, -1), 5), ('TOPPADDING', (0, 0), (-1, -1), 5), ('BOTTOMPADDING', (0, 0), (-1, -1), 5)]
        if grid:
            rules.append(('GRID', (0, 0), (-1, -1), .55, colors.black))
        result.setStyle(TableStyle(rules))
        return result

    d = document
    company, customer = d['company'], d['customer']
    story = []
    title = 'Sales Quotation / Proforma Invoice'
    story.append(table([[p(title, center)]], [width]))
    logo = Path(settings.QUOTATION_LOGO_PATH)
    company_block = []
    if logo.is_file():
        image = Image(str(logo))
        image.drawHeight = min(26 * mm, image.imageHeight * 85 * mm / image.imageWidth)
        image.drawWidth = image.drawHeight * image.imageWidth / image.imageHeight
        image.hAlign = 'LEFT'
        company_block.extend([image, Spacer(1, 4 * mm)])
    company_block.extend([p(company['tradingName'], bold), p(f"(REGD. NAME: {company['legalName']})", small), p(company['address']), p(f"Mob: {company['telephone']}"), p(company['email']), p(f"TIN: {company['tin']}", bold)])
    meta = [p(f"Date: {d['date']}"), p(f"No: {d['number']}  / Rev {d['revision']}", bold), p(f"Validity: {d['validityDays']} days"), p(f"Delivery: {str(d['deliveryDays']) + ' days' if 'deliveryDays' in d else d['terms']['deliveryPeriod'] or 'To be agreed'}"), Spacer(1, 5 * mm), p('To:', bold), p(customer['name'], bold), p(customer['billingAddress']), Spacer(1, 3 * mm), p(f"ATTN: {customer['contactPerson']}", bold), p(f"Phone: {customer['telephone']}"), p(f"Email: {customer['email']}")]
    story.append(table([[company_block, meta]], [width * .54, width * .46]))
    if not approved:
        story.append(table([[p('DRAFT - NOT APPROVED FOR SENDING', center)]], [width]))
    if d['documentType'] == 'Proforma Invoice':
        story.append(table([[p('Proforma Invoice - commercial pre-invoice, not a tax/fiscal invoice', small)]], [width]))
    price_basis = 'incl. VAT' if d['taxMode'] == 'inclusive' else 'excl. VAT'
    headers = ['Item #', 'Particulars', 'Qty', 'Unit', f'Unit Price ({price_basis})', f"Qty x Price ({d['currency']})"]
    rows = [[p(h, bold) for h in headers]]
    for index, line in enumerate(d['lines'], 1):
        desc = [p(line['group'], bold)] if line['group'] else []
        desc += [p(line['description'], bold)]
        if line['specifications']:
            desc.append(p(line['specifications'], small))
        price = f"{Decimal(line['unitPrice']):,.6f}".rstrip('0')
        price = price + '00' if price.endswith('.') else price + '0' if len(price.rsplit('.', 1)[-1]) == 1 else price
        rows.append([p(index), desc, p(format(Decimal(line['quantity']).normalize(), 'f'), right), p(line['unit']), p(price, right), p(f"{money(Decimal(line['quantity']) * Decimal(line['unitPrice'])):,.2f}", right)])
    items = table(rows, [width * ratio for ratio in [.065, .385, .09, .08, .17, .21]])
    items.repeatRows = 1
    story.append(items)
    totals = d['totals']
    summary = []
    for label, key in [('Subtotal (entered price basis)', 'subtotal'), ('Discount (entered price basis)', 'discount'), ('Taxable amount', 'taxableAmount'), (f"VAT {d['taxRate']}% ({d['taxMode']} pricing)", 'tax'), (f"Total Amount inclusive VAT {d['taxRate']}%", 'total')]:
        summary.append(table([[p(label, bold if key == 'total' else normal), p(f"{Decimal(totals[key]):,.2f}", right)]], [width * .79, width * .21]))
    total = Decimal(totals['total'])
    if totals.get('led'):
        summary.insert(-1, table([[p('LED', normal), p(f"{Decimal(totals['led']):,.2f}", right)]], [width * .79, width * .21]))
    whole = int(total)
    cents = int((total - whole) * 100)
    words = num2words(whole).capitalize() + (f' and {cents:02d}/100' if cents else '') + f" {d['currency']} only."
    summary.append(table([[p('Amount in Words: ' + words, bold)]], [width]))
    story.append(KeepTogether(summary))
    bank_block = [p('Bank Details', center)]
    for bank in company['banks']:
        bank_block += [p(bank['accountName']), p(f"{bank['bank']} - {bank['branch']}"), p(f"{bank['accountNumber']} ({bank['currency']})", bold), Spacer(1, 3 * mm)]
    if not company['banks']:
        bank_block.append(p('To be supplied'))
    sign = [Spacer(1, 12 * mm), p('Authorised Signatory', center), Spacer(1, 8 * mm), p(company['signatory'] or '________________________', center)]
    story.append(KeepTogether([table([[bank_block, sign]], [width * .5, width * .5])]))
    terms = d['terms']
    term_block = [p('Terms & Conditions', bold)]
    for label, key in [('Price Validity', 'priceValidity'), ('Delivery', 'deliveryPeriod'), ('Payment Terms', 'paymentTerms'), ('Price revision', 'revisionClause'), ('Additional Terms', 'additionalTerms'), ('Remarks', 'remarks')]:
        if terms[key]:
            term_block.append(p(f'{label}: {terms[key]}', small))
    for key, label in [('advancePercent', 'Advance payment %'), ('creditDays', 'Credit days')]:
        if key in terms:
            term_block.append(p(f'{label}: {terms[key]}', small))
    if 'quantityTolerance' in terms:
        tolerance = format(Decimal(terms['quantityTolerance']).normalize(), 'f')
        term_block.append(p(f'Disclaimer: The final quantity shall be +/- {tolerance}% of the ordered quantity.', small))
    story.append(table([[term_block]], [width]))

    def footer(canvas, doc):
        canvas.setFont('Helvetica', 7)
        canvas.drawString(14 * mm, 10 * mm, f"{d['number']} | Revision {d['revision']}")
        canvas.drawRightString(A4[0] - 14 * mm, 10 * mm, f'Page {doc.page}')

    SimpleDocTemplate(output, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=12 * mm, bottomMargin=18 * mm, title=f"{d['documentType']} {d['number']}", author=company['tradingName']).build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
