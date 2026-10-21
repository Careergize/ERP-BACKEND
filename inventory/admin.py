from django.contrib import admin
from django.db.models import Q, Sum
from .models import RawMaterial, RawMovement, ProcurementPlan, ProcurementDelivery, ProcurementPlanRevision


class ReadOnlyInventoryAdmin(admin.ModelAdmin):
    """Inspect saved data without bypassing inventory transactions/version checks."""
    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RawMaterial)
class RawMaterialAdmin(ReadOnlyInventoryAdmin):
    list_display = ['name', 'family', 'category', 'unit', 'opening_stock', 'remaining_stock']
    list_filter = ['category', 'family', 'unit']
    search_fields = ['id', 'name', 'family']

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            total_in=Sum('movements__quantity', filter=Q(movements__movement_type='IN')),
            total_out=Sum('movements__quantity', filter=Q(movements__movement_type='OUT')))

    @admin.display(description='Stock in store')
    def remaining_stock(self, obj):
        if obj.opening_stock is None:
            return 'Unknown opening balance'
        return obj.opening_stock + (obj.total_in or 0) - (obj.total_out or 0)


@admin.register(RawMovement)
class RawMovementAdmin(ReadOnlyInventoryAdmin):
    list_display = ['date', 'material', 'movement_type', 'quantity', 'reference', 'created_by', 'created_at']
    list_filter = ['movement_type', 'date', 'material__category']
    search_fields = ['id', 'material__name', 'reference', 'remarks']
    list_select_related = ['material', 'created_by']


@admin.register(ProcurementPlan)
class ProcurementPlanAdmin(ReadOnlyInventoryAdmin):
    list_display = ['material', 'stock_in_store', 'as_of_date', 'monthly', 'days', 'lead', 'version', 'updated_by', 'updated_at']
    search_fields = ['material__name', 'comment']
    list_select_related = ['material', 'updated_by']


@admin.register(ProcurementDelivery)
class ProcurementDeliveryAdmin(ReadOnlyInventoryAdmin):
    list_display = ['plan', 'supplier', 'expected_date', 'quantity', 'updated_by']
    list_filter = ['expected_date']
    search_fields = ['supplier', 'plan__material__name']
    list_select_related = ['plan__material', 'updated_by']


@admin.register(ProcurementPlanRevision)
class ProcurementPlanRevisionAdmin(ReadOnlyInventoryAdmin):
    list_display = ['plan', 'version', 'changed_by', 'changed_at']
    search_fields = ['plan__material__name']
    list_select_related = ['plan__material', 'changed_by']
