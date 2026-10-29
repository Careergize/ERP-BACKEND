"""Customer-owned labour-job material receipts and returns, in kg."""
from datetime import date
from decimal import Decimal
import re
from django.http import HttpResponse
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import SharedEntry
from .recycling_purchases import READERS, number, xlsx_bytes

KEY = 'labour-job-entries'

def validate_options(content, previous):
    ids, names = set(), set()
    for row in content:
        if set(row) != {'id', 'kind', 'name'} or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Option requires ID, kind and name.')
        if row['kind'] not in {'material', 'color'}:
            raise serializers.ValidationError('Select material or colour.')
        name = row['name']
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids or not name.strip() or name != name.strip() or len(name) > 200 or re.search(r'[\x00-\x1f\ud800-\udfff\ufffe\uffff]', name):
            raise serializers.ValidationError('Enter a unique ID and a name of 1–200 characters.')
        identity = (row['kind'], name.casefold())
        if identity in names:
            raise serializers.ValidationError('This option already exists.')
        ids.add(row['id'])
        names.add(identity)
    current = {row['id']: row for row in content}
    if any(current.get(row['id']) != row for row in previous or []):
        raise serializers.ValidationError('Saved options cannot be removed or renamed.')


def validate_entries(content, previous):
    required = {'id', 'date', 'direction', 'customer', 'material', 'color', 'quantity', 'notes', 'status', 'correctionReason'}
    ids = set()
    for row in content:
        if set(row) != required or not all(isinstance(v, str) for v in row.values()):
            raise serializers.ValidationError('Complete all labour job fields using text and decimal values.')
        if any(re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]', v) for v in row.values()):
            raise serializers.ValidationError('Remove unsupported control characters.')
        if not row['id'] or len(row['id']) > 80 or row['id'] in ids:
            raise serializers.ValidationError('Entry IDs must be unique (maximum 80 characters).')
        ids.add(row['id'])
        try:
            if date.fromisoformat(row['date']).isoformat() != row['date']:
                raise ValueError()
        except ValueError:
            raise serializers.ValidationError('Enter a valid date (YYYY-MM-DD).')
        if row['direction'] not in {'IN', 'OUT'} or row['status'] not in {'Active', 'Voided'}:
            raise serializers.ValidationError('Select a supported direction and status.')
        for field in ['customer', 'material', 'color']:
            if not row[field].strip() or row[field] != row[field].strip() or len(row[field]) > 200:
                raise serializers.ValidationError('Customer, material and colour are required (maximum 200 characters).')
        number(row['quantity'], 3, positive=True)
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


def report(month):
    try:
        if not re.fullmatch(r'\d{4}-\d{2}', month):
            raise ValueError()
        date.fromisoformat(month + '-01')
    except ValueError:
        raise serializers.ValidationError('Select a valid reporting month (YYYY-MM).')
    entry = SharedEntry.objects.filter(pk=KEY).first()
    records = entry.content if entry else []
    rows = sorted([r for r in records if r['date'].startswith(month)], key=lambda r: (r['date'], r['id']))
    groups = {}
    for r in records:
        if r['status'] != 'Active' or r['date'][:7] > month:
            continue
        key = (r['customer'], r['material'], r['color'])
        g = groups.setdefault(key, dict(customer=key[0], material=key[1], color=key[2], opening=Decimal(0), quantityIn=Decimal(0), quantityOut=Decimal(0)))
        q = Decimal(r['quantity'])
        if r['date'][:7] < month:
            g['opening'] += q if r['direction'] == 'IN' else -q
        else:
            g['quantityIn' if r['direction'] == 'IN' else 'quantityOut'] += q
    summary = []
    for _, g in sorted(groups.items()):
        g['closing'] = g['opening'] + g['quantityIn'] - g['quantityOut']
        summary.append({k: str(v) if isinstance(v, Decimal) else v for k, v in g.items()})
    return dict(month=month, version=entry.version if entry else 0, entries=rows, summary=summary,
                quantityIn=str(sum((Decimal(g['quantityIn']) for g in summary), Decimal(0))),
                quantityOut=str(sum((Decimal(g['quantityOut']) for g in summary), Decimal(0))))


def tables(data):
    summary = [['Labour Job â€” ' + data['month']], ['Customer', 'Material Type', 'Color', 'Recorded opening kg', 'Qty In kg', 'Qty Out kg', 'Recorded closing kg']]
    summary += [[r['customer'], r['material'], r['color'], *[Decimal(r[f]) for f in ['opening', 'quantityIn', 'quantityOut', 'closing']]] for r in data['summary']]
    sheets = [('Summary', summary)]
    for direction in ['IN', 'OUT']:
        rows = [['Material ' + direction + ' â€” ' + data['month']], ['Date', 'Customers', 'Material Type', 'Color', 'Qty ' + direction + ' kg', 'Status', 'Notes', 'Correction / void reason', 'Record ID']]
        rows += [[r['date'], r['customer'], r['material'], r['color'], Decimal(r['quantity']), r['status'], r['notes'], r['correctionReason'], r['id']] for r in data['entries'] if r['direction'] == direction]
        sheets.append(('Material ' + direction, rows))
    return sheets


class LabourJobReport(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from .shared_entries import permitted
        if not permitted(request.user, READERS):
            raise PermissionDenied('Your account cannot view labour jobs.')
        data = report(request.query_params.get('month', ''))
        export = request.query_params.get('export')
        if not export:
            return Response(data)
        if export != 'xlsx':
            raise serializers.ValidationError('Export must be xlsx.')
        response = HttpResponse(xlsx_bytes(tables(data)), content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename="labour-job-{data["month"]}.xlsx"'
        return response
