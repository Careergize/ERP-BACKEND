from django.db import migrations


def seed_roles(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    User = apps.get_model('accounts', 'User')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    roles = {}
    for code, name in User._meta.get_field('role').choices:
        roles[code], _ = Role.objects.get_or_create(code=code, defaults={'name': name})
    content_type, _ = ContentType.objects.get_or_create(app_label='quotations', model='quotation')
    permissions = []
    for code, name in [('view_quotation', 'Can view quotation'), ('approve_quotation', 'Can approve quotation PDF')]:
        permission, _ = Permission.objects.get_or_create(content_type=content_type, codename=code, defaults={'name': name})
        permissions.append(permission)
    for code in ['project_manager', 'production_manager']:
        roles[code].permissions.add(*permissions)
    # Preserve existing accounts, including the previously supported superuser UI.
    for user in User.objects.filter(assigned_role__isnull=True).iterator():
        code = 'admin' if user.is_superuser and user.role == 'receptionist' else user.role
        if code in roles:
            User.objects.filter(pk=user.pk).update(assigned_role=roles[code])


class Migration(migrations.Migration):
    dependencies = [('accounts', '0002_alter_user_role_role_user_assigned_role'), ('quotations', '0004_quotation_rejected_at_quotation_rejected_by_and_more')]
    operations = [migrations.RunPython(seed_roles, migrations.RunPython.noop)]
