"""Operational Sales documents; source invoices remain in their existing ERP register."""
import uuid
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from .models import Customer, SharedEntry, ImmutableEvidence, EvidenceQuerySet


class SalesDocument(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    number=models.CharField(max_length=80,unique=True,editable=False)
    customer=models.ForeignKey(Customer,on_delete=models.PROTECT,related_name='+')
    source=models.ForeignKey(SharedEntry,on_delete=models.PROTECT,related_name='+')
    invoice_id=models.CharField(max_length=100)
    line_key=models.CharField(max_length=150)
    snapshot=models.JSONField()
    date=models.DateField()
    quantity=models.DecimalField(max_digits=20,decimal_places=6)
    net=models.DecimalField(max_digits=24,decimal_places=2)
    vat=models.DecimalField(max_digits=24,decimal_places=2)
    led=models.DecimalField(max_digits=24,decimal_places=2)
    total=models.DecimalField(max_digits=24,decimal_places=2)
    reason=models.CharField(max_length=500)
    remarks=models.TextField()
    status=models.CharField(max_length=20,default='Draft')
    version=models.PositiveBigIntegerField(default=1)
    contributors=models.JSONField(default=list)
    created_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,related_name='+')
    reviewed_by=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT,null=True,related_name='+')
    created_at=models.DateTimeField(auto_now_add=True)
    updated_at=models.DateTimeField(auto_now=True)
    approved_at=models.DateTimeField(null=True)
    objects=EvidenceQuerySet.as_manager()
    class Meta:
        abstract=True
        default_permissions=()
        ordering=['date','number']
    def save(self,*args,**kwargs):
        old=type(self).objects.filter(pk=self.pk).first()
        if old:
            if old.status in {'Issued','Rejected','Cancelled'}:raise ValidationError('Terminal Sales documents are immutable.')
            if self.version!=old.version+1:raise ValidationError('Use the versioned Sales command service.')
            fixed=['number','customer_id','source_id','invoice_id','line_key','snapshot','created_by_id','created_at']
            if any(getattr(self,f)!=getattr(old,f) for f in fixed):raise ValidationError('Sales source identity is immutable.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):raise ValidationError('Sales documents cannot be deleted.')


class SalesReturn(SalesDocument):
    condition=models.CharField(max_length=100)
    qc_status=models.CharField(max_length=20,default='Uninspected')
    class Meta(SalesDocument.Meta):
        abstract=False
        permissions=[('view_sales','View Sales reporting'),('edit_sales','Prepare Sales drafts'),('review_sales','Independently review Sales documents'),('inspect_sales','Inspect returned goods'),('issue_sales','Issue eligible Sales credit notes'),('view_all_sales','View all Sales documents')]
        constraints=[models.CheckConstraint(condition=models.Q(quantity__gt=0),name='sales_return_positive'),models.CheckConstraint(condition=models.Q(status__in=['Draft','Submitted','Pending Approval','Approved','Rejected','Cancelled']),name='sales_return_status'),models.CheckConstraint(condition=models.Q(reviewed_by__isnull=True)|~models.Q(created_by=models.F('reviewed_by')),name='sales_return_independent')]


class SalesCreditNote(SalesDocument):
    sales_return=models.ForeignKey(SalesReturn,on_delete=models.PROTECT,related_name='credits')
    issued_at=models.DateTimeField(null=True)
    gl_status=models.CharField(max_length=20,default='Not posted',editable=False)
    class Meta(SalesDocument.Meta):
        abstract=False
        constraints=[models.CheckConstraint(condition=models.Q(quantity__gt=0),name='sales_credit_positive'),models.CheckConstraint(condition=models.Q(status__in=['Draft','Submitted','Pending Approval','Approved','Issued','Rejected','Cancelled']),name='sales_credit_status'),models.UniqueConstraint(fields=['sales_return'],condition=~models.Q(status__in=['Rejected','Cancelled']),name='sales_credit_once_per_return'),models.CheckConstraint(condition=models.Q(reviewed_by__isnull=True)|~models.Q(created_by=models.F('reviewed_by')),name='sales_credit_independent')]


class SalesReturnInspection(ImmutableEvidence):
    id=models.UUIDField(primary_key=True,default=uuid.uuid4,editable=False)
    sales_return=models.ForeignKey(SalesReturn,on_delete=models.PROTECT,related_name='inspections')
    result=models.CharField(max_length=8)
    inspected_quantity=models.DecimalField(max_digits=20,decimal_places=6)
    accepted_quantity=models.DecimalField(max_digits=20,decimal_places=6)
    remarks=models.TextField()
    inspector=models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    recorded_at=models.DateTimeField(auto_now_add=True)
    qc_reference=models.CharField(max_length=100,blank=True)
    class Meta(ImmutableEvidence.Meta):
        constraints=[models.CheckConstraint(condition=models.Q(inspected_quantity__gt=0,accepted_quantity__gte=0,accepted_quantity__lte=models.F('inspected_quantity')),name='sales_inspection_quantity'),models.CheckConstraint(condition=models.Q(result__in=['Pass','Fail','Hold']),name='sales_inspection_result')]
