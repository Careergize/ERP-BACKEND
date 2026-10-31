"""Physical item identity and unit choices for accounting valuation."""
from .api import AccountingAPIView
from rest_framework.response import Response
from .models import StockValue


def catalog_unit(item):
    if item.startswith('finished:'):
        from erp.models import FinishedStockItem
        return FinishedStockItem.objects.filter(pk=item[len('finished:'):]).values_list('unit',flat=True).first()
    if item.startswith('raw:'):
        from inventory.models import RawMaterial
        return RawMaterial.objects.filter(pk=item[len('raw:'):]).values_list('unit',flat=True).first()
    return None


class ValuationItems(AccountingAPIView):
    def get(self, request):
        from erp.models import FinishedStockItem
        from inventory.models import RawMaterial
        values={row.item:row for row in StockValue.objects.all()}
        choices={}
        for prefix,model in [('finished',FinishedStockItem),('raw',RawMaterial)]:
            for row in model.objects.order_by('name','pk'):
                key=f'{prefix}:{row.pk}'
                value=values.get(key)
                choices[key]={'id':key,'name':row.name,'unit':row.unit,'valuedUnit':value.unit if value else None,'lastDate':str(value.last_date) if value else None}
        for key,value in values.items():
            choices.setdefault(key,{'id':key,'name':key,'unit':value.unit,'valuedUnit':value.unit,'lastDate':str(value.last_date)})
        return Response(list(choices.values()))
