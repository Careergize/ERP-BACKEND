from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('accounts', '0003_seed_role_master')]
    operations = [migrations.CreateModel(
        name='FinanceEntitlement',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('frappe_user', models.CharField(max_length=140)),
            ('enabled', models.BooleanField(default=False)),
            ('expires_at', models.DateTimeField(blank=True, null=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
            ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='finance_entitlement', to=settings.AUTH_USER_MODEL)),
        ],
    )]
