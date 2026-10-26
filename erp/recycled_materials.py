"""Module 2: daily recycled-material bag register, based on the July reference.

This register does not infer raw-stock transfers or consumption of purchased waste.
"""
import calendar
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import re

from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SharedEntry
from .recycling_purchases import CURRENCIES, READERS, number, xlsx_bytes

KEY = 'recycled-material-entries'
CATALOG_KEY = 'recycled-material-catalog'
KG_PER_BAG = Decimal('40')
MATERIALS = ['HDPE BlacK_Inhouse', 'HDPE Clear Jerrycan', 'HDPE White', 'HDPE Yellow',
             'HD Black jerrican RP', 'LDPE Clear_Inhouse', 'LDPE Brown_Inhouse',
             'LDPE Blue (Jumbo Cap)', 'LDPE Green', 'LDPE Clear', 'Tank', 'HDPE Pipe',
             'LD Wateya', 'HD Pure white']


def validate_catalog(content, previous):
    # Append-only names, with this module's distinct base catalog.
    names = {name.casefold() for name in MATERIALS}
    # The purchase validator reserves purchase names; use equivalent checks here
    # without accidentally prohibiting a legitimate recycled-material name.
    ids = set()
    for row in content:
        if set(row) != {'id', 'name'} or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Material requires an ID and name.')
        name = row['name']
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids or not name.strip() or name != name.strip() or len(name) > 120 or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', name) or name.casefold() in names:
            raise serializers.ValidationError('Enter a unique ID and material name (maximum 120 characters).')
        ids.add(row['id']); names.add(name.casefold())
    current = {r['id']: r for r in content}
    if any(current.get(r['id']) != r for r in previous or []):
        raise serializers.ValidationError('Saved materials cannot be removed or renamed.')


def validate_entries(content, previous):
    catalog = SharedEntry.objects.filter(pk=CATALOG_KEY).first()
    materials = set(MATERIALS) | {r['name'] for r in (catalog.content if catalog else [])}
    required = {'id', 'date', 'material', 'bags', 'pricePerKg', 'currency', 'notes', 'status', 'correctionReason'}
    ids = set()
    for row in content:
        if set(row) != required or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Complete the bag entry fields using text and decimal values.')
        if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', v) for v in row.values()):
            raise serializers.ValidationError('Remove unsupported control characters.')
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids:
            raise serializers.ValidationError('Entry IDs must be unique.')
        ids.add(row['id'])
        try:
            if date.fromisoformat(row['date']).isoformat() != row['date']:
                raise ValueError()
        except ValueError:
            raise serializers.ValidationError('Enter a valid date (YYYY-MM-DD).')
        if row['material'] not in materials or row['currency'] not in CURRENCIES or row['status'] not in {'Active', 'Voided'}:
            raise serializers.ValidationError('Select a supported material, currency and status.')
        number(row['bags'], 0, positive=True)
        number(row['pricePerKg'], 4)
        if len(row['notes']) > 1000 or len(row['correctionReason']) > 500:
            raise serializers.ValidationError('Notes maximum 1000 characters; correction reason maximum 500.')
    current = {r['id']: r for r in content}
    old = {r['id']: r for r in previous or []}
    for key, row in old.items():
        if key not in current:
            raise serializers.ValidationError('Entries cannot be deleted; void with a reason.')
        if current[key] != row:
            if row['status'] == 'Voided':
                raise serializers.ValidationError('Voided entries cannot be changed.')
            if not current[key]['correctionReason'].strip():
                raise serializers.ValidationError('Explain the correction or void.')
    for key, row in current.items():
        if key not in old and (row['status'] != 'Active' or row['correctionReason']):
            raise serializers.ValidationError('New entries must be active with no correction reason.')


def calculated(row):
    kg = Decimal(row['bags']) * KG_PER_BAG
    return {**row, 'kg': str(kg), 'amount': str((kg * Decimal(row['pricePerKg'])).quantize(Decimal('.01'), rounding=ROUND_HALF_UP))}


def report(month):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError()
        first = date.fromisoformat(month + '-01')
    except ValueError:
        raise serializers.ValidationError('Select a valid reporting month (YYYY-MM).')
    entry = SharedEntry.objects.filter(pk=KEY).first()
    rows = [calculated(r) for r in (entry.content if entry else []) if r['date'].startswith(month)]
    rows.sort(key=lambda r: (r['date'], r['id']))
    groups = {}
    totals = defaultdict(lambda: Decimal(0))
    bags = Decimal(0)
    for r in rows:
        if r['status'] != 'Active':
            continue
        # Keep different prices and currencies separate; never invent a monthly rate.
        key = (r['material'], r['currency'], Decimal(r['pricePerKg']))
        group = groups.setdefault(key, {'material': key[0], 'currency': key[1], 'pricePerKg': str(key[2]), 'daily': defaultdict(lambda: Decimal(0)), 'bags': Decimal(0), 'kg': Decimal(0), 'amount': Decimal(0)})
        group['daily'][r['date']] += Decimal(r['bags'])
        for field in ['bags', 'kg', 'amount']:
            group[field] += Decimal(r[field])
        bags += Decimal(r['bags'])
        totals[r['currency']] += Decimal(r['amount'])
    summary = [{**g, 'daily': {d: str(n) for d, n in g['daily'].items()}, **{f: str(g[f]) for f in ['bags', 'kg', 'amount']}} for _, g in sorted(groups.items())]
    return {'month': month, 'version': entry.version if entry else 0, 'kgPerBag': str(KG_PER_BAG),
            'days': [f'{month}-{d:02}' for d in range(1, calendar.monthrange(first.year, first.month)[1]+1)],
            'entries': rows, 'summary': summary, 'bags': str(bags), 'kg': str(bags * KG_PER_BAG),
            'totals': {c: str(v) for c, v in sorted(totals.items())}}


def tables(data):
    summary = [['Recycled materials — ' + data['month']], ['Material', 'Currency', 'Price/kg', *data['days'], 'Total bags', 'Total kg', 'Total amount']]
    summary += [[r['material'], r['currency'], Decimal(r['pricePerKg']), *[Decimal(r['daily'].get(d, '0')) for d in data['days']], Decimal(r['bags']), Decimal(r['kg']), Decimal(r['amount'])] for r in data['summary']]
    detail = [['Recycled material entries — ' + data['month']], ['Date', 'Material', 'Bags', 'kg/bag', 'Total kg', 'Price/kg', 'Currency', 'Amount', 'Status', 'Notes', 'Correction / void reason', 'Record ID']]
    detail += [[r['date'], r['material'], Decimal(r['bags']), KG_PER_BAG, Decimal(r['kg']), Decimal(r['pricePerKg']), r['currency'], Decimal(r['amount']), r['status'], r['notes'], r['correctionReason'], r['id']] for r in data['entries']]
    totals = [['Monthly totals — ' + data['month']], ['Total bags', 'Total kg'], [Decimal(data['bags']), Decimal(data['kg'])], ['Currency', 'Total amount']]
    totals += [[c, Decimal(v)] for c, v in data['totals'].items()]
    return [('Daily Bags', summary), ('Bag Entries', detail), ('Monthly Totals', totals)]


class RecycledMaterialReport(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .shared_entries import permitted
        if not permitted(request.user, READERS):
            raise PermissionDenied('Your account cannot view recycled materials.')
        data = report(request.query_params.get('month', ''))
        export = request.query_params.get('export')
        if not export:
            return Response(data)
        if export != 'xlsx':
            raise serializers.ValidationError('Export must be xlsx.')
        response = HttpResponse(xlsx_bytes(tables(data)), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="recycled-materials-{data["month"]}.xlsx"'
        return response
