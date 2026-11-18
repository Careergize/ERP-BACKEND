from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import FinishedStockCategory, FinishedStockItem, PurchaseOrderLine, GoodsReceivedLine

BUILTIN_CATEGORIES = {'carrier-bags': 'Carrier Bags', 'flat-bags': 'Flat Bags', 'packing': 'Packing'}


def finished_categories():
    return {**BUILTIN_CATEGORIES, **dict(FinishedStockCategory.objects.order_by('name').values_list('key', 'name'))}


def can_add_category(user):
    return user.is_active and (user.effective_role == 'admin' or user.has_perm('erp.add_finishedstockitem'))


class CategoryInput(serializers.Serializer):
    name = serializers.CharField(max_length=80)


def item_data(item):
    return {**item.reference, 'id': item.pk, 'itemDetails': item.name, 'category': item.category,
            'subgroup': item.subgroup, 'customer': item.customer, 'size': item.size, 'brand': item.brand,
            'packingSize': item.packing_size, 'unit': item.unit,
            'openingStock': None if item.opening_stock is None else float(item.opening_stock),
            'openingDate': item.opening_date.isoformat(),
            **({} if item.reference else {'sourceSheet': 'Purchase entry', 'sourceRow': 0, 'sourceCustomer': '',
               'customerInherited': False, 'sourceUnit': item.unit, 'sourceClosingStock': None, 'dataNotes': []})}


def item_label(item):
    if item is None:
        return ''
    return ' · '.join(value for value in (item.name, item.size, item.brand, item.packing_size, item.customer, item.unit) if value)


class FinishedStockSnapshot(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def delete(self, request):
        if not request.user.is_active or (request.user.effective_role != 'admin' and not request.user.is_superuser):
            raise PermissionDenied('Only admins can delete finished-goods categories.')
        key = request.query_params.get('category')
        if key in BUILTIN_CATEGORIES:
            raise ValidationError('Built-in categories cannot be deleted.')
        category = get_object_or_404(FinishedStockCategory.objects.select_for_update(), pk=key)
        if FinishedStockItem.objects.filter(category=category.pk).exists():
            raise ValidationError('This category is used by finished goods and cannot be deleted.')
        category.delete()
        return Response(status=204)

    def post(self, request):
        if not can_add_category(request.user):
            raise PermissionDenied('Your account cannot add finished-goods categories.')
        data = CategoryInput(data=request.data)
        data.is_valid(raise_exception=True)
        name = ' '.join(data.validated_data['name'].split())
        key = name.lower()
        if len(key) > 80 or key in {'all', *BUILTIN_CATEGORIES, *(label.lower() for label in BUILTIN_CATEGORIES.values())}:
            raise ValidationError({'name': 'Choose a different category name.'})
        category, created = FinishedStockCategory.objects.get_or_create(key=key, defaults={'name': name})
        return Response({'key': category.key, 'name': category.name}, status=201 if created else 200)

    def get(self, request):
        if not (request.user.has_perm('erp.view_finishedstockitem') or request.user.has_perm('erp.view_purchaseorder') or request.user.has_perm('erp.view_goodsreceivednote')):
            raise PermissionDenied('Your account does not have permission to view finished-goods stock.')
        lines = PurchaseOrderLine.objects.filter(stock_posted=True).select_related('order', 'stock_item')
        grn_lines = GoodsReceivedLine.objects.filter(quantity_received__gt=0).select_related('note', 'purchase_line__stock_item')
        grn_receipts = [{'id': f'grn-line-{line.pk}', 'productId': line.purchase_line.stock_item_id,
                         'date': line.note.receipt_date.isoformat(), 'type': 'IN',
                         'quantity': float(line.quantity_received - line.damaged_quantity), 'unit': line.purchase_line.unit,
                         'reference': line.note.number, 'remarks': line.remarks, 'customer': line.note.supplier_name,
                         'batch': 'reference-stock', 'movementPurpose': 'receipt',
                         'purchaseOrderId': str(line.note.purchase_order_id), 'goodsReceivedNoteId': str(line.note_id)}
                        for line in grn_lines if line.quantity_received > line.damaged_quantity]
        return Response({'products': [item_data(item) for item in FinishedStockItem.objects.all()],
                         'categories': finished_categories(),
                         'canAddCategory': can_add_category(request.user),
                         'receipts': [{'id': f'purchase-line-{line.pk}', 'productId': line.stock_item_id,
                                       'date': line.order.order_date.isoformat(), 'type': 'IN',
                                       'quantity': float(line.quantity), 'unit': line.unit,
                                       'reference': line.order.number, 'remarks': line.order.notes,
                                       'customer': line.order.supplier_name, 'batch': 'reference-stock',
                                       'movementPurpose': 'receipt', 'purchaseOrderId': str(line.order_id)} for line in lines] + grn_receipts})
