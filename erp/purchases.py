from decimal import Decimal, localcontext
import hashlib
import json
import uuid
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import FinishedStockItem, PurchaseOrder, PurchaseOrderLine
from .views import DraftRecordViewSet
from .finished_stock import item_label


class NewStockSerializer(serializers.Serializer):
    category = serializers.ChoiceField(choices=['carrier-bags', 'flat-bags', 'packing'])
    subgroup = serializers.CharField(max_length=200, allow_blank=True, default='')
    customer = serializers.CharField(max_length=200, allow_blank=True, default='')
    size = serializers.CharField(max_length=100, allow_blank=True, default='')
    brand = serializers.CharField(max_length=100, allow_blank=True, default='')
    packing_size = serializers.CharField(max_length=100, allow_blank=True, default='')


class PurchaseLineSerializer(serializers.ModelSerializer):
    stock_label = serializers.SerializerMethodField()
    quantity_received = serializers.SerializerMethodField()
    quantity_remaining = serializers.SerializerMethodField()
    stock_mode = serializers.ChoiceField(choices=['existing', 'manual'])
    stock_item = serializers.PrimaryKeyRelatedField(queryset=FinishedStockItem.objects.all(), required=False, allow_null=True, default=None)
    new_item = NewStockSerializer(source='stock_details', required=False, write_only=True)

    class Meta:
        model = PurchaseOrderLine
        fields = ('id', 'description', 'unit', 'quantity', 'unit_price', 'stock_mode', 'stock_item', 'new_item', 'stock_posted', 'quantity_received', 'quantity_remaining', 'stock_label')
        read_only_fields = ('id', 'stock_posted')

    def get_quantity_received(self, line):
        return str(line.quantity if line.stock_posted else sum((row.quantity_received for row in line.goods_received_lines.all()), Decimal(0)))

    def get_stock_label(self, line):
        return item_label(line.stock_item)

    def get_quantity_remaining(self, line):
        return str(max(Decimal(0), line.quantity - Decimal(self.get_quantity_received(line))))

    def validate(self, attrs):
        item = attrs.get('stock_item')
        if attrs['stock_mode'] == 'existing':
            if item is None:
                raise serializers.ValidationError({'stock_item': 'Choose a finished-goods stock item.'})
            if attrs['unit'] != item.unit:
                raise serializers.ValidationError({'unit': 'Use the exact unit of the selected stock item.'})
            if attrs.get('stock_details'):
                raise serializers.ValidationError('Use manual details only for a new stock item.')
            attrs['description'] = item.name
            attrs['stock_details'] = {}
        else:
            if item is not None or 'stock_details' not in attrs:
                raise serializers.ValidationError('Manual entry requires new item details and no existing stock item.')
            if len(attrs['description']) > 250:
                raise serializers.ValidationError({'description': 'Stock item names cannot exceed 250 characters.'})
        return attrs


class PurchaseSerializer(serializers.ModelSerializer):
    receipt_method = serializers.ChoiceField(choices=['immediate', 'grn'], default='grn')
    can_receive = serializers.SerializerMethodField()
    request_id = serializers.UUIDField()
    lines = PurchaseLineSerializer(many=True, allow_empty=False, max_length=100)
    total = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = ('id', 'number', 'request_id', 'supplier_name', 'supplier_reference',
                  'supplier_contact', 'supplier_address', 'order_date', 'delivery_date',
                  'delivery_address', 'currency', 'payment_terms', 'notes', 'status',
                  'lines', 'total', 'created_by', 'created_at', 'updated_at', 'receipt_method', 'can_receive')
        read_only_fields = ('number', 'status', 'created_by', 'created_at', 'updated_at')

    def validate_currency(self, value):
        value = value.upper()
        if len(value) != 3 or not value.isascii() or not value.isalpha():
            raise serializers.ValidationError('Enter a three-letter currency code, such as INR or USD.')
        return value

    def validate(self, attrs):
        if attrs.get('delivery_date') and attrs['delivery_date'] < attrs['order_date']:
            raise serializers.ValidationError({'delivery_date': 'Must be on or after order date.'})
        if attrs['receipt_method'] == 'immediate' and attrs['order_date'] > timezone.localdate():
            raise serializers.ValidationError({'order_date': 'Purchase date cannot be in the future because saving adds stock immediately.'})
        for line in attrs['lines']:
            item = line.get('stock_item')
            if attrs['receipt_method'] == 'immediate' and item and attrs['order_date'] < item.opening_date:
                raise serializers.ValidationError({'order_date': 'Purchase date precedes the selected stock item opening date.'})
        return attrs

    def get_total(self, order):
        with localcontext() as context:
            context.prec = 50
            return str(sum((line.quantity * line.unit_price for line in order.lines.all()), Decimal('0')).quantize(Decimal('0.01')))

    def get_can_receive(self, order):
        return any(line.stock_item_id and not line.stock_posted and Decimal(PurchaseLineSerializer().get_quantity_remaining(line)) > 0 for line in order.lines.all())

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop('lines')
        order, created = PurchaseOrder.objects.get_or_create(
            request_id=validated_data['request_id'], defaults=validated_data)
        if not created:
            existing = [{**{k: getattr(line, k) for k in ('description', 'unit', 'quantity', 'unit_price', 'stock_mode', 'stock_details')},
                         'stock_item': line.stock_item if line.stock_mode == 'existing' else None}
                        for line in order.lines.select_related('stock_item')]
            if any(getattr(order, k) != v for k, v in validated_data.items()) or existing != lines:
                error = APIException('This save request was already used with different purchase order details. Reload before creating another order.')
                error.status_code = 409
                raise error
            return order
        # Stock entry is part of the same database transaction as the purchase.
        # The immutable purchase line itself is the receipt, so replay cannot post twice.
        ids = sorted({line['stock_item'].pk for line in lines if line['stock_item']})
        list(FinishedStockItem.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
        for line in lines:
            if line['stock_mode'] == 'manual':
                values = {'name': line['description'], 'unit': line['unit'], **line['stock_details']}
                keys = ('name', 'unit', 'category', 'subgroup', 'customer', 'size', 'brand', 'packing_size')
                normalize = lambda value: ' '.join(value.split()).casefold()
                identity = [normalize(values[key]) for key in keys]
                # Retain all source variants. Ambiguous matches require explicit selection.
                if any([normalize(getattr(item, key)) for key in keys] == identity for item in FinishedStockItem.objects.all()):
                    raise serializers.ValidationError({'lines': 'This finished-goods variant already exists. Choose it using From stock.'})
                key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
                item, new = FinishedStockItem.objects.get_or_create(identity_key=key, defaults={
                    'id': 'purchase-' + uuid.uuid4().hex, 'opening_stock': 0,
                    'opening_date': order.order_date, **values})
                if not new:
                    raise serializers.ValidationError({'lines': 'This stock variant was just added by another purchase. Choose it using From stock.'})
                line['stock_item'] = item
            PurchaseOrderLine.objects.create(order=order, stock_posted=order.receipt_method == 'immediate', **line)
        order.status = 'received' if order.receipt_method == 'immediate' else 'awaiting_receipt'
        order.save(update_fields=['status', 'updated_at'])
        if order.receipt_method == 'immediate':
            from accounting.integration import purchase_received
            purchase_received(order, order.created_by)
        return order


class PurchaseOrderViewSet(DraftRecordViewSet):
    queryset = PurchaseOrder.objects.select_related('created_by').prefetch_related('lines__goods_received_lines', 'lines__stock_item').all()
    serializer_class = PurchaseSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['can_create'] = request.user.has_perm('erp.add_purchaseorder')
        return response
