from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounting', '0011_document_purchase_method')]

    operations = [
        migrations.AddField(
            model_name='document',
            name='receipt_mode',
            field=models.CharField(
                max_length=11, blank=True, default='',
                choices=[
                    ('advance', 'Advance'), ('against-ref', 'Against Ref'),
                    ('new-ref', 'New Ref'), ('on-account', 'On Account'),
                ],
            ),
        ),
    ]
