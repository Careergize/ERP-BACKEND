from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    codes = [('rawmaterial', 'view'), ('rawmovement', 'view'), ('rawmovement', 'add'),
             ('procurementplan', 'view'), ('procurementplan', 'change')]
    perms = {}
    for model, action in codes:
        ct, _ = ContentType.objects.get_or_create(app_label='inventory', model=model)
        perm, _ = Permission.objects.get_or_create(content_type=ct, codename=f'{action}_{model}', defaults={'name': f'Can {action} {model}'})
        perms[perm.codename] = perm
    for role in Role.objects.all():
        role.permissions.add(perms['view_rawmaterial'], perms['view_rawmovement'])
        if role.code in ['admin', 'store_keeper']:
            role.permissions.add(perms['add_rawmovement'])
        if role.code in ['admin', 'store_keeper', 'production_manager', 'project_manager', 'accounts']:
            role.permissions.add(perms['view_procurementplan'], perms['change_procurementplan'])


class Migration(migrations.Migration):
    dependencies = [('inventory', '0002_reference_inventory'), ('accounts', '0003_seed_role_master')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
