"""Unbilled operational commitments: quantities never become GL postings here."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from erp.models import WorkflowOrder, PurchaseOrder, SharedEntry
from .models import Document, DocumentLine
from .services import ZERO, money


def order_commitments(end):
    invoices = SharedEntry.objects.filter(pk='invoices').first()
    invoiced = defaultdict(lambda: ZERO)
    issues = []
    for row in invoices.content if invoices else []:
        if row.get('status') in {'Draft', 'Cancelled'} or row.get('date', '9999') > str(end):
            continue
        try:
            key = str(row['orderId']).removeprefix('wf-order-')
            for line in row['items']:
                invoiced[(key, str(line['itemId']), line['unit'])] += Decimal(str(line['quantity']))
        except (KeyError, TypeError, InvalidOperation):
            issues.append(f"Invoice {row.get('number', row.get('id', '?'))} has incomplete order references.")
    sales = []
    for order in WorkflowOrder.objects.order_by('pk'):
        data = order.data
        if data.get('date', '9999') > str(end) or data.get('status') == 'Cancelled' or order.commercial_status == 'Cancelled':
            continue
        for line in data.get('items', []):
            try:
                ordered = Decimal(str(line['quantity']))
                billed = invoiced[(str(order.pk), str(line['id']), line['unit'])]
                remaining = max(ordered-billed, ZERO)
                if billed > ordered:
                    issues.append(f"{data.get('orderNumber', order.pk)} has invoiced quantity above its current scope.")
                if not remaining: continue
                price = Decimal(str(line['unitPrice']))
                sales.append({'orderId':order.pk,'number':data.get('orderNumber', str(order.pk)), 'date':data['date'],
                    'party':data.get('customerName',''), 'customerSource':f"customer:{data['customerId']}" if data.get('customerId') else '',
                    'status':data.get('status',''), 'currency':data['currency'], 'item':line.get('itemName',''),
                    'line':str(line['id']), 'unit':line['unit'], 'ordered':str(ordered), 'invoiced':str(billed),
                    'remaining':str(remaining), 'unitPrice':str(price), 'remainingValue':str(money(remaining*price))})
            except (KeyError, TypeError, InvalidOperation):
                issues.append(f"Sales order {data.get('orderNumber', order.pk)} has an incomplete line.")
    billed = defaultdict(lambda: ZERO)
    for line in DocumentLine.objects.filter(document__kind='bill', document__date__lte=end, item__startswith='purchase-line:').select_related('document'):
        if hasattr(line.document, 'reversal') and line.document.reversal.date <= end: continue
        billed[line.item] += line.quantity
    purchases = []
    for order in PurchaseOrder.objects.filter(order_date__lte=end).exclude(status__iexact='cancelled').prefetch_related('lines').order_by('order_date','pk'):
        for line in order.lines.all():
            used = billed[f'purchase-line:{line.pk}']
            remaining = max(line.quantity-used, ZERO)
            if not remaining: continue
            purchases.append({'orderId':order.pk, 'number':order.number, 'date':str(order.order_date), 'party':order.supplier_name,
                'status':order.status, 'currency':order.currency, 'line':line.pk, 'item':line.description, 'unit':line.unit,
                'ordered':str(line.quantity), 'invoiced':str(used), 'remaining':str(remaining),
                'unitPrice':str(line.unit_price), 'remainingValue':str(money(remaining*line.unit_price))})
    totals = []
    for kind, rows in [('sales',sales),('purchase',purchases)]:
        grouped = defaultdict(lambda: ZERO)
        for row in rows: grouped[row['currency']] += Decimal(row['remainingValue'])
        totals.extend({'kind':kind, 'currency':currency, 'remainingValue':str(value)} for currency,value in sorted(grouped.items()))
    return {'asOf':str(end), 'sales':sales, 'purchases':purchases, 'totals':totals, 'issues':issues,
            'basis':'Current saved order scope, less invoices/bills dated through the end date. Line values before discounts, tax and levies. Sales use recorded operational invoices, including those awaiting GL posting. Quantities retain exact units. No historical order revision reconstruction.'}
