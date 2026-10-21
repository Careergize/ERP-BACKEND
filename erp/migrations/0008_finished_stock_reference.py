import json
from pathlib import Path
from django.db import migrations


def seed(apps, schema_editor):
    Item = apps.get_model('erp', 'FinishedStockItem')
    for row in json.loads(Path(__file__).with_name('finished_stock_reference.json').read_text(encoding='utf-8')):
        Item.objects.get_or_create(pk=row['id'], defaults={
            'name': row['itemDetails'] or row['subgroup'], 'category': row['category'],
            'subgroup': row['subgroup'], 'customer': row['customer'], 'size': row['size'],
            'brand': row['brand'], 'packing_size': row['packingSize'], 'unit': row['unit'],
            'opening_stock': row['openingStock'], 'opening_date': row['openingDate'], 'reference': row})
    Role = apps.get_model('accounts', 'Role')
    Permission = apps.get_model('auth', 'Permission')
    ContentType = apps.get_model('contenttypes', 'ContentType')
    ct, _ = ContentType.objects.get_or_create(app_label='erp', model='finishedstockitem')
    permission, _ = Permission.objects.get_or_create(content_type=ct, codename='view_finishedstockitem',
                                                    defaults={'name': 'Can view finished stock item'})
    for role in Role.objects.all():
        role.permissions.add(permission)


class Migration(migrations.Migration):
    dependencies = [('erp', '0007_finishedstockitem_purchaseorderline_stock_details_and_more')]
    # Keep reference identities on rollback; never delete a master used by purchases.
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
