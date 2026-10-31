from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):
    dependencies = [('accounting','0006_finance_masters')]
    operations = [
        migrations.AddField(model_name='currency',name='decimal_places',field=models.PositiveSmallIntegerField(default=2,validators=[django.core.validators.MaxValueValidator(6)])),
        migrations.AddField(model_name='currency',name='show_in_millions',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='currency',name='suffix_symbol',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='currency',name='space_between',field=models.BooleanField(default=False)),
        migrations.AddField(model_name='currency',name='decimal_word',field=models.CharField(blank=True,max_length=100)),
        migrations.AddField(model_name='currency',name='words_decimal_places',field=models.PositiveSmallIntegerField(default=2,validators=[django.core.validators.MaxValueValidator(6)])),
    ]
