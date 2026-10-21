from decimal import Decimal
from django.db import migrations


def forward(apps, schema_editor):
    Plan = apps.get_model('inventory', 'ProcurementPlan')
    Delivery = apps.get_model('inventory', 'ProcurementDelivery')
    Revision = apps.get_model('inventory', 'ProcurementPlanRevision')
    for plan in Plan.objects.select_related('material').all():
        for item in plan.deliveries:
            row = Delivery.objects.filter(pk=item['id']).first()
            if row and row.plan_id != plan.pk:
                raise ValueError('Saved delivery ID is shared by multiple material plans; original data was not removed.')
            if row:
                row.supplier = item['supplier']
                row.expected_date = item['date']
                row.quantity = Decimal(str(item['quantity']))
                row.updated_by_id = plan.updated_by_id
                row.save(update_fields=['supplier', 'expected_date', 'quantity', 'updated_by'])
            else:
                Delivery.objects.create(id=item['id'], plan_id=plan.pk, supplier=item['supplier'], expected_date=item['date'],
                    quantity=Decimal(str(item['quantity'])), created_by_id=plan.updated_by_id, updated_by_id=plan.updated_by_id)
        # Previous versions were not retained before this migration. Preserve the
        # full currently saved plan, including its existing delivery IDs.
        Revision.objects.get_or_create(plan_id=plan.pk, version=plan.version, defaults={'changed_by_id': plan.updated_by_id, 'snapshot': {
            'id': 'planner-' + plan.material_id, 'materialId': plan.material_id,
            'name': plan.material.name, 'unit': plan.material.unit, 'stock': None,
            'monthly': float(plan.monthly), 'days': plan.days, 'lead': plan.lead,
            'comment': plan.comment, 'deliveries': plan.deliveries, 'version': plan.version}})


def reverse(apps, schema_editor):
    Plan = apps.get_model('inventory', 'ProcurementPlan')
    Delivery = apps.get_model('inventory', 'ProcurementDelivery')
    for plan in Plan.objects.all():
        plan.deliveries = [
            {'id': str(d.pk), 'supplier': d.supplier, 'date': d.expected_date.isoformat(), 'quantity': float(d.quantity)}
            for d in Delivery.objects.filter(plan_id=plan.pk).order_by('expected_date', 'id')]
        plan.save(update_fields=['deliveries'])


class Migration(migrations.Migration):
    dependencies = [('inventory', '0004_procurementdelivery_procurementplanrevision_and_more')]
    operations = [migrations.RunPython(forward, reverse)]
