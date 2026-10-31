"""Explicit stable-ID links between accounting parties and ERP customers."""
from rest_framework.exceptions import PermissionDenied
from rest_framework import serializers
from erp.models import Customer
from erp.serializers import CustomerSerializer
from .models import Party
from .services import require


def link_customer(party, payload, user):
    require(party.kind == 'customer', 'Only customer ledgers can link to ERP customers.')
    customer_id = payload.get('erpCustomer')
    if customer_id is not None:
        if not user.has_perm('erp.view_customer'): raise PermissionDenied('Customer view permission is required.')
        require(type(customer_id) is int, 'Select a saved ERP customer.')
        customer = Customer.objects.filter(pk=customer_id,is_active=True).first()
        require(customer is not None, 'Select an active ERP customer.')
    else:
        if not user.has_perm('erp.add_customer'): raise PermissionDenied('Customer creation permission is required to make this ledger available to Sales Orders.')
        key = serializers.UUIDField().run_validation(payload.get('creationKey'))
        serializer = CustomerSerializer(data={'name':party.name,'customer_type':payload.get('customerType','B2B'),'creation_key':str(key)})
        serializer.is_valid(raise_exception=True)
        customer = serializer.save()
    source = f'customer:{customer.pk}'
    require(not party.source or party.source==source, 'This ledger already links to another customer. Its identity cannot be reassigned.')
    require(not Party.objects.filter(source=source).exclude(pk=party.pk).exists(), 'This ERP customer already has an accounting ledger. Use that ledger instead of merging balances.')
    party.source = source
    party.save(update_fields=['source'])
    return customer
