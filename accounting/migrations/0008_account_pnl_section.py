from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounting','0007_currency_display_fields')]
    operations = [migrations.AddField(model_name='account',name='pnl_section',field=models.CharField(blank=True,default='',max_length=24,choices=[('', 'Unclassified'), ('sales','Sales'), ('cost_of_sales','Cost of goods sold'), ('direct_expense','Direct expenses'), ('indirect_expense','Indirect expenses'), ('other_income','Indirect income')]))]
