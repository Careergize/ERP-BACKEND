from django.db import migrations


def grant_supporting_record_permissions(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    alias = schema_editor.connection.alias
    writers = {'admin', 'accounts', 'receptionist', 'production_manager', 'project_manager', 'qc_inspector', 'qc_supervisor', 'store_keeper'}
    managers = {'admin', 'accounts', 'production_manager', 'project_manager'}
    for model, action, codes in [('sharedentry', 'view', None), ('sharedentry', 'change', writers), ('entryrevision', 'view', managers)]:
        content_type, _ = ContentType.objects.using(alias).get_or_create(app_label='erp', model=model)
        permission, _ = Permission.objects.using(alias).get_or_create(content_type=content_type, codename=f'{action}_{model}', defaults={'name': f'Can {action} {model}'})
        roles = Role.objects.using(alias).all() if codes is None else Role.objects.using(alias).filter(code__in=codes)
        for role in roles:
            role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('erp', '0017_shared_entry_history')]
    operations = [migrations.RunPython(grant_supporting_record_permissions, migrations.RunPython.noop)]
