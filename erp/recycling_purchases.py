"""Manual waste purchases. Reporting only: no inferred stock or granules posting."""
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
import re
from xml.sax.saxutils import escape
from zipfile import ZipFile, ZIP_DEFLATED

from django.http import HttpResponse
from rest_framework import serializers, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import PermissionDenied

from .models import SharedEntry

MATERIALS = ['Tank', 'LD waste', 'LD Green', 'Ld Printed waste IFB', 'HD C black',
             'HD white', 'HD Yellow', 'LD blue', 'HD clear', 'HD Green',
             'Jumbo cup', 'PEPSI', 'packing bags', 'LD Brown', 'Nava Pack']
CURRENCIES = {'UGX', 'USD', 'NGN', 'GHS', 'ZAR'}
READERS = {'admin', 'accounts', 'store_keeper', 'recycling_operator', 'production_manager', 'project_manager'}
WRITERS = {'admin', 'accounts', 'store_keeper', 'recycling_operator'}


def validate_materials(content, previous):
    names = {name.casefold() for name in MATERIALS}
    ids = set()
    for row in content:
        if set(row) != {'id', 'name'} or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Material requires an ID and name.')
        name = row['name']
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids or not name.strip() or name != name.strip() or len(name) > 120 or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', name):
            raise serializers.ValidationError('Enter a unique material ID and a name of 1–120 characters.')
        if name.casefold() in names:
            raise serializers.ValidationError('This material name already exists.')
        ids.add(row['id']); names.add(name.casefold())
    current = {r['id']: r for r in content}
    if any(current.get(r['id']) != r for r in previous or []):
        raise serializers.ValidationError('Saved materials cannot be removed or renamed; purchase history retains its material identity.')


def number(value, places, positive=False):
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d+(?:\.\d+)?', value):
            raise ValueError()
        result = Decimal(value)
        if not result.is_finite() or result < 0 or result > Decimal('1000000000') or (positive and result == 0):
            raise ValueError()
        if result != result.quantize(Decimal(1).scaleb(-places)):
            raise ValueError()
        return result
    except (ValueError, InvalidOperation):
        raise serializers.ValidationError(f'Enter a valid {"positive" if positive else "nonnegative"} number with at most {places} decimal places (maximum 1 billion).')


def amount(row):
    return (Decimal(row['quantity']) * Decimal(row['unitPrice'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def validate_purchases(content, previous, user):
    ids = set()
    catalog = SharedEntry.objects.filter(pk='recycling-materials').first()
    materials = set(MATERIALS) | {r['name'] for r in (catalog.content if catalog else [])}
    required = {'id', 'date', 'material', 'supplier', 'quantity', 'unit', 'unitPrice', 'currency', 'notes', 'status', 'correctionReason'}
    for row in content:
        if set(row) != required or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Complete the purchase fields with text and decimal values.')
        if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', v) for v in row.values()):
            raise serializers.ValidationError('Remove unsupported control characters from purchase fields.')
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids:
            raise serializers.ValidationError('Purchase IDs must be unique.')
        ids.add(row['id'])
        try:
            date.fromisoformat(row['date'])
        except ValueError:
            raise serializers.ValidationError('Enter a valid purchase date.')
        if row['material'] not in materials or row['unit'] not in {'kg', 'pcs'} or row['currency'] not in CURRENCIES:
            raise serializers.ValidationError('Select a supported material, unit and currency.')
        if not row['supplier'].strip() or len(row['supplier']) > 200 or len(row['notes']) > 1000 or len(row['correctionReason']) > 500:
            raise serializers.ValidationError('Supplier is required (maximum 200 characters); notes maximum 1000, correction reason maximum 500.')
        if row['status'] not in {'Active', 'Voided'}:
            raise serializers.ValidationError('Invalid purchase status.')
        number(row['quantity'], 3, positive=True)
        number(row['unitPrice'], 4)
    current = {row['id']: row for row in content}
    old = {row['id']: row for row in previous or []}
    for key, row in old.items():
        if key not in current:
            raise serializers.ValidationError('Purchases cannot be deleted; void them with a reason.')
        if current[key] != row:
            if row['status'] == 'Voided':
                raise serializers.ValidationError('Voided purchases cannot be changed.')
            if not current[key]['correctionReason'].strip():
                raise serializers.ValidationError('Explain the correction or void. Earlier versions remain in management history.')
    for key, row in current.items():
        if key not in old and (row['status'] != 'Active' or row['correctionReason']):
            raise serializers.ValidationError('New purchases must be active with no correction reason.')


def report(month):
    if not re.fullmatch(r'\d{4}-\d{2}', month):
        raise serializers.ValidationError('Select a reporting month (YYYY-MM).')
    try:
        date.fromisoformat(month + '-01')
    except ValueError:
        raise serializers.ValidationError('Invalid reporting month.')
    entry = SharedEntry.objects.filter(pk='recycling-purchases').first()
    rows = sorted([r for r in (entry.content if entry else []) if r['date'].startswith(month)], key=lambda r: (r['date'], r['id']))
    groups = defaultdict(lambda: [Decimal(0), Decimal(0)])
    totals = defaultdict(lambda: Decimal(0))
    for row in rows:
        if row['status'] != 'Active':
            continue
        group = groups[(row['material'], row['unit'], row['currency'])]
        group[0] += Decimal(row['quantity'])
        group[1] += amount(row)
        totals[row['currency']] += amount(row)
    summary = [{'material': key[0], 'unit': key[1], 'currency': key[2], 'quantity': str(values[0]), 'amount': str(values[1])}
               for key, values in sorted(groups.items())]
    return {'month': month, 'version': entry.version if entry else 0, 'entries': [{**r, 'amount': str(amount(r))} for r in rows],
            'summary': summary, 'totals': {key: str(value) for key, value in sorted(totals.items())}}


def report_tables(data):
    summary = [['Waste purchases — ' + data['month']], ['Material', 'Unit', 'Currency', 'Quantity', 'Amount']]
    summary += [[r['material'], r['unit'], r['currency'], Decimal(r['quantity']), Decimal(r['amount'])] for r in data['summary']]
    summary += [['Total purchase', '', currency, '', Decimal(total)] for currency, total in data['totals'].items()]
    detail = [['Purchase entries — ' + data['month']], ['Date', 'Material', 'Supplier', 'Quantity', 'Unit', 'Unit price', 'Currency', 'Amount', 'Status', 'Notes', 'Correction / void reason', 'Record ID']]
    detail += [[r['date'], r['material'], r['supplier'], Decimal(r['quantity']), r['unit'], Decimal(r['unitPrice']), r['currency'], Decimal(r['amount']), r['status'], r['notes'], r['correctionReason'], r['id']] for r in data['entries']]
    return [('Monthly Purchase', summary), ('Purchase Entries', detail)]


def xlsx_bytes(tables):
    """Small native OOXML export; strings are literal cells, never formulas."""
    output = BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, len(tables)+1)) + '</Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(tables, 1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(tables)+1)) + '</Relationships>')
        for i, (_, rows) in enumerate(tables, 1):
            body = ''.join('<row>' + ''.join(f'<c t="n"><v>{value}</v></c>' if isinstance(value, Decimal) else '<c t="inlineStr"><is><t xml:space="preserve">' + escape(str(value)) + '</t></is></c>' for value in row) + '</row>' for row in rows)
            z.writestr(f'xl/worksheets/sheet{i}.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="2" topLeftCell="A3" state="frozen"/></sheetView></sheetViews><cols><col min="1" max="12" width="24" customWidth="1"/></cols><sheetData>' + body + '</sheetData></worksheet>')
    return output.getvalue()


def pdf_bytes(data):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    output = BytesIO()
    styles = getSampleStyleSheet()
    styles['BodyText'].fontSize = 8
    styles['BodyText'].leading = 11
    story = [Paragraph('Waste Purchases — ' + data['month'], styles['Title']),
             Paragraph(f'Saved version {data["version"]}. Active entries only in totals. Quantities remain separate by unit; amounts by currency.', styles['BodyText']), Spacer(1, 14)]
    def table(rows, widths):
        cells = [[Paragraph(escape(str(c)), styles['BodyText']) for c in row] for row in rows]
        t = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e7f0f0')), ('GRID', (0,0), (-1,-1), .3, colors.HexColor('#cbd5e1')), ('VALIGN', (0,0), (-1,-1), 'TOP'), ('TOPPADDING', (0,0), (-1,-1), 6), ('BOTTOMPADDING', (0,0), (-1,-1), 6)]))
        return t
    summary = report_tables(data)[0][1][1:]
    story.append(table(summary, [270, 70, 80, 140, 190]))
    story += [PageBreak(), Paragraph('Purchase entries', styles['Heading1'])]
    detail = [['Date', 'Material / Supplier', 'Quantity', 'Unit price', 'Currency', 'Amount', 'Status / Notes']]
    for r in data['entries']:
        detail.append([r['date'], r['material'] + ' / ' + r['supplier'], r['quantity'] + ' ' + r['unit'], r['unitPrice'], r['currency'], r['amount'], ' · '.join(filter(None, [r['status'], r['notes'], r['correctionReason']]))])
    story.append(table(detail, [65, 160, 85, 85, 60, 100, 195]))
    def footer(canvas, doc):
        canvas.setFont('Helvetica', 8)
        canvas.drawString(36, 20, f'Waste purchases {data["month"]} · Page {doc.page}')
    SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=36, rightMargin=36, topMargin=30, bottomMargin=35).build(story, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()


class PurchaseReport(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .shared_entries import permitted
        if not permitted(request.user, READERS):
            raise PermissionDenied('Your account cannot view recycling purchases.')
        data = report(request.query_params.get('month', ''))
        export = request.query_params.get('export')
        if not export:
            return Response(data)
        if export == 'xlsx':
            response = HttpResponse(xlsx_bytes(report_tables(data)), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        elif export == 'pdf':
            response = HttpResponse(pdf_bytes(data), content_type='application/pdf')
        else:
            raise serializers.ValidationError('Export must be xlsx or pdf.')
        response['Content-Disposition'] = f'attachment; filename="waste-purchases-{data["month"]}.{export}"'
        return response
