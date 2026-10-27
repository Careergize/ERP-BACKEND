from django.conf import settings
from django.db import models


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValueError('Posted accounting rows are append-only; use a reversing command.')

    def delete(self):
        raise ValueError('Posted accounting rows cannot be deleted.')


class AppendOnlyRow(models.Model):
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValueError('Accounting evidence is immutable.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Accounting evidence cannot be deleted.')


class Book(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    company = models.CharField(max_length=200)
    currency = models.CharField(max_length=3)
    start_date = models.DateField()
    fiscal_month = models.PositiveSmallIntegerField(default=1)
    closed_through = models.DateField(null=True, blank=True)
    valuation = models.CharField(max_length=20, default='weighted-average')
    led_account = models.CharField(max_length=30, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)


class Account(models.Model):
    code = models.CharField(max_length=30, primary_key=True)
    name = models.CharField(max_length=150)
    kind = models.CharField(max_length=12, choices=[(v, v) for v in ['asset', 'liability', 'equity', 'income', 'expense']])
    cash = models.BooleanField(default=False)
    control = models.BooleanField(default=False)


class Party(models.Model):
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=10, choices=[('customer', 'Customer'), ('supplier', 'Supplier')])
    source = models.CharField(max_length=100, unique=True, null=True)


class Document(models.Model):
    objects = AppendOnlyQuerySet.as_manager()
    command = models.UUIDField(unique=True)
    digest = models.CharField(max_length=64)
    source = models.CharField(max_length=180, unique=True, null=True)
    kind = models.CharField(max_length=25)
    number = models.CharField(max_length=80, unique=True)
    date = models.DateField()
    party = models.ForeignKey(Party, null=True, on_delete=models.PROTECT)
    currency = models.CharField(max_length=3)
    fx = models.DecimalField(max_digits=24, decimal_places=8, default=1)
    total = models.DecimalField(max_digits=24, decimal_places=2)
    notes = models.TextField(blank=True)
    cash_class = models.CharField(max_length=15, default='operating')
    reversal_of = models.OneToOneField('self', null=True, on_delete=models.PROTECT, related_name='reversal')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['date', 'id']

    def save(self, *args, **kwargs):
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(total=0).exists():
            raise ValueError('Posted documents are immutable; use a reversal.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError('Posted documents cannot be deleted.')


class DocumentLine(models.Model):
    document = models.ForeignKey(Document, related_name='items', on_delete=models.PROTECT)
    item = models.CharField(max_length=120, blank=True)
    description = models.CharField(max_length=250)
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    unit = models.CharField(max_length=30, blank=True)
    price = models.DecimalField(max_digits=24, decimal_places=6)
    net = models.DecimalField(max_digits=24, decimal_places=2)
    tax = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    category = models.ForeignKey(Account, null=True, on_delete=models.PROTECT)


class JournalLine(AppendOnlyRow):
    document = models.ForeignKey(Document, related_name='journal', on_delete=models.PROTECT)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    party = models.ForeignKey(Party, null=True, on_delete=models.PROTECT)
    debit = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    credit = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    foreign_amount = models.DecimalField(max_digits=24, decimal_places=2, default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=(models.Q(debit__gt=0, credit=0) | models.Q(credit__gt=0, debit=0)), name='accounting_one_side_positive')]


class Allocation(AppendOnlyRow):
    payment = models.ForeignKey(Document, related_name='allocations', on_delete=models.PROTECT)
    bill = models.ForeignKey(Document, related_name='settlements', on_delete=models.PROTECT)
    item_line = models.ForeignKey(DocumentLine, null=True, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=24, decimal_places=2)
    date = models.DateField(null=True)
    recorded_document = models.ForeignKey(Document, null=True, on_delete=models.PROTECT, related_name='allocation_commands')

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_allocation_positive')]


class StockValue(models.Model):
    # Valuation identity includes physical namespace and exact unit.
    item = models.CharField(max_length=120, primary_key=True)
    unit = models.CharField(max_length=30)
    quantity = models.DecimalField(max_digits=24, decimal_places=6, default=0)
    value = models.DecimalField(max_digits=24, decimal_places=2, default=0)
    last_date = models.DateField()


class ValueMovement(AppendOnlyRow):
    document = models.ForeignKey(Document, related_name='valuations', on_delete=models.PROTECT)
    stock = models.ForeignKey(StockValue, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    value = models.DecimalField(max_digits=24, decimal_places=2)


class PostingEvent(models.Model):
    source = models.CharField(max_length=180, primary_key=True)
    payload = models.JSONField()
    document = models.OneToOneField(Document, null=True, on_delete=models.PROTECT)
    error = models.TextField(blank=True)
    cancelled = models.BooleanField(default=False)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)
