"""A new Sales Order must identify and retain a saved quotation revision."""
from decimal import Decimal, InvalidOperation
from uuid import UUID
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from quotations.models import QuotationDraft


def quotation_source(data, user):
    try:
        source_id = UUID(str(data.get('quotationRevisionId', '')))
    except (ValueError, TypeError):
        raise serializers.ValidationError('Create and save a quotation before creating its Sales Order.')
    quote = QuotationDraft.objects.select_for_update().filter(pk=source_id).first()
    if not quote:
        raise serializers.ValidationError('The source quotation revision was not found. Save the quotation first.')
    if quote.created_by_id != user.pk and user.effective_role != 'admin':
        raise PermissionDenied('Select a quotation you are authorized to convert.')
    document = quote.editor_data
    if document.get('salesOrderId'):
        raise serializers.ValidationError('This document already belongs to a Sales Order; it cannot create another.')
    if str(data.get('quotationId')) != str(document['rootId']) or data.get('quotationRevision') != document['revision']:
        raise serializers.ValidationError('Use the saved quotation root and exact revision.')
    if QuotationDraft.objects.filter(created_by=quote.created_by, editor_data__rootId=document['rootId'], editor_data__revision__gt=document['revision']).exists():
        raise serializers.ValidationError('Use the latest saved quotation revision.')
    lines = {str(line['id']): line for line in document['lines']}
    items = data.get('items', [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items) or len(items) != len(lines) or {str(item.get('quotationLineId')) for item in items} != set(lines):
        raise serializers.ValidationError('The order must contain each saved quotation line exactly once.')
    try:
        for item in items:
            line = lines[str(item['quotationLineId'])]
            if any(Decimal(str(item.get(key))) != Decimal(str(line[key])) for key in ('quantity', 'unitPrice')) or item.get('unit') != line['unit'] or item.get('itemName') != line['description']:
                raise ValueError()
            if item.get('fulfillmentSource') != ('manufacturing' if line['type'] == 'custom' else 'stock'):
                raise ValueError()
            for order_key, quote_key in (('itemId', 'productId'), ('modelId', 'modelId'), ('modelRevision', 'modelRevision')):
                if item.get(order_key) != line.get(quote_key):
                    raise ValueError()
            if item.get('specifications', '') != line.get('specifications', ''):
                raise ValueError()
            if item.get('ledSnapshot') != line.get('ledSnapshot'):
                raise ValueError()
        pricing = data.get('commercialPricing') or {}
        if data.get('currency') != document['currency'] or Decimal(str(data.get('taxRate'))) != Decimal(str(document['taxRate'])) or pricing.get('taxMode', 'exclusive') != document['taxMode'] or Decimal(str(pricing.get('discount', 0))) != Decimal(str(document['discount'])):
            raise ValueError()
        if data.get('customerName') != document['customer']['name'] or data.get('customerType') != document['customer']['customerType'] or data.get('marketingPersonName') != document.get('salesRepresentative', ''):
            raise ValueError()
        if document['customer'].get('customerId') and data.get('customerId') != document['customer']['customerId']:
            raise ValueError()
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise serializers.ValidationError('The order differs from its saved quotation. Revise the quotation before changing commercial details.')
    data.update(quotationNumber=document['number'], commercialTerms=document['terms'], customerContact=document['customer'])
