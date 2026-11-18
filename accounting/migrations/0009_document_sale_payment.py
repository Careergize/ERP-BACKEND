from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounting', '0008_account_pnl_section')]

    operations = [
        migrations.AddField(
            model_name='document',
            name='sale_payment',
            field=models.CharField(
                max_length=6, choices=[('cash', 'Cash'), ('credit', 'Credit')],
                blank=True, default='',
            ),
        ),
    ]
