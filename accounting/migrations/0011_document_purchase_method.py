from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounting', '0010_document_trade_payment')]

    operations = [
        migrations.AddField(
            model_name='document',
            name='purchase_method',
            field=models.CharField(
                max_length=14, blank=True, default='',
                choices=[
                    ('cash', 'Cash'), ('bank-transfer', 'Bank transfer'),
                    ('cheque', 'Cheque'), ('mobile-money', 'Mobile money'),
                    ('other', 'Other'),
                ],
            ),
        ),
    ]
