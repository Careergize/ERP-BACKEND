import uuid
from decimal import Decimal
from django.core.validators import MinValueValidator
from django.core.exceptions import ValidationError
from django.db import models, transaction


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
    # Inactive controls: receipt status and posting continue to use existing fields.
    control_version = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    approval_status = models.CharField(max_length=16, null=True, blank=True, editable=False,
                                      choices=[('Draft', 'Draft'), ('InReview', 'In review'), ('Approved', 'Approved')])
    current_revision = models.ForeignKey('PurchaseOrderRevision', on_delete=models.PROTECT, null=True, blank=True, related_name='+', editable=False)
    approved_revision = models.ForeignKey('PurchaseOrderRevision', on_delete=models.PROTECT, null=True, blank=True, related_name='+', editable=False)
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
    control_version = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    commercial_status = models.CharField(max_length=16, null=True, blank=True, editable=False,
                                        choices=[('Draft', 'Draft'), ('InReview', 'In review'), ('Confirmed', 'Confirmed'), ('Cancelled', 'Cancelled')])
    current_revision = models.ForeignKey('WorkflowOrderRevision', on_delete=models.PROTECT, null=True, blank=True, related_name='+', editable=False)
    confirmed_revision = models.ForeignKey('WorkflowOrderRevision', on_delete=models.PROTECT, null=True, blank=True, related_name='+', editable=False)
    request_id = models.UUIDField(unique=True)
    data = models.JSONField()
    created_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT)


class WorkflowJob(Record):
    source_confirmed_revision = models.ForeignKey('WorkflowOrderRevision', on_delete=models.PROTECT, null=True, blank=True, related_name='+', editable=False)
    order = models.OneToOneField(WorkflowOrder, on_delete=models.PROTECT, related_name='workflow_job')
    data = models.JSONField()
    version = models.PositiveIntegerField(default=1)

    class Meta(Record.Meta):
        permissions = [('approve_workflowjob', 'Can approve production Job Cards'), ('issue_workflowjob', 'Can issue approved job materials')]


class GoodsReceivedNote(Record):
    control_version = models.PositiveBigIntegerField(null=True, blank=True, editable=False)
    verification_status = models.CharField(max_length=16, null=True, blank=True, editable=False,
                                          choices=[('Pending', 'Pending'), ('Verified', 'Verified'), ('Discrepancy', 'Discrepancy')])
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


class EvidenceQuerySet(models.QuerySet):
    """No mutable ORM shortcuts for evidence; DB grants remain a deployment gate."""

    def update(self, **kwargs):
        raise ValidationError('Evidence cannot be updated through a queryset.')

    def delete(self):
        raise ValidationError('Evidence cannot be deleted.')

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError('Evidence cannot be bulk updated.')

    def bulk_create(self, objs, batch_size=None, ignore_conflicts=False, update_conflicts=False, update_fields=None, unique_fields=None):
        if update_conflicts:
            raise ValidationError('Evidence cannot be replaced on conflict.')
        return super().bulk_create(objs, batch_size=batch_size, ignore_conflicts=ignore_conflicts,
                                   update_conflicts=False, update_fields=update_fields, unique_fields=unique_fields)


class ImmutableEvidence(models.Model):
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        abstract = True
        default_permissions = ()

    def save(self, *args, **kwargs):
        # Check identity even for a fresh Python object with an existing PK.
        if self.pk is not None and type(self).objects.using(kwargs.get('using') or self._state.db or 'default').filter(pk=self.pk).exists():
            raise ValidationError('Evidence is immutable; append a new record.')
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Evidence cannot be deleted.')


class RevisionEvidence(ImmutableEvidence):
    content = models.JSONField()
    content_hash = models.CharField(max_length=64)
    captured_by = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='+')
    captured_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    provenance = models.CharField(max_length=24, choices=[('Contemporaneous', 'Contemporaneous'), ('ObservedAtCutover', 'Observed at cutover')])

    class Meta(ImmutableEvidence.Meta):
        abstract = True
        constraints = [
            models.CheckConstraint(condition=models.Q(content_hash__regex=r'^[0-9a-f]{64}$'), name='%(class)s_hash'),
            models.CheckConstraint(condition=~models.Q(reason__regex=r'^\s*$'), name='%(class)s_reason'),
            models.CheckConstraint(condition=models.Q(provenance__in=['Contemporaneous', 'ObservedAtCutover']), name='%(class)s_provenance'),
        ]


class WorkflowOrderRevision(RevisionEvidence):
    order = models.ForeignKey(WorkflowOrder, on_delete=models.PROTECT, related_name='control_revisions')
    revision = models.PositiveBigIntegerField()

    class Meta(RevisionEvidence.Meta):
        abstract = False
        constraints = RevisionEvidence.Meta.constraints + [
            models.UniqueConstraint(fields=['order', 'revision'], name='workflow_order_revision_unique'),
            models.CheckConstraint(condition=models.Q(revision__gt=0), name='workflow_order_revision_positive'),
        ]


class PurchaseOrderRevision(RevisionEvidence):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name='control_revisions')
    revision = models.PositiveBigIntegerField()

    class Meta(RevisionEvidence.Meta):
        abstract = False
        constraints = RevisionEvidence.Meta.constraints + [
            models.UniqueConstraint(fields=['order', 'revision'], name='purchase_order_revision_unique'),
            models.CheckConstraint(condition=models.Q(revision__gt=0), name='purchase_order_revision_positive'),
        ]


class WorkflowJobRevision(RevisionEvidence):
    job = models.ForeignKey(WorkflowJob, on_delete=models.PROTECT, related_name='control_revisions')
    source_order_revision = models.ForeignKey(WorkflowOrderRevision, on_delete=models.PROTECT, related_name='+')
    job_version = models.PositiveBigIntegerField()

    class Meta(RevisionEvidence.Meta):
        abstract = False
        constraints = RevisionEvidence.Meta.constraints + [
            models.UniqueConstraint(fields=['job', 'job_version'], name='workflow_job_revision_unique'),
            models.CheckConstraint(condition=models.Q(job_version__gt=0), name='workflow_job_version_positive'),
        ]


def one_evidence_target(*names):
    """Portable local XOR; cross-row ownership needs future locked services."""
    result = models.Q()
    for name in names:
        term = models.Q(**{name + '__isnull': False})
        for other in names:
            if other != name:
                term &= models.Q(**{other + '__isnull': True})
        result |= term
    return result


class ContributorEvidence(ImmutableEvidence):
    event_id = models.UUIDField(unique=True)
    order_revision = models.ForeignKey(WorkflowOrderRevision, on_delete=models.PROTECT, null=True, related_name='contributors')
    job_revision = models.ForeignKey(WorkflowJobRevision, on_delete=models.PROTECT, null=True, related_name='contributors')
    purchase_revision = models.ForeignKey(PurchaseOrderRevision, on_delete=models.PROTECT, null=True, related_name='contributors')
    actor = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=80)
    captured_at = models.DateTimeField(auto_now_add=True)

    class Meta(ImmutableEvidence.Meta):
        constraints = [
            models.CheckConstraint(condition=one_evidence_target('order_revision', 'job_revision', 'purchase_revision'), name='contributor_exact_revision'),
            models.CheckConstraint(condition=~models.Q(action__regex=r'^\s*$'), name='contributor_action_nonblank'),
        ]


class ApprovalDecision(ImmutableEvidence):
    workflow_order = models.ForeignKey(WorkflowOrder, on_delete=models.PROTECT, null=True, related_name='+')
    workflow_job = models.ForeignKey(WorkflowJob, on_delete=models.PROTECT, null=True, related_name='+')
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, null=True, related_name='+')
    goods_received_note = models.ForeignKey(GoodsReceivedNote, on_delete=models.PROTECT, null=True, related_name='+')
    quotation = models.ForeignKey('quotations.Quotation', on_delete=models.PROTECT, null=True, related_name='+')
    snapshot = models.JSONField()
    target_version = models.PositiveBigIntegerField()
    content_hash = models.CharField(max_length=64)
    checker = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=80)
    outcome = models.CharField(max_length=16, choices=[('Approved', 'Approved'), ('Rejected', 'Rejected'), ('Verified', 'Verified'), ('Discrepancy', 'Discrepancy')])
    reason = models.TextField()
    decided_at = models.DateTimeField(auto_now_add=True)
    policy_evidence_reference = models.CharField(max_length=500)
    policy_evidence_hash = models.CharField(max_length=64)

    class Meta(ImmutableEvidence.Meta):
        constraints = [
            models.CheckConstraint(condition=one_evidence_target('workflow_order', 'workflow_job', 'purchase_order', 'goods_received_note', 'quotation'), name='decision_exact_target'),
            models.CheckConstraint(condition=models.Q(target_version__gt=0), name='decision_version_positive'),
            models.CheckConstraint(condition=models.Q(content_hash__regex=r'^[0-9a-f]{64}$', policy_evidence_hash__regex=r'^[0-9a-f]{64}$'), name='decision_valid_hashes'),
            models.CheckConstraint(condition=~models.Q(reason__regex=r'^\s*$') & ~models.Q(action__regex=r'^\s*$') & ~models.Q(policy_evidence_reference__regex=r'^\s*$'), name='decision_required_evidence'),
            models.CheckConstraint(condition=models.Q(outcome__in=['Approved', 'Rejected', 'Verified', 'Discrepancy']), name='decision_valid_outcome'),
        ]
        indexes = [models.Index(fields=['checker', 'decided_at'], name='decision_checker_time')]


class CommandReceipt(models.Model):
    command_id = models.UUIDField(primary_key=True)
    actor = models.ForeignKey('accounts.User', on_delete=models.PROTECT, related_name='+')
    action = models.CharField(max_length=80)
    workflow_order = models.ForeignKey(WorkflowOrder, on_delete=models.PROTECT, null=True, related_name='+')
    workflow_job = models.ForeignKey(WorkflowJob, on_delete=models.PROTECT, null=True, related_name='+')
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, null=True, related_name='+')
    goods_received_note = models.ForeignKey(GoodsReceivedNote, on_delete=models.PROTECT, null=True, related_name='+')
    quotation = models.ForeignKey('quotations.Quotation', on_delete=models.PROTECT, null=True, related_name='+')
    schema_version = models.PositiveIntegerField()
    payload_digest = models.CharField(max_length=64)
    response_status = models.PositiveSmallIntegerField(null=True)
    response_body = models.JSONField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True)
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=one_evidence_target('workflow_order', 'workflow_job', 'purchase_order', 'goods_received_note', 'quotation') | models.Q(workflow_order__isnull=True, workflow_job__isnull=True, purchase_order__isnull=True, goods_received_note__isnull=True, quotation__isnull=True), name='receipt_at_most_one_target'),
            models.CheckConstraint(condition=models.Q(completed_at__isnull=True, response_status__isnull=True, response_body__isnull=True) | (models.Q(completed_at__isnull=False, response_status__isnull=False, response_status__gte=200, response_status__lte=299, response_body__isnull=False) & one_evidence_target('workflow_order', 'workflow_job', 'purchase_order', 'goods_received_note', 'quotation')), name='receipt_completion_evidence'),
            models.CheckConstraint(condition=models.Q(schema_version__gt=0, payload_digest__regex=r'^[0-9a-f]{64}$') & ~models.Q(action__regex=r'^\s*$'), name='receipt_valid_command'),
        ]
        indexes = [models.Index(fields=['actor', 'created_at'], name='receipt_actor_time')]

    def save(self, *args, **kwargs):
        database = kwargs.get('using') or self._state.db or 'default'
        with transaction.atomic(using=database):
            previous = type(self).objects.using(database).select_for_update().filter(pk=self.pk).first()
            if previous:
                targets = ('workflow_order_id', 'workflow_job_id', 'purchase_order_id', 'goods_received_note_id', 'quotation_id')
                fixed = ('actor_id', 'action', 'schema_version', 'payload_digest', 'created_at')
                if previous.completed_at or not self.completed_at or any(getattr(previous, key) != getattr(self, key) for key in fixed):
                    raise ValidationError('A command receipt may only be completed once.')
                if any(getattr(previous, key) is not None and getattr(previous, key) != getattr(self, key) for key in targets):
                    raise ValidationError('A command target cannot be replaced.')
                kwargs['force_update'] = True
            else:
                kwargs['force_insert'] = True
            return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Command receipts cannot be deleted.')
