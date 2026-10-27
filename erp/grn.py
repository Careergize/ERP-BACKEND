from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import APIException
from .models import GoodsReceivedNote, GoodsReceivedLine, PurchaseOrder, PurchaseOrderLine
from .views import DraftRecordViewSet
from .finished_stock import item_label


class GRNLineSerializer(serializers.ModelSerializer):
    stock_label = serializers.SerializerMethodField()
    description = serializers.CharField(source='purchase_line.description', read_only=True)
    unit = serializers.CharField(source='purchase_line.unit', read_only=True)
    quantity_ordered = serializers.DecimalField(source='purchase_line.quantity', max_digits=14, decimal_places=3, read_only=True)
    quantity_accepted = serializers.SerializerMethodField()

    class Meta:
        model = GoodsReceivedLine
        fields = ('id', 'purchase_line', 'description', 'unit', 'quantity_ordered', 'previously_received',
                  'quantity_received', 'damaged_quantity', 'missing_quantity', 'quantity_accepted', 'remarks', 'stock_label')
        read_only_fields = ('id', 'previously_received')

    def get_quantity_accepted(self, line):
        return str(line.quantity_received - line.damaged_quantity)

    def get_stock_label(self, line):
        return item_label(line.purchase_line.stock_item)

    def validate(self, attrs):
        if attrs.get('damaged_quantity', 0) > attrs['quantity_received']:
            raise serializers.ValidationError('Damaged quantity cannot exceed received quantity.')
        if attrs['quantity_received'] + attrs.get('missing_quantity', 0) <= 0:
            raise serializers.ValidationError('Enter received goods or a missing quantity for this item.')
        attrs.setdefault('damaged_quantity', Decimal(0))
        attrs.setdefault('missing_quantity', Decimal(0))
        attrs.setdefault('remarks', '')
        return attrs


class GRNSerializer(serializers.ModelSerializer):
    request_id = serializers.UUIDField()
    lines = GRNLineSerializer(many=True, allow_empty=False, max_length=100)
    po_number = serializers.CharField(source='purchase_order.number', read_only=True)

    class Meta:
        model = GoodsReceivedNote
        fields = ('id', 'number', 'request_id', 'purchase_order', 'po_number', 'receipt_date',
                  'supplier_name', 'received_by', 'receiver_name', 'authorized_person', 'notes',
                  'lines', 'created_at', 'updated_at')
        read_only_fields = ('number', 'supplier_name', 'received_by', 'receiver_name', 'created_at', 'updated_at')

    def validate(self, attrs):
        attrs.setdefault('authorized_person', '')
        attrs.setdefault('notes', '')
        if attrs['receipt_date'] > timezone.localdate():
            raise serializers.ValidationError({'receipt_date': 'Receipt date cannot be in the future.'})
        if attrs['receipt_date'] < attrs['purchase_order'].order_date:
            raise serializers.ValidationError({'receipt_date': 'Receipt date cannot precede the PO date.'})
        ids = [row['purchase_line'].pk for row in attrs['lines']]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({'lines': 'Enter each PO line only once per GRN.'})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        lines = sorted(validated_data.pop('lines'), key=lambda row: row['purchase_line'].pk)
        order = PurchaseOrder.objects.select_for_update().get(pk=validated_data['purchase_order'].pk)
        actor = validated_data['received_by']
        note, created = GoodsReceivedNote.objects.get_or_create(request_id=validated_data['request_id'], defaults={
            **validated_data, 'supplier_name': order.supplier_name,
            'receiver_name': (actor.get_full_name() or actor.username)[:200]})
        if not created:
            previous = [{key: getattr(row, key) for key in ('purchase_line', 'quantity_received', 'damaged_quantity', 'missing_quantity', 'remarks')}
                        for row in note.lines.select_related('purchase_line').order_by('purchase_line_id')]
            if any(getattr(note, key) != value for key, value in validated_data.items()) or previous != lines:
                error = APIException('This GRN request was already saved with different details. Reload before recording another receipt.')
                error.status_code = 409
                raise error
            return note
        for row in lines:
            line = PurchaseOrderLine.objects.select_for_update().select_related('stock_item').get(pk=row['purchase_line'].pk)
            if line.order_id != order.pk:
                raise serializers.ValidationError({'lines': 'Every item must belong to the selected PO.'})
            if line.stock_posted:
                raise serializers.ValidationError({'lines': 'This PO item was already added to Stock by the purchase. A GRN cannot add it again.'})
            if not line.stock_item_id:
                raise serializers.ValidationError({'lines': 'This earlier PO item has no finished-stock link. Use a PO with linked stock items.'})
            if note.receipt_date < line.stock_item.opening_date:
                raise serializers.ValidationError({'receipt_date': 'Receipt precedes the stock item opening date.'})
            previous = sum((entry.quantity_received for entry in line.goods_received_lines.all()), Decimal(0))
            remaining = line.quantity - previous
            if row['quantity_received'] + row['missing_quantity'] > remaining:
                raise serializers.ValidationError({'lines': f'{line.description}: received plus missing quantity exceeds the outstanding {remaining} {line.unit}. Reload the PO.'})
            GoodsReceivedLine.objects.create(note=note, previously_received=previous, **row)
        order_lines = list(order.lines.prefetch_related('goods_received_lines'))
        received = [line.quantity if line.stock_posted else sum((entry.quantity_received for entry in line.goods_received_lines.all()), Decimal(0)) for line in order_lines]
        order.status = 'received' if all(quantity >= line.quantity for line, quantity in zip(order_lines, received)) else 'partially_received' if any(received) else 'awaiting_receipt'
        order.save(update_fields=['status', 'updated_at'])
        from accounting.integration import purchase_received
        purchase_received(order, actor, note)
        return note


class GoodsReceivedNoteViewSet(DraftRecordViewSet):
    queryset = GoodsReceivedNote.objects.select_related('purchase_order', 'received_by').prefetch_related('lines__purchase_line__stock_item').all()
    serializer_class = GRNSerializer

    def perform_create(self, serializer):
        serializer.save(received_by=self.request.user)

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        response.data['can_create'] = request.user.has_perm('erp.add_goodsreceivednote')
        return response
