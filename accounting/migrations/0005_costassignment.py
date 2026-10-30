import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('accounting', '0004_postingevent_cancelled')]
    operations = [migrations.CreateModel(
        name='CostAssignment',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('amount', models.DecimalField(decimal_places=2, max_digits=24)),
            ('document', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='cost_assignment', to='accounting.document')),
            ('source_line', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cost_assignments', to='accounting.journalline')),
        ],
        options={'constraints':[models.CheckConstraint(condition=models.Q(amount__gt=0), name='accounting_assignment_positive')]},
    )]
