import uuid
from decimal import Decimal
from django.core.validators import MinValueValidator
from django.db import models


def order_number():
    return 'SO-' + uuid.uuid4().hex.upper()


def job_number():
    return 'JC-' + uuid.uuid4().hex.upper()


def customer_code():
    return 'CUS-' + uuid.uuid4().hex.upper()


class Record(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ['-id']


class Customer(Record):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=40, unique=True, default=customer_code)
    creation_key = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    contact_person = models.CharField(max_length=150, blank=True)
    delivery_address = models.TextField(blank=True)
    tin = models.CharField(max_length=80, blank=True)
    customer_type = models.CharField(max_length=3, choices=[('B2B', 'Business'), ('B2C', 'Consumer')], default='B2B')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Unit(Record):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)

    class Meta(Record.Meta):
        ordering = ['code']
        constraints = [models.UniqueConstraint(models.functions.Lower('code'), name='unit_code_case_insensitive')]

    def __str__(self):
        return f'{self.code} - {self.name}'


class Supplier(Record):
    name = models.CharField(max_length=200)
    code = models.CharField(max_length=40, unique=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=40, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Product(Record):
    sku = models.CharField(max_length=80, unique=True)
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=20)
    kind = models.CharField(max_length=20, choices=[('finished_goods', 'Finished goods'), ('raw_material', 'Raw material'), ('consumable', 'Consumable')])
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.sku} - {self.name}'


class SalesOrder(Record):
    number = models.CharField(max_length=40, unique=True, default=order_number, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='sales_orders')
    order_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='draft', editable=False)


class SalesOrderLine(Record):
    order = models.ForeignKey(SalesOrder, on_delete=models.CASCADE, related_name='lines')
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])

    class Meta(Record.Meta):
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='order_line_positive_quantity'),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name='order_line_nonnegative_price'),
        ]


class JobCard(Record):
    number = models.CharField(max_length=40, unique=True, default=job_number, editable=False)
    sales_order = models.OneToOneField(SalesOrder, on_delete=models.PROTECT, related_name='job_card', null=True, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='draft', editable=False)


def purchase_number():
    return 'PO-' + uuid.uuid4().hex.upper()


class PurchaseOrder(Record):
    number = models.CharField(max_length=40, unique=True, default=purchase_number, editable=False)
    request_id = models.UUIDField(unique=True, editable=False)
    supplier_name = models.CharField(max_length=200)
    supplier_reference = models.CharField(max_length=100, blank=True)
    supplier_contact = models.CharField(max_length=200, blank=True)
    supplier_address = models.TextField(blank=True)
    order_date = models.DateField()
    delivery_date = models.DateField(null=True, blank=True)
    delivery_address = models.TextField(blank=True)
    currency = models.CharField(max_length=3)
    payment_terms = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, default='draft', editable=False)
    receipt_method = models.CharField(max_length=10, choices=[('immediate', 'Receive immediately'), ('grn', 'Receive through GRN')], default='immediate')
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, editable=False)


class FinishedStockItem(models.Model):
    id = models.CharField(primary_key=True, max_length=100)
    name = models.CharField(max_length=250)
    category = models.CharField(max_length=20, choices=[('carrier-bags', 'Carrier Bags'), ('flat-bags', 'Flat Bags'), ('packing', 'Packing')])
    subgroup = models.CharField(max_length=200, blank=True)
    customer = models.CharField(max_length=200, blank=True)
    size = models.CharField(max_length=100, blank=True)
    brand = models.CharField(max_length=100, blank=True)
    packing_size = models.CharField(max_length=100, blank=True)
    unit = models.CharField(max_length=30)
    opening_stock = models.DecimalField(max_digits=20, decimal_places=6, null=True)
    opening_date = models.DateField()
    reference = models.JSONField(default=dict)
    # Only new manual variants use this key; repeated source rows retain their IDs.
    identity_key = models.CharField(max_length=64, unique=True, null=True, editable=False)

    class Meta:
        ordering = ['id']

    def __str__(self):
        return f'{self.name} ({self.unit})'


class PurchaseOrderLine(Record):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='lines')
    description = models.CharField(max_length=300)
    unit = models.CharField(max_length=30)
    quantity = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0.001'))])
    unit_price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0'))])
    stock_item = models.ForeignKey(FinishedStockItem, on_delete=models.PROTECT, null=True, blank=True, related_name='purchase_lines')
    stock_mode = models.CharField(max_length=10, default='legacy', choices=[('legacy', 'Legacy entry'), ('existing', 'From stock'), ('manual', 'Manual entry')])
    stock_details = models.JSONField(default=dict)
    stock_posted = models.BooleanField(default=False, editable=False)

    class Meta(Record.Meta):
        ordering = ['id']
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name='purchase_line_positive_quantity'),
            models.CheckConstraint(condition=models.Q(unit_price__gte=0), name='purchase_line_nonnegative_price'),
        ]


def grn_number():
    return 'GRN-' + uuid.uuid4().hex.upper()


class ProductionMachine(Record):
    name = models.CharField(max_length=150, unique=True)
    data = models.JSONField(default=dict)


class ProductionProcess(Record):
    name = models.CharField(max_length=150, unique=True)
    data = models.JSONField(default=dict)


class ProductionModelRevision(Record):
    model_id = models.CharField(max_length=100)
    revision = models.PositiveIntegerField()
    data = models.JSONField()

    class Meta(Record.Meta):
        constraints = [models.UniqueConstraint(fields=['model_id', 'revision'], name='production_model_revision_unique')]


class WorkflowOrder(Record):
    request_id = models.UUIDField(unique=True)
    data = models.JSONField()
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)

    class Meta(Record.Meta):
        permissions = [('approve_workfloworder', 'Can approve Sales Orders and allocate manufacturing Job Cards')]


class WorkflowJob(Record):
    order = models.OneToOneField(WorkflowOrder, on_delete=models.PROTECT, related_name='workflow_job', null=True, blank=True)
    request_id = models.UUIDField(unique=True, null=True, blank=True, editable=False)
    data = models.JSONField()
    version = models.PositiveIntegerField(default=1)

    class Meta(Record.Meta):
        permissions = [('approve_workflowjob', 'Can approve production Job Cards'), ('issue_workflowjob', 'Can issue approved job materials')]


class GoodsReceivedNote(Record):
    number = models.CharField(max_length=40, unique=True, default=grn_number, editable=False)
    request_id = models.UUIDField(unique=True, editable=False)
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='goods_received_notes')
    receipt_date = models.DateField()
    supplier_name = models.CharField(max_length=200, editable=False)
    received_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, editable=False)
    receiver_name = models.CharField(max_length=200, editable=False)
    authorized_person = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)


class GoodsReceivedLine(Record):
    note = models.ForeignKey(GoodsReceivedNote, on_delete=models.PROTECT, related_name='lines')
    purchase_line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name='goods_received_lines')
    previously_received = models.DecimalField(max_digits=14, decimal_places=3, editable=False)
    quantity_received = models.DecimalField(max_digits=14, decimal_places=3, validators=[MinValueValidator(Decimal('0'))])
    damaged_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(Decimal('0'))])
    missing_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0, validators=[MinValueValidator(Decimal('0'))])
    remarks = models.TextField(blank=True)

    class Meta(Record.Meta):
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(fields=['note', 'purchase_line'], name='grn_unique_purchase_line'),
            models.CheckConstraint(condition=models.Q(quantity_received__gte=0, damaged_quantity__gte=0, missing_quantity__gte=0, previously_received__gte=0), name='grn_nonnegative_quantities'),
            models.CheckConstraint(condition=models.Q(damaged_quantity__lte=models.F('quantity_received')), name='grn_damage_within_received'),
        ]
