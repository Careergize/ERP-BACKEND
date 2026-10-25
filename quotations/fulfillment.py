"""Create order demand and manufacturing preparation on quotation approval."""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, NAMESPACE_URL, uuid5

from django.utils import timezone
from rest_framework import serializers

from erp.models import Customer, WorkflowOrder
from erp.production_workflow import allocate, dated, validated_order_total
from .models import QuotationDraft
from .serializers import DocumentSerializer


def source_draft(quote, lock=False):
    try:
        key = UUID(quote.source_id)
    except (ValueError, TypeError):
        return None  # Legacy standalone PDF snapshots have no manufacturing source.
    rows = QuotationDraft.objects.select_for_update() if lock else QuotationDraft.objects
    return rows.filter(pk=key, created_by=quote.created_by).first()


def linked_order(quote):
    return WorkflowOrder.objects.filter(data__quotationRevisionId=quote.source_id).first()


def create_fulfillment(quote, manager, required_date=None):
    from .views import normalized_document

    draft = source_draft(quote, lock=True)
    if not draft:
        return None
    doc = draft.editor_data
    if doc.get('salesOrderId'):
        return None  # An order-origin quotation must not create another order.
    key = uuid5(NAMESPACE_URL, 'navapack-sales-quotation:' + str(doc['rootId']))
    existing = WorkflowOrder.objects.select_for_update().filter(request_id=key).first()
    if existing:
        if existing.data.get('quotationRevisionId') != str(draft.pk):
            raise serializers.ValidationError('This quotation already has an order from another revision.')
        return existing
    if QuotationDraft.objects.filter(created_by=draft.created_by, editor_data__rootId=doc['rootId'], editor_data__revision__gt=doc['revision']).exists():
        raise serializers.ValidationError('Approve the latest quotation revision.')

    # Bind hidden product/model identities to the exact commercial snapshot reviewed.
    public = deepcopy(doc)
    public['source_id'] = quote.source_id
    if quote.customer_master_id:
        public['customer_id'] = quote.customer_master_id
    for line in public['lines']:
        line.pop('led', None)
        if line.get('ledSnapshot'):
            line['led'] = {k: line['ledSnapshot'][k] for k in ('method', 'selectedAmount', 'vatOnLED') if k in line['ledSnapshot']}
        if line.get('unitId'):
            line['unit_id'] = line['unitId']
    serializer = DocumentSerializer(data=public)
    serializer.is_valid(raise_exception=True)
    if normalized_document(serializer.validated_data) != quote.document:
        raise serializers.ValidationError('The saved draft differs from the submitted quotation. Submit its current revision before approval.')
    customer_key = str(doc['customer'].get('customerId', '')).removeprefix('db:')
    if not customer_key.isdigit() or not Customer.objects.filter(pk=customer_key, is_active=True).exists():
        raise serializers.ValidationError('Link an active customer master on the quotation before approval can create its Sales Order.')
    today = timezone.localdate()
    manufacturing = any(line['type'] == 'custom' for line in doc['lines'])
    if manufacturing and (not required_date or dated(required_date) < today):
        raise serializers.ValidationError('Confirm a manufacturing delivery date on or after today.')
    data = {
        'quotationId': str(doc['rootId']), 'quotationRevisionId': str(draft.pk),
        'quotationNumber': doc['number'], 'quotationRevision': doc['revision'],
        'customerName': doc['customer']['name'], 'customerId': doc['customer']['customerId'],
        'customerType': doc['customer'].get('customerType', 'B2B'), 'customerContact': deepcopy(doc['customer']),
        'date': today.isoformat(), 'marketingPersonName': doc.get('salesRepresentative', ''),
        'currency': doc['currency'], 'taxRate': float(doc['taxRate']), 'requisitionOrder': '',
        'commercialTerms': deepcopy(doc['terms']),
        'commercialPricing': {'taxMode': doc['taxMode'], 'discount': doc['discount'], 'roundingPolicy': 'Approved quotation snapshot'},
        'items': [],
    }
    for line in doc['lines']:
        item = {'id': str(line['id']), 'quotationLineId': str(line['id']),
                'itemName': line['description'], 'specifications': line.get('specifications', ''),
                'fulfillmentSource': 'manufacturing' if line['type'] == 'custom' else 'stock',
                'quantity': float(line['quantity']), 'unit': line['unit'], 'unitPrice': float(line['unitPrice'])}
        item['totalPrice'] = float((Decimal(line['quantity']) * Decimal(line['unitPrice'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))
        if line.get('estimateId'):
            item.update(quotationCostEstimateId=line['estimateId'], quotationCostEstimateRevision=line.get('estimateRevision'))
        for target, source in (('itemId', 'productId'), ('modelId', 'modelId'), ('modelRevision', 'modelRevision'), ('ledSnapshot', 'ledSnapshot'), ('estimateId', 'estimateId')):
            if source in line:
                item[target] = deepcopy(line[source])
        if line['type'] == 'custom':
            item['requiredDate'] = required_date
        data['items'].append(item)
    total = validated_order_total(data)
    order = WorkflowOrder.objects.create(request_id=key, data={}, created_by=quote.created_by)
    data.update(id=f'wf-order-{order.pk}', orderNumber=f'SO-W{order.pk:05d}',
                status='Approved', jobCardIds=[], orderTotal=total, persistedWorkflow=True,
                createdBy=quote.created_by_id,
                salesOrderApproval={'approvedBy': manager.get_full_name() or manager.username,
                                    'approvedById': manager.pk, 'approvedAt': timezone.now().isoformat(),
                                    'sourceQuotationId': str(quote.pk)})
    order.data = data
    order.save(update_fields=['data'])
    if manufacturing:
        allocate(order, manager)
    return order
