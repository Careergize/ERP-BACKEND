from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q
from rest_framework import serializers
from .catalog_models import SpareItem, SpareStockMovement


def balance(item):
    totals = item.movements.aggregate(
        opening=Sum('quantity', filter=Q(is_opening=True)),
        incoming=Sum('quantity', filter=Q(is_opening=False, movement_type='IN')),
        outgoing=Sum('quantity', filter=Q(movement_type='OUT')))
    opening = totals['opening']
    incoming = totals['incoming'] or Decimal('0')
    outgoing = totals['outgoing'] or Decimal('0')
    current = None if opening is None else opening + incoming - outgoing
    return {'opening_stock': opening, 'stock_in': incoming, 'stock_out': outgoing,
            'current_stock': current}

class MovementInput(serializers.Serializer):
    request_id = serializers.UUIDField()
    movement_type = serializers.ChoiceField(choices=['IN', 'OUT'])
    is_opening = serializers.BooleanField(default=False)
    quantity = serializers.DecimalField(max_digits=20, decimal_places=6, min_value=Decimal('0'))
    unit = serializers.ChoiceField(choices=['Each', 'Both', 'Litres', 'Kilos'])
    reference = serializers.CharField(max_length=250)

@transaction.atomic
def post_movement(item_id, data, actor):
    s = MovementInput(data=data)
    s.is_valid(raise_exception=True)
    v = s.validated_data
    item = SpareItem.objects.select_for_update().get(pk=item_id)
    previous = SpareStockMovement.objects.filter(request_id=v['request_id']).first()
    if previous:
        if previous.recorded_by_id != actor.pk or previous.item_id != item.pk or any(getattr(previous, key) != value for key, value in v.items()):
            raise serializers.ValidationError('Request ID already used with different details.')
        return previous, False
    if not item.is_active or v['unit'] != item.unit:
        raise serializers.ValidationError('Use an active spare and its exact unit.')
    totals = balance(item)
    if v['is_opening']:
        if v['movement_type'] != 'IN' or totals['opening_stock'] is not None or item.movements.exists():
            raise serializers.ValidationError('Opening must be the first and only opening entry.')
    elif v['quantity'] <= 0 or totals['opening_stock'] is None:
        raise serializers.ValidationError('A positive quantity and explicit opening balance are required.')
    if v['movement_type'] == 'OUT' and v['quantity'] > totals['current_stock']:
        raise serializers.ValidationError('Insufficient stock.')
    return SpareStockMovement.objects.create(item=item, recorded_by=actor, **v), True
