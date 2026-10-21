from django.db import migrations


def seed_units(apps, schema_editor):
    Unit = apps.get_model('erp', 'Unit')
    for code, name in [('kg', 'Kilogram'), ('ctn', 'Carton'), ('pcs', 'Pieces'), ('roll', 'Roll'), ('pack', 'Pack'), ('ltr', 'Litre'), ('m', 'Metre')]:
        Unit.objects.get_or_create(code=code, defaults={'name': name})


class Migration(migrations.Migration):
    dependencies = [('erp', '0003_unit')]
    operations = [migrations.RunPython(seed_units, migrations.RunPython.noop)]
