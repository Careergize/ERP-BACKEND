from django.urls import path
from .dispatch_sales import DispatchSaleOptions
from .valuation_items import ValuationItems
from .detailed_profit_loss import DetailedProfitLoss
from .views import Bootstrap, Masters, Commands, Reports, ClosePeriod, PostingQueue, ImportMasters, PurchaseBills, CaptureSources, TaxMapping

urlpatterns = [path('detailed-profit-loss/',DetailedProfitLoss.as_view()),path('valuation-items/',ValuationItems.as_view()),path('dispatch-options/',DispatchSaleOptions.as_view()),path('setup/',Bootstrap.as_view()),path('masters/<str:kind>/',Masters.as_view()),
    path('commands/',Commands.as_view()),path('reports/',Reports.as_view()),path('close-period/',ClosePeriod.as_view()),
    path('posting-queue/',PostingQueue.as_view()),path('import-parties/',ImportMasters.as_view()),
    path('purchase-bills/',PurchaseBills.as_view()),path('capture-sources/',CaptureSources.as_view()),path('tax-mapping/',TaxMapping.as_view())]
