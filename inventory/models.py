from django.conf import settings
from django.db import models
from django.utils import timezone
from decimal import Decimal
import uuid


class RawMaterial(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    name = models.CharField(max_length=250)
    family = models.CharField(max_length=200)
    category = models.CharField(max_length=20, choices=[('virgin', 'Virgin Material'), ('recycled', 'Recycled Granules'), ('ink', 'Ink')])
    unit = models.CharField(max_length=30)
    opening_stock = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    opening_date = models.DateField()
    min_level = models.DecimalField(max_digits=20, decimal_places=6, null=True)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.name} ({self.unit})'


class RawMovement(models.Model):
    # Stable client request UUID, or the original reference ledger ID.
    id = models.CharField(primary_key=True, max_length=100)
    material = models.ForeignKey(RawMaterial, on_delete=models.PROTECT, related_name='movements')
    date = models.DateField()
    movement_type = models.CharField(max_length=3, choices=[('IN', 'IN'), ('OUT', 'OUT')])
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    reference = models.CharField(max_length=250, blank=True)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='raw_quantity_positive'),
                       models.CheckConstraint(condition=models.Q(movement_type__in=['IN', 'OUT']), name='raw_movement_direction')]


class ProcurementPlan(models.Model):
    material = models.OneToOneField(RawMaterial, primary_key=True, on_delete=models.PROTECT)
    stock_in_store = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    as_of_date = models.DateField(default=timezone.localdate)
    monthly = models.DecimalField(max_digits=20, decimal_places=6, default=0)
    days = models.PositiveSmallIntegerField(default=26)
    lead = models.PositiveSmallIntegerField(default=75)
    comment = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(stock_in_store__isnull=True) | models.Q(stock_in_store__gte=0), name='procurement_stock_nonnegative'),
            models.CheckConstraint(condition=models.Q(monthly__gte=0), name='procurement_monthly_nonnegative'),
            models.CheckConstraint(condition=models.Q(days__gte=1, days__lte=31), name='procurement_days_valid'),
            models.CheckConstraint(condition=models.Q(lead__lte=3650), name='procurement_lead_valid'),
            models.CheckConstraint(condition=models.Q(version__gte=1), name='procurement_version_positive'),
        ]

    def __str__(self):
        return f'{self.material} - plan v{self.version}'

    def remaining_stock(self, date=None):
        # Consumption rates are forecasts only. Stock changes require manual entry.
        return self.stock_in_store


class ProcurementDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    plan = models.ForeignKey(ProcurementPlan, on_delete=models.PROTECT, related_name='expected_deliveries')
    supplier = models.CharField(max_length=250)
    expected_date = models.DateField()
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['expected_date', 'id']
        constraints = [models.CheckConstraint(condition=models.Q(quantity__gt=0), name='procurement_delivery_positive')]

    def __str__(self):
        return f'{self.supplier} - {self.expected_date} - {self.quantity}'


class ProcurementPlanRevision(models.Model):
    plan = models.ForeignKey(ProcurementPlan, on_delete=models.PROTECT, related_name='revisions')
    version = models.PositiveIntegerField()
    snapshot = models.JSONField()
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-version']
        constraints = [models.UniqueConstraint(fields=['plan', 'version'], name='unique_procurement_plan_revision')]
