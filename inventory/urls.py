from django.urls import path
from .views import InventorySnapshot, RawMovementCreate, ProcurementPlanSave, ProcurementSnapshot

urlpatterns = [
    path('raw-inventory/', InventorySnapshot.as_view()),
    path('raw-inventory/movements/', RawMovementCreate.as_view()),
    path('procurement-plans/', ProcurementSnapshot.as_view()),
    path('procurement-plans/<str:material_id>/', ProcurementPlanSave.as_view()),
]
