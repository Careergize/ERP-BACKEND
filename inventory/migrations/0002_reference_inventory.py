import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from django.db import migrations


def seed(apps, schema_editor):
    Material = apps.get_model('inventory', 'RawMaterial')
    Movement = apps.get_model('inventory', 'RawMovement')
    data = json.loads(Path(__file__).with_name('reference.json').read_text(encoding='utf-8'))
    decimal = lambda v: None if v is None else Decimal(str(v)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)
    for m in data['materials']:
        Material.objects.get_or_create(id=m['id'], defaults={
            'name': m['name'], 'family': m['family'], 'category': m['category'], 'unit': m['unit'],
            'opening_stock': decimal(m['openingStock']), 'opening_date': m['openingDate'], 'min_level': decimal(m.get('minLevel'))})
    for m in data['movements']:
        Movement.objects.get_or_create(id=m['id'], defaults={
            'material_id': m['materialId'], 'date': m['date'], 'movement_type': m['movementType'],
            'quantity': decimal(m['quantity']), 'reference': m['reference'], 'remarks': m['remarks']})


class Migration(migrations.Migration):
    dependencies = [('inventory', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
