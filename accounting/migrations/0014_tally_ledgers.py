from django.db import migrations

from accounting.tally_ledgers import seed_tally_ledgers


def seed_existing_book(apps, schema_editor):
    alias = schema_editor.connection.alias
    if apps.get_model('accounting', 'Book').objects.using(alias).exists():
        seed_tally_ledgers(
            apps.get_model('accounting', 'Account'),
            apps.get_model('accounting', 'AccountGroup'),
            apps.get_model('accounting', 'Party'),
            alias,
        )


class Migration(migrations.Migration):
    dependencies = [('accounting', '0013_reference_finance_groups')]
    operations = [migrations.RunPython(seed_existing_book, migrations.RunPython.noop)]
