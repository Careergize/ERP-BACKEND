import uuid
from decimal import Decimal
from django.conf import settings
from django.db import models

UNITS = [(x, x) for x in ('Each', 'Both', 'Litres', 'Kilos')]

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
    pass

class SpareStockMovement(models.Model):
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

    class Meta:
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(condition=models.Q(movement_type__in=['IN', 'OUT']), name='spare_movement_direction'),
            models.CheckConstraint(condition=models.Q(quantity__gt=0) | models.Q(is_opening=True, quantity=0), name='spare_quantity_valid'),
            models.CheckConstraint(condition=models.Q(is_opening=False) | models.Q(movement_type='IN'), name='spare_opening_in'),
            models.UniqueConstraint(fields=['item'], condition=models.Q(is_opening=True), name='spare_single_opening'),
        ]
