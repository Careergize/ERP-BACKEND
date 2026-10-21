from django.db import migrations


def grant(apps, schema_editor):
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ct, _ = ContentType.objects.get_or_create(app_label='erp', model='goodsreceivednote')
    permissions = [Permission.objects.get_or_create(content_type=ct, codename=f'{action}_goodsreceivednote',
                   defaults={'name': f'Can {action} goods received note'})[0] for action in ('view', 'add')]
    for role in Role.objects.filter(code__in=['admin', 'store_keeper', 'project_manager', 'production_manager', 'accounts']):
        role.permissions.add(*permissions)


class Migration(migrations.Migration):
    dependencies = [('erp', '0009_purchaseorder_receipt_method_goodsreceivednote_and_more')]
    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
