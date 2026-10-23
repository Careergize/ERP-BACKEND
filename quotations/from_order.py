"""Create the editable quotation together with its originating workflow order."""
from copy import deepcopy
from uuid import uuid4
from rest_framework import serializers
from .models import QuotationDraft
from .drafts import DraftContentSerializer
from .views import normalized_document


COMPANY = {
    'tradingName': 'NAVAPACK', 'legalName': 'Nova Recycling Industries Ltd',
    'logo': '/assets/Nava-logo.png',
    'address': 'Plot 22, Ring Road\nNalukolongo Industrial Area\nKampala',
    'telephone': '0753349228', 'email': 'novarecyclingindltd@gmail.com',
    'tin': '1017362107', 'signatory': '',
    'banks': [
        {'bank': 'DFCU Bank', 'branch': 'Ndeeba Branch', 'accountName': 'Nova Recycling Industries Ltd.', 'accountNumber': '01450016576735', 'currency': 'UGX'},
        {'bank': 'Diamond Trust Bank', 'branch': 'Ndeeba Branch', 'accountName': 'Nova Recycling Industries Ltd.', 'accountNumber': '0272021001', 'currency': 'UGX'},
    ],
}
TERMS = {
    'priceValidity': '14 days from the date of quotation, subject to change based on prevailing exchange rate & material prices.',
    'deliveryPeriod': '14 Days', 'paymentTerms': '30 days credit', 'creditDays': 30,
    'revisionClause': '', 'additionalTerms': '', 'remarks': '', 'quantityTolerance': 10,
}


def create_order_quotation(order, user, template=None):
    data = order.data
    # Quotation-to-order conversion already has its original commercial document.
    if data.get('quotationId'):
        return
    from erp.models import Customer
    customer = None
    customer_key = str(data.get('customerId', '')).removeprefix('db:')
    if customer_key.isdigit():
        customer = Customer.objects.filter(pk=customer_key).first()
    contact = data.get('customerContact') or {}
    template = template or {}
    if not isinstance(template, dict):
        raise serializers.ValidationError('Quotation template must contain company and validity settings.')
    validity_days = serializers.IntegerField(min_value=0, max_value=3650).run_validation(template.get('validityDays', 14))
    key = str(uuid4())
    payload = {
        'id': key, 'rootId': key, 'number': f"QT-{data['orderNumber']}", 'revision': 1,
        'documentType': 'Sales Quotation', 'date': data['date'],
        'validityDays': validity_days, 'deliveryDays': 14,
        'currency': data['currency'], 'taxRate': data['taxRate'],
        'taxMode': (data.get('commercialPricing') or {}).get('taxMode', 'exclusive'),
        'discount': (data.get('commercialPricing') or {}).get('discount', 0),
        'company': deepcopy(template.get('company', COMPANY)),
        'terms': {**TERMS, 'priceValidity': f'{validity_days} days from the date of quotation, subject to change based on prevailing exchange rate & material prices.', **(data.get('commercialTerms') or {})},
        'salesRepresentative': data['marketingPersonName'], 'salesOrderId': data['id'],
        'customer': {
            **({'customerId': data['customerId']} if data.get('customerId') else {}), 'name': data['customerName'],
            'customerType': data['customerType'],
            **{key: contact.get(key, getattr(customer, field, '') if customer else '') for key, field in (
                ('billingAddress', 'address'), ('deliveryAddress', 'delivery_address'),
                ('contactPerson', 'contact_person'), ('telephone', 'phone'), ('email', 'email'), ('tin', 'tin'))},
        },
        'lines': [{
            'id': str(uuid4()), 'type': 'custom' if item['fulfillmentSource'] == 'manufacturing' else 'stock',
            **{target: item[source] for target, source in (('productId', 'itemId'), ('modelId', 'modelId'), ('modelRevision', 'modelRevision'), ('led', 'led'), ('ledSnapshot', 'ledSnapshot')) if item.get(source) is not None},
            'description': item['itemName'], 'group': '', 'specifications': item.get('specifications', ''),
            'quantity': item['quantity'], 'unit': item['unit'], 'unitPrice': item['unitPrice'],
        } for item in data['items']],
    }
    serializer = DraftContentSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    document = normalized_document(serializer.validated_data)
    QuotationDraft.objects.create(id=key, created_by=user, editor_data=document)
    order.data = {**data, 'generatedQuotationId': key, 'generatedQuotationNumber': payload['number']}
    order.save(update_fields=['data', 'updated_at'])
