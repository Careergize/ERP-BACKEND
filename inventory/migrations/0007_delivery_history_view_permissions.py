from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    for model in ['procurementdelivery', 'procurementplanrevision']:
        ct, _ = ContentType.objects.get_or_create(app_label='inventory', model=model)
        permission, _ = Permission.objects.get_or_create(content_type=ct, codename=f'view_{model}', defaults={'name': f'Can view {model}'})
        for role in Role.objects.filter(permissions__content_type__app_label='inventory', permissions__codename='view_procurementplan'):
            role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('inventory', '0006_remove_procurementplan_deliveries')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
