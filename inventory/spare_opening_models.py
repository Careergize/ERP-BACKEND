"""Spares opening proposals and immutable independent-review decisions."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from erp.models import EvidenceQuerySet, ImmutableEvidence
from .catalog_models import SpareItem, SpareStockMovement


class SpareOpeningRequest(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission_id = models.UUIDField(unique=True, editable=False)
    item = models.ForeignKey(SpareItem, on_delete=models.PROTECT, related_name='opening_requests')
    quantity = models.DecimalField(max_digits=20, decimal_places=6)
    unit = models.CharField(max_length=30)
    date = models.DateField()
    department = models.CharField(max_length=100, blank=True)
    reference = models.CharField(max_length=250, blank=True)
    remarks = models.TextField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    requested_at = models.DateTimeField(auto_now_add=True)
    payload_digest = models.CharField(max_length=64, editable=False)
    status = models.CharField(max_length=8, default='PENDING', choices=[('PENDING','Pending'),('APPROVED','Approved'),('REJECTED','Rejected')])
    objects = EvidenceQuerySet.as_manager()

    class Meta:
        ordering = ['-requested_at', '-id']
        default_permissions = ()
        permissions = [
            ('request_spareopening', 'Can request spare opening stock'),
            ('review_spareopening', 'Can independently review spare opening stock'),
            ('view_spareopening', 'Can read all spare opening requests'),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name='spare_open_request_nonnegative'),
            models.CheckConstraint(condition=models.Q(status__in=['PENDING','APPROVED','REJECTED']), name='spare_open_request_status'),
            models.CheckConstraint(condition=~models.Q(remarks__regex=r'^\s*$'), name='spare_open_request_reason'),
            models.UniqueConstraint(fields=['item'], condition=models.Q(status='PENDING'), name='spare_single_pending_opening'),
        ]

    def save(self, *args, **kwargs):
        previous = type(self).objects.filter(pk=self.pk).first()
        if previous:
            fixed = ['submission_id','item_id','quantity','unit','date','department','reference','remarks','requested_by_id','requested_at','payload_digest']
            if any(getattr(previous,f) != getattr(self,f) for f in fixed):
                raise ValidationError('Opening request content is immutable. Reject and submit a new request.')
            if previous.status != 'PENDING' or self.status not in {'APPROVED','REJECTED'}:
                raise ValidationError('Opening requests can only be decided once.')
            decision = SpareOpeningDecision.objects.filter(request_id=self.pk).first()
            if not decision or decision.outcome != self.status:
                raise ValidationError('A protected independent decision must exist before changing status.')
        return super().save(*args, **kwargs)

    def delete(self,*args,**kwargs):
        raise ValidationError('Opening requests cannot be deleted.')


class SpareOpeningDecision(ImmutableEvidence):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    command_id = models.UUIDField(unique=True, editable=False)
    request = models.OneToOneField(SpareOpeningRequest, on_delete=models.PROTECT, related_name='decision')
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    outcome = models.CharField(max_length=8, choices=[('APPROVED','Approved'),('REJECTED','Rejected')])
    movement = models.OneToOneField(SpareStockMovement, on_delete=models.PROTECT, null=True, blank=True, related_name='opening_decision')
    remarks = models.TextField()
    payload_digest = models.CharField(max_length=64, editable=False)
    decided_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=~models.Q(requester=models.F('reviewer')), name='spare_open_independent_review'),
            models.CheckConstraint(condition=models.Q(outcome='APPROVED',movement__isnull=False)|models.Q(outcome='REJECTED',movement__isnull=True), name='spare_open_decision_movement'),
            models.CheckConstraint(condition=~models.Q(remarks__regex=r'^\s*$'), name='spare_open_decision_reason'),
        ]
