from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    permission = Permission.objects.using(schema_editor.connection.alias).get(content_type__app_label='erp', codename='change_sharedentry')
    for role in Role.objects.using(schema_editor.connection.alias).filter(code='production_operator'):
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('erp', '0018_shared_entry_permissions')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
