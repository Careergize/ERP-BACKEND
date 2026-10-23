from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView, TokenBlacklistView
from accounts.views import LoginView, MeView
from accounts.management_api import RoleViewSet, ManagedUserViewSet
from erp.views import CustomerViewSet, SupplierViewSet, ProductViewSet, SalesOrderViewSet, JobCardViewSet, UnitViewSet, health
from quotations.views import QuotationViewSet
from quotations.drafts import QuotationDraftViewSet
from erp.purchases import PurchaseOrderViewSet
from erp.finished_stock import FinishedStockSnapshot
from erp.grn import GoodsReceivedNoteViewSet
from erp import production_workflow as workflow
from erp.order_drafts import DraftAPI, DraftDetail, DraftHistory

router = DefaultRouter()
router.register('customers', CustomerViewSet)
router.register('units', UnitViewSet)
router.register('suppliers', SupplierViewSet)
router.register('products', ProductViewSet)
router.register('sales-orders', SalesOrderViewSet)
router.register('purchase-orders', PurchaseOrderViewSet)
router.register('goods-received-notes', GoodsReceivedNoteViewSet)
router.register('job-cards', JobCardViewSet)
router.register('quotations', QuotationViewSet)
router.register('quotation-drafts', QuotationDraftViewSet)
router.register('roles', RoleViewSet)
router.register('users', ManagedUserViewSet)

from erp.job_drafts import JobDraftAPI, JobDraftDetail, JobDraftHistory

urlpatterns = [
    path('api/production-workflow/job-drafts/', JobDraftAPI.as_view()),
    path('api/production-workflow/job-drafts/<int:pk>/', JobDraftDetail.as_view()),
    path('api/production-workflow/job-drafts/<int:pk>/history/', JobDraftHistory.as_view()),
    path('api/production-workflow/order-drafts/', DraftAPI.as_view()),
    path('api/production-workflow/order-drafts/<int:pk>/', DraftDetail.as_view()),
    path('api/production-workflow/order-drafts/<int:pk>/history/', DraftHistory.as_view()),
    path('api/production-workflow/processes/', workflow.Processes.as_view()),
    path('api/production-workflow/machines/', workflow.Machines.as_view()),
    path('api/production-workflow/models/', workflow.Models.as_view()),
    path('api/production-workflow/orders/', workflow.Orders.as_view()),
    path('api/production-workflow/order-options/', workflow.OrderOptions.as_view()),
    path('api/production-workflow/orders/<int:pk>/allocate/', workflow.Allocate.as_view()),
    path('api/production-workflow/orders/<int:pk>/approve/', workflow.ApproveOrder.as_view()),
    path('api/production-workflow/jobs/', workflow.Jobs.as_view()),
    path('api/production-workflow/jobs/<int:pk>/<str:action>/', workflow.JobAction.as_view()),
    path('api/finished-stock/', FinishedStockSnapshot.as_view()),
    path('admin/', admin.site.urls),
    path('api/health/', health),
    path('api/', include('inventory.urls')),
    path('api/auth/login/', LoginView.as_view()),
    path('api/auth/refresh/', TokenRefreshView.as_view()),
    path('api/auth/logout/', TokenBlacklistView.as_view()),
    path('api/auth/me/', MeView.as_view()),
    path('api/', include(router.urls)),
]
