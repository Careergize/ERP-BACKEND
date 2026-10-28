from django.urls import path
from .sales_api import SalesAPI

urlpatterns=[path('ledger/customers/<int:customer_id>/',SalesAPI.as_view(),{'resource':'ledger'}),path('<str:resource>/',SalesAPI.as_view()),path('<str:resource>/<uuid:pk>/',SalesAPI.as_view()),path('<str:resource>/<uuid:pk>/<str:action>/',SalesAPI.as_view())]
