from django.urls import path
from .views import FinanceStatusView
from .access import FinanceAccessView

urlpatterns = [path('status/', FinanceStatusView.as_view()),
               path('v1/access/', FinanceAccessView.as_view())]
