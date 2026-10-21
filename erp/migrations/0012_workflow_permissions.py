from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    rules = {'productionprocess': ['view', 'add'], 'productionmodelrevision': ['view', 'add'],
             'workfloworder': ['view', 'add'], 'workflowjob': ['view', 'change', 'approve', 'issue']}
    for model, actions in rules.items():
        ct, _ = ContentType.objects.get_or_create(app_label='erp', model=model)
        for action in actions:
            permission, _ = Permission.objects.get_or_create(content_type=ct, codename=f'{action}_{model}', defaults={'name': f'Can {action} {model}'})
            for role in Role.objects.all():
                permitted = action == 'view' or role.code == 'admin'
                if model in ('productionprocess', 'productionmodelrevision') and role.code in ('production_manager', 'receptionist'):
                    permitted = True
                if model == 'workfloworder' and role.code == 'receptionist':
                    permitted = True
                if model == 'workflowjob' and action in ('change', 'approve') and role.code == 'production_manager':
                    permitted = True
                if model == 'workflowjob' and action == 'issue' and role.code == 'store_keeper':
                    permitted = True
                if permitted:
                    role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('erp', '0011_productionprocess_productionmodelrevision_and_more')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
