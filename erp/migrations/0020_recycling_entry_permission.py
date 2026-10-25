from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    alias = schema_editor.connection.alias
    permission = Permission.objects.using(alias).get(content_type__app_label='erp', codename='change_sharedentry')
    for role in Role.objects.using(alias).filter(code='recycling_operator'):
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('erp', '0019_production_tracking_permission')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
