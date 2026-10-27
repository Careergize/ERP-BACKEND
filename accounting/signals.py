from django.db.models.signals import post_save
from django.dispatch import receiver
from inventory.models import RawMovement
from .integration import enqueue


@receiver(post_save, sender=RawMovement, dispatch_uid='accounting_raw_movement')
def raw_movement(sender, instance, created, raw=False, **kwargs):
    if not created or raw or not instance.created_by_id: return
    p={'kind':'stock-receipt' if instance.movement_type=='IN' else 'stock-issue',
       'number':'VAL-RAW-'+str(instance.pk),'date':str(instance.date),'item':'raw:'+str(instance.material_id),
       'unit':instance.material.unit,'quantity':str(instance.quantity),
       'contra':'2300' if instance.movement_type=='IN' else '1600'}
    enqueue('raw-movement:'+str(instance.pk),p,instance.created_by)
