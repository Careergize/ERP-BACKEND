from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('accounting', '0009_document_sale_payment')]

    operations = [
        migrations.RenameField(
            model_name='document',
            old_name='sale_payment',
            new_name='trade_payment',
        ),
    ]
