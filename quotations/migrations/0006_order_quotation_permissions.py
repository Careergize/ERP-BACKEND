from django.db import migrations


def grant_order_quotation_access(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    content_type, _ = ContentType.objects.get_or_create(app_label='quotations', model='quotation')
    for action in ('view', 'add'):
        permission, _ = Permission.objects.get_or_create(content_type=content_type, codename=f'{action}_quotation', defaults={'name': f'Can {action} quotation'})
        for role in Role.objects.filter(code__in=('receptionist', 'admin')):
            role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('quotations', '0005_quotationdraft'), ('accounts', '0003_seed_role_master')]
    operations = [migrations.RunPython(grant_order_quotation_access, migrations.RunPython.noop)]
