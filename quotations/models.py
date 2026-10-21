import uuid
from django.conf import settings
from django.db import models


class QuotationDraft(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    editor_data = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class Quotation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_id = models.CharField(max_length=100)
    fingerprint = models.CharField(max_length=64)
    document = models.JSONField()
    customer_master = models.ForeignKey('erp.Customer', on_delete=models.PROTECT, null=True, blank=True, related_name='quotation_snapshots')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, related_name='approved_quotations')
    approved_at = models.DateTimeField(null=True)
    rejected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='rejected_quotations')
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['created_by', 'source_id', 'fingerprint'], name='unique_quote_snapshot')]
        permissions = [('approve_quotation', 'Can approve quotation PDF'), ('send_quotation', 'Can send quotation PDF')]


class Delivery(models.Model):
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name='deliveries')
    request_id = models.UUIDField()
    channel = models.CharField(max_length=10)
    recipient = models.CharField(max_length=254)
    # 'accepted' means provider accepted, not confirmed recipient delivery.
    status = models.CharField(max_length=20, default='pending')
    detail = models.CharField(max_length=300, blank=True)
    provider_id = models.CharField(max_length=250, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['quotation', 'request_id', 'channel'], name='unique_quote_delivery_request')]
