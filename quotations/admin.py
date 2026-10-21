from django.contrib import admin
from .models import Quotation, Delivery, QuotationDraft


@admin.register(QuotationDraft)
class QuotationDraftAdmin(admin.ModelAdmin):
    list_display = ('id', 'created_by', 'version', 'updated_at')
    readonly_fields = ('id', 'created_by', 'editor_data', 'version', 'created_at', 'updated_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ('id', 'source_id', 'created_by', 'approved_at', 'created_at')
    readonly_fields = ('id', 'source_id', 'fingerprint', 'document', 'created_by', 'created_at', 'submitted_at', 'approved_by', 'approved_at', 'rejected_by', 'rejected_at', 'rejection_reason')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ('quotation', 'channel', 'recipient', 'status', 'created_at')
    readonly_fields = ('quotation', 'request_id', 'channel', 'recipient', 'status', 'detail', 'provider_id', 'created_by', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
