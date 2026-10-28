import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from erp.models import ImmutableEvidence

UNITS = [(x, x) for x in ('Each', 'Both', 'Litres', 'Kilos', 'Pair', 'Set', 'Box', 'Pack', 'Roll', 'KG', 'Gram', 'Litre', 'Millilitre', 'Metre')]

class CatalogueItemQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if 'code' in kwargs:
            raise ValidationError('Item codes are immutable.')
        return super().update(**kwargs)

    def bulk_update(self, objs, fields, **kwargs):
        if 'code' in fields:
            raise ValidationError('Item codes are immutable.')
        return super().bulk_update(objs, fields, **kwargs)


class RegisterItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=250)
    description = models.TextField(blank=True)
    unit = models.CharField(max_length=30, choices=UNITS)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='+')
    supplier = models.CharField(max_length=250, blank=True)
    department = models.CharField(max_length=100, blank=True)
    storage_location = models.CharField(max_length=250, blank=True)
    expense_classification = models.CharField(max_length=250, blank=True)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    creation_request_id = models.UUIDField(unique=True, null=True, blank=True, editable=False)
    creation_digest = models.CharField(max_length=64, blank=True, editable=False)
    objects = CatalogueItemQuerySet.as_manager()

    def save(self, *args, **kwargs):
        previous = type(self).objects.filter(pk=self.pk).values_list('code', flat=True).first()
        if previous is not None and previous != self.code:
            raise ValidationError('Item codes are immutable.')
        return super().save(*args, **kwargs)

    class Meta:
        abstract = True
        ordering = ['name', 'id']

class SpareItem(RegisterItem):
    category = models.CharField(max_length=8, choices=[('IMPORTED', 'Imported Spare'), ('LOCAL', 'Local Spare')])
    minimum_stock = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal('0'))

    class Meta(RegisterItem.Meta):
        abstract = False
        constraints = [models.CheckConstraint(condition=models.Q(minimum_stock__gte=0), name='spare_minimum_nonnegative')]

class Consumable(RegisterItem):
    category = models.CharField(max_length=100, blank=True)
    subcategory = models.CharField(max_length=100, blank=True)
    minimum_stock = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal('0'))
    reorder_quantity = models.DecimalField(max_digits=20, decimal_places=6, default=Decimal('0'))
    machine = models.CharField(max_length=250, blank=True)

    class Meta(RegisterItem.Meta):
        abstract = False
        constraints = [
            models.CheckConstraint(condition=models.Q(minimum_stock__gte=0), name='consumable_minimum_nonnegative'),
            models.CheckConstraint(condition=models.Q(reorder_quantity__gte=0), name='consumable_reorder_nonnegative'),
            models.CheckConstraint(condition=models.Q(unit_cost__isnull=True) | models.Q(unit_cost__gte=0), name='consumable_cost_nonnegative'),
        ]

class SpareStockMovement(ImmutableEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(SpareItem, on_delete=models.PROTECT, related_name='movements')
    request_id = models.UUIDField(unique=True)
    movement_type = models.CharField(max_length=3, choices=[('IN', 'IN'), ('OUT', 'OUT')])
    is_opening = models.BooleanField(default=False)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=30, choices=UNITS)
    reference = models.CharField(max_length=250)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    transaction_reference = models.CharField(max_length=80, unique=True, null=True, editable=False)
    date = models.DateField(null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    supplier = models.CharField(max_length=250, blank=True)
    department = models.CharField(max_length=100, blank=True)
    storage_location = models.CharField(max_length=250, blank=True)
    received_issued_by = models.CharField(max_length=250, blank=True)
    issued_to = models.CharField(max_length=250, blank=True)
    purpose = models.CharField(max_length=100, blank=True)
    machine = models.CharField(max_length=250, blank=True)
    job_reference = models.CharField(max_length=250, blank=True)
    purchase_order_reference = models.CharField(max_length=250, blank=True)
    invoice_reference = models.CharField(max_length=250, blank=True)
    remarks = models.TextField(blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(movement_type__in=['IN', 'OUT']), name='spare_movement_direction'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0) | models.Q(is_opening=True, quantity=0), name='spare_quantity_valid'),
            models.CheckConstraint(condition=models.Q(is_opening=False) | models.Q(movement_type='IN'), name='spare_opening_in'),
            models.UniqueConstraint(fields=['item'], condition=models.Q(is_opening=True), name='spare_single_opening'),
        ]


class ConsumableStockMovement(ImmutableEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(Consumable, on_delete=models.PROTECT, related_name='movements')
    request_id = models.UUIDField(unique=True)
    movement_type = models.CharField(max_length=3, choices=[('IN', 'IN'), ('OUT', 'OUT')])
    is_opening = models.BooleanField(default=False)
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=30, choices=UNITS)
    reference = models.CharField(max_length=250)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    transaction_reference = models.CharField(max_length=80, unique=True, null=True, editable=False)
    date = models.DateField(null=True, blank=True)
    unit_cost = models.DecimalField(max_digits=20, decimal_places=6, null=True, blank=True)
    supplier = models.CharField(max_length=250, blank=True)
    department = models.CharField(max_length=100, blank=True)
    storage_location = models.CharField(max_length=250, blank=True)
    received_issued_by = models.CharField(max_length=250, blank=True)
    issued_to = models.CharField(max_length=250, blank=True)
    purpose = models.CharField(max_length=100, blank=True)
    machine = models.CharField(max_length=250, blank=True)
    job_reference = models.CharField(max_length=250, blank=True)
    purchase_order_reference = models.CharField(max_length=250, blank=True)
    invoice_reference = models.CharField(max_length=250, blank=True)
    remarks = models.TextField(blank=True)
    payload_hash = models.CharField(max_length=64, blank=True, editable=False)

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(movement_type__in=['IN', 'OUT']), name='consumable_movement_direction'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0) | models.Q(is_opening=True, quantity=0), name='consumable_quantity_valid'),
            models.CheckConstraint(condition=models.Q(is_opening=False) | models.Q(movement_type='IN'), name='consumable_opening_in'),
            models.UniqueConstraint(fields=['item'], condition=models.Q(is_opening=True), name='consumable_single_opening'),
        ]


class CatalogueSequence(models.Model):
    scope = models.CharField(max_length=80, primary_key=True)
    value = models.PositiveBigIntegerField(default=0)

    class Meta:
        default_permissions = ()


class ConsumableCodeReservation(ImmutableEvidence):
    code = models.CharField(max_length=80, primary_key=True)

    class Meta:
        default_permissions = ()
