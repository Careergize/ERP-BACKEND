"""In-house waste delivery register and monthly valuation."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re
from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import SharedEntry
from .recycling_purchases import READERS, number, xlsx_bytes

KEY = 'inhouse-waste-entries'
FIELDS = ['hdpeBlack', 'hdpeColor', 'hdpeClear', 'hdpePrinted', 'ldpeColor', 'ldpeClear', 'ldpePrinted', 'pp']
LABELS = ['HDPE Black', 'HDPE Color', 'HDPE Clear', 'HDPE Printed', 'LDPE Color', 'LDPE Clear', 'LDPE Printed', 'PP Clear / Printed']
GROUPS = {'Black': ['hdpeBlack'], 'Clear LD': ['ldpeClear'], 'Clear & Color': ['hdpeColor', 'hdpeClear', 'hdpePrinted', 'ldpeColor', 'ldpePrinted'], 'PP': ['pp']}

def catalog():
    entry = SharedEntry.objects.filter(pk='inhouse-waste-items').first()
    return entry.content if entry else []

def validate_items(content, previous):
    ids, names = set(), {name.casefold() for name in LABELS + list(GROUPS)}
    for row in content:
        if set(row) != {'id', 'name'} or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Item requires ID and name.')
        if not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', row['id']) or row['id'] in ids or not row['name'].strip() or row['name'] != row['name'].strip() or len(row['name']) > 200 or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', row['name']):
            raise serializers.ValidationError('Enter a unique item ID and a name of 1-200 characters.')
        if row['name'].casefold() in names:
            raise serializers.ValidationError('This waste item already exists.')
        ids.add(row['id']); names.add(row['name'].casefold())
    current = {r['id']: r for r in content}
    if any(current.get(r['id']) != r for r in previous or []):
        raise serializers.ValidationError('Saved items cannot be renamed or deleted.')

def validate_extra(values, places):
    known = {r['id'] for r in catalog()}
    if not isinstance(values, dict) or any(key not in known for key in values):
        raise serializers.ValidationError('Select saved waste items.')
    for value in values.values():
        number(value, places)


def valid_month(month):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError()
        date.fromisoformat(month + '-01')
    except (ValueError, TypeError):
        raise serializers.ValidationError('Select a valid month (YYYY-MM).')

def retain(content, previous):
    current = {r['id']: r for r in content}
    for old in previous or []:
        new = current.get(old['id'])
        if new is None:
            raise serializers.ValidationError('Records cannot be deleted.')
        if new != old and (old.get('status') == 'Voided' or not new['correctionReason'].strip()):
            raise serializers.ValidationError('Explain the correction; voided records cannot change.')

def validate_entries(content, previous):
    ids, dates = set(), set()
    old_ids = {r['id'] for r in previous or []}
    for r in content:
        if set(r) - {'extraQuantities'} != {'id', 'date', 'notes', 'status', 'correctionReason', *FIELDS} or not all(isinstance(v, str) for k, v in r.items() if k != 'extraQuantities'):
            raise serializers.ValidationError('Complete all delivery fields.')
        validate_extra(r.get('extraQuantities', {}), 3)
        try:
            if date.fromisoformat(r['date']).isoformat() != r['date']:
                raise ValueError()
        except ValueError:
            raise serializers.ValidationError('Enter a valid date.')
        if not r['id'] or len(r['id']) > 80 or r['id'] in ids or r['status'] not in {'Active', 'Voided'}:
            raise serializers.ValidationError('Use unique IDs and a valid status.')
        ids.add(r['id'])
        if r['status'] == 'Active':
            if r['date'] in dates:
                raise serializers.ValidationError('Only one active daily sheet per date; correct the existing sheet.')
            dates.add(r['date'])
        for field in FIELDS:
            number(r[field], 3)
        if len(r['notes']) > 1000 or len(r['correctionReason']) > 500 or any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', v) for v in r.values() if isinstance(v, str)):
            raise serializers.ValidationError('Check note length and unsupported characters.')
        if r['id'] not in old_ids and (r['status'] != 'Active' or r['correctionReason']):
            raise serializers.ValidationError('New sheets must be active without a correction reason.')
    retain(content, previous)

def validate_rates(content, previous):
    ids, months = set(), set()
    old_ids = {r['id'] for r in previous or []}
    for r in content:
        if set(r) - {'customRates'} != {'id', 'month', 'currency', 'blackRate', 'clearLdRate', 'mixedRate', 'ppRate', 'correctionReason'} or not all(isinstance(v, str) for k, v in r.items() if k != 'customRates'):
            raise serializers.ValidationError('Complete monthly rates and currency.')
        validate_extra(r.get('customRates', {}), 4)
        valid_month(r['month'])
        if not r['id'] or len(r['id']) > 80 or r['id'] in ids or r['month'] in months or not re.fullmatch(r'[A-Z]{3}', r['currency']):
            raise serializers.ValidationError('Use unique month/ID and a three-letter currency code.')
        ids.add(r['id']); months.add(r['month'])
        for field in ['blackRate', 'clearLdRate', 'mixedRate', 'ppRate']:
            number(r[field], 4)
        if len(r['correctionReason']) > 500 or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', r['correctionReason']):
            raise serializers.ValidationError('Check correction reason.')
        if r['id'] not in old_ids and r['correctionReason']:
            raise serializers.ValidationError('New rates need no correction reason.')
    retain(content, previous)

def calculated(r):
    values = {f: Decimal(r[f]) for f in FIELDS}
    hdpe = sum((values[f] for f in FIELDS[:4]), Decimal(0))
    ldpe = sum((values[f] for f in FIELDS[4:7]), Decimal(0))
    return {**r, 'hdpeTotal': str(hdpe), 'ldpeTotal': str(ldpe), 'ppTotal': r['pp'], 'total': str(hdpe + ldpe + values['pp'] + sum((Decimal(v) for v in r.get('extraQuantities', {}).values()), Decimal(0)))}

def report(month):
    valid_month(month)
    entry = SharedEntry.objects.filter(pk=KEY).first()
    rows = sorted([calculated(r) for r in (entry.content if entry else []) if r['date'].startswith(month)], key=lambda r: (r['date'], r['id']))
    totals = {f: sum((Decimal(r[f]) for r in rows if r['status'] == 'Active'), Decimal(0)) for f in FIELDS}
    items = catalog()
    extras = {item['id']: str(sum((Decimal(r.get('extraQuantities', {}).get(item['id'], '0')) for r in rows if r['status'] == 'Active'), Decimal(0))) for item in items}
    total_row = calculated({**{f: str(v) for f, v in totals.items()}, 'extraQuantities': extras})
    rates_entry = SharedEntry.objects.filter(pk='inhouse-waste-rates').first()
    rates = next((r for r in (rates_entry.content if rates_entry else []) if r['month'] == month), None)
    valuation = []
    for (name, fields), rate_field in zip(GROUPS.items(), ['blackRate', 'clearLdRate', 'mixedRate', 'ppRate']):
        quantity = sum((totals[f] for f in fields), Decimal(0))
        rate = rates[rate_field] if rates else None
        amount = str((quantity * Decimal(rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if rates else None
        valuation.append(dict(item=name, quantity=str(quantity), rate=rate, amount=amount))
    for item in items:
        quantity = extras[item['id']]
        rate = rates.get('customRates', {}).get(item['id']) if rates else None
        amount = str((Decimal(quantity) * Decimal(rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) if rate is not None else ('0.00' if rates and Decimal(quantity) == 0 else None)
        valuation.append(dict(item=item['name'], quantity=quantity, rate=rate, amount=amount))
    amount = str(sum((Decimal(r['amount']) for r in valuation), Decimal(0))) if rates and all(r['amount'] is not None for r in valuation) else None
    return dict(items=items, month=month, entries=rows, totals=total_row, rates=rates, valuation=valuation, amount=amount, currency=rates['currency'] if rates else None)

class InhouseWasteReport(APIView):
    permission_classes = [permissions.IsAuthenticated]
    def get(self, request):
        from .shared_entries import permitted
        if not permitted(request.user, READERS):
            raise PermissionDenied('Your account cannot view in-house waste.')
        data = report(request.query_params.get('month', ''))
        export = request.query_params.get('export')
        if not export:
            return Response(data)
        if export != 'xlsx':
            raise serializers.ValidationError('Export must be xlsx.')
        columns = [*FIELDS[:4], 'hdpeTotal', *FIELDS[4:7], 'ldpeTotal', 'pp', 'ppTotal', 'total']
        headers = ['Date', *LABELS[:4], 'HDPE Total kg', *LABELS[4:7], 'LDPE Total kg', LABELS[7], 'PP Total kg', 'Total kg', 'Status', 'Notes', 'Correction / void reason']
        rows = [['Inhouse Waste Delivery Sheet - ' + data['month']], headers]
        rows += [[r['date'], *[Decimal(r[f]) for f in columns], r['status'], r['notes'], r['correctionReason']] for r in data['entries']]
        rows += [['Active totals', *[Decimal(data['totals'][f]) for f in columns]]]
        # Insert custom item columns before the status/notes columns.
        offset = 1 + len(columns)
        rows[1][offset:offset] = [item['name'] + ' kg' for item in data['items']]
        for output, record in zip(rows[2:-1], data['entries']):
            output[offset:offset] = [Decimal(record.get('extraQuantities', {}).get(item['id'], '0')) for item in data['items']]
        rows[-1].extend(Decimal(data['totals']['extraQuantities'][item['id']]) for item in data['items'])
        valuation = [['Monthly valuation', data['currency'] or 'Rates not configured'], ['Item', 'Rate/kg', 'Total Qty kg', 'Total Amount']]
        valuation += [[r['item'], Decimal(r['rate']) if r['rate'] is not None else '', Decimal(r['quantity']), Decimal(r['amount']) if r['amount'] is not None else ''] for r in data['valuation']]
        valuation += [['TOTAL', '', Decimal(data['totals']['total']), Decimal(data['amount']) if data['amount'] is not None else '']]
        response = HttpResponse(xlsx_bytes([('Waste Delivery', rows), ('Valuation', valuation)]), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="inhouse-waste-{data["month"]}.xlsx"'
        return response
