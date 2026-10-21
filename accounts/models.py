from django.contrib.auth.models import AbstractUser, Permission
from django.db import models


class RoleCode(models.TextChoices):
    ADMIN = 'admin', 'Admin'
    RECEPTIONIST = 'receptionist', 'Receptionist'
    PRODUCTION_MANAGER = 'production_manager', 'Production Manager'
    STORE_KEEPER = 'store_keeper', 'Store Keeper'
    PRODUCTION_OPERATOR = 'production_operator', 'Production Operator'
    RECYCLING_OPERATOR = 'recycling_operator', 'Recycling Operator'
    ACCOUNTS = 'accounts', 'Accounts'
    QC_INSPECTOR = 'qc_inspector', 'QC Inspector'
    QC_SUPERVISOR = 'qc_supervisor', 'QC Supervisor'
    PROJECT_MANAGER = 'project_manager', 'Project Manager'


class Role(models.Model):
    code = models.CharField(max_length=32, choices=RoleCode.choices, unique=True)
    name = models.CharField(max_length=100)
    is_active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(Permission, blank=True, related_name='erp_roles')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class User(AbstractUser):
    Role = RoleCode
    # Retained for compatibility with existing records and integrations.
    role = models.CharField(max_length=32, choices=RoleCode.choices, default=RoleCode.RECEPTIONIST)
    assigned_role = models.ForeignKey('accounts.Role', on_delete=models.PROTECT, null=True, blank=True, related_name='users')
    department = models.CharField(max_length=100, blank=True)

    @property
    def effective_role(self):
        if self.assigned_role_id:
            return self.assigned_role.code if self.assigned_role.is_active else ''
        return self.role
