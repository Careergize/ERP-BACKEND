from rest_framework import permissions
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import FinishedStockItem, PurchaseOrderLine, GoodsReceivedLine


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
                         'receipts': [{'id': f'purchase-line-{line.pk}', 'productId': line.stock_item_id,
                                       'date': line.order.order_date.isoformat(), 'type': 'IN',
                                       'quantity': float(line.quantity), 'unit': line.unit,
                                       'reference': line.order.number, 'remarks': line.order.notes,
                                       'customer': line.order.supplier_name, 'batch': 'reference-stock',
                                       'movementPurpose': 'receipt', 'purchaseOrderId': str(line.order_id)} for line in lines] + grn_receipts})
