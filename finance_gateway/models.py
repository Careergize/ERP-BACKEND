"""Integration metadata only. No native provisioning or financial configuration."""
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

CAPABILITIES = ('finance.access.read', 'finance.setup.read', 'finance.accounts.read',
                'finance.parties.read', 'finance.reports.read')
ACTIONS = ('finance.access.read', 'finance.setup.read', 'finance.accounts.read',
           'finance.parties.read', 'finance.reports.read')
REASONS = ('ok', 'authentication_required', 'access_denied', 'integration_disabled',
           'credentials_unavailable', 'destination_denied', 'integration_unavailable')


class Metadata(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class FinanceSite(Metadata):
    reference = models.SlugField(max_length=64, unique=True)
    base_url = models.URLField(max_length=255)
    native_site_name = models.CharField(max_length=140)
    enabled = models.BooleanField(default=False)

    def clean(self):
        from .destinations import validate_destination
        validate_destination(self.base_url, self.native_site_name)


class FinanceCompanyReference(Metadata):
    site = models.ForeignKey(FinanceSite, on_delete=models.PROTECT)
    reference = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    native_company_name = models.CharField(max_length=140)
    display_name = models.CharField(max_length=200)
    enabled = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['site', 'native_company_name'], name='fin_company_site_unique')]


class FinanceIdentityBinding(Metadata):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    site = models.ForeignKey(FinanceSite, on_delete=models.PROTECT)
    native_username = models.CharField(max_length=140)
    credential_reference = models.CharField(max_length=255, blank=True, default='')
    enabled = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'site'], name='fin_identity_user_site_unique'),
            models.UniqueConstraint(Lower('native_username'), 'site', name='fin_identity_native_unique'),
            models.CheckConstraint(condition=~models.Q(native_username__in=['']) &
                                   ~models.Q(native_username__iexact='Administrator') &
                                   ~models.Q(native_username__iexact='Guest'), name='fin_identity_safe_username'),
        ]

    def clean(self):
        if (self.native_username != self.native_username.strip() or
                self.native_username.casefold() in {'', 'administrator', 'guest'}):
            raise ValidationError('Invalid native identity.')


class FinanceCompanyGrant(Metadata):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    company = models.ForeignKey(FinanceCompanyReference, on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'company'], name='fin_company_grant_unique')]


class FinanceCapabilityGrant(Metadata):
    company_grant = models.ForeignKey(FinanceCompanyGrant, on_delete=models.PROTECT, related_name='capabilities')
    capability = models.CharField(max_length=64, choices=[(v, v) for v in CAPABILITIES])
    enabled = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['company_grant', 'capability'], name='fin_capability_grant_unique'),
            models.CheckConstraint(condition=models.Q(capability__in=CAPABILITIES), name='fin_capability_allowed'),
        ]


class AuditQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('Audit events are append-only.')

    def delete(self):
        raise ValidationError('Audit events are append-only.')

    def bulk_update(self, *args, **kwargs):
        raise ValidationError('Audit events are append-only.')

    def bulk_create(self, *args, **kwargs):
        raise ValidationError('Use the validated audit writer.')


class FinanceAuditEvent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    occurred_at = models.DateTimeField(auto_now_add=True)
    correlation_id = models.UUIDField(default=uuid.uuid4, db_index=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    site = models.ForeignKey(FinanceSite, null=True, blank=True, on_delete=models.PROTECT)
    company = models.ForeignKey(FinanceCompanyReference, null=True, blank=True, on_delete=models.PROTECT)
    action = models.CharField(max_length=64, choices=[(v, v) for v in ACTIONS])
    outcome = models.CharField(max_length=16, choices=[(v, v) for v in ('allowed', 'denied', 'disabled', 'unavailable', 'error')])
    reason_code = models.CharField(max_length=64, choices=[(v, v) for v in REASONS])
    http_status = models.PositiveSmallIntegerField()
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    objects = AuditQuerySet.as_manager()

    class Meta:
        base_manager_name = 'objects'
        indexes = [models.Index(fields=['actor', 'occurred_at'], name='fin_audit_actor_time'),
                   models.Index(fields=['company', 'occurred_at'], name='fin_audit_company_time')]

    def save(self, *args, **kwargs):
        if not self._state.adding or type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError('Audit events are append-only.')
        self.full_clean()
        kwargs['force_insert'] = True
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Audit events are append-only.')
