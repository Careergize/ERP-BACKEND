"""Disposable localhost browser fixture. Never loads .env or the business DB."""
import os
import sys
import types
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
module = types.ModuleType('persistence_ui_settings')
module.__file__ = str(root / 'config/settings.py')
with patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'disposable-ui-fixture-only-not-a-production-key',
    'DJANGO_DEBUG': 'true', 'DJANGO_ALLOWED_HOSTS': 'localhost,127.0.0.1'}, clear=True), patch('dotenv.load_dotenv', return_value=False):
    exec(compile(Path(module.__file__).read_text(), module.__file__, 'exec'), module.__dict__)
database = root / 'tmp' / ('persistence-ui-' + uuid4().hex + '.sqlite3')
module.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': str(database)}}
module.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
module.CORS_ALLOWED_ORIGINS = ['http://127.0.0.1:5175', 'http://localhost:5175']
module.CSRF_TRUSTED_ORIGINS = ['http://127.0.0.1:5175', 'http://localhost:5175']
sys.modules[module.__name__] = module
os.environ['DJANGO_SETTINGS_MODULE'] = module.__name__
import django
django.setup()
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from accounts.models import Role
from erp.models import Customer, FinishedStockItem, WorkflowOrder
call_command('migrate', verbosity=0, interactive=False)
User = get_user_model()
admin = User.objects.create_user('persistence-ui-admin', password='UiFixture-only-725!', role='admin',
    assigned_role=Role.objects.get(code='admin'), is_superuser=True, is_staff=True)
User.objects.create_user('persistence-ui-manager', password='UiFixture-only-725!', role='project_manager',
    assigned_role=Role.objects.get(code='project_manager'))
customer = Customer.objects.create(name='Persistence Test Buyer', code='UI-BUYER')
product = FinishedStockItem.objects.create(id='persistence-ui-fg', name='Persistence Test Bag', category='packing',
    unit='ctn', opening_stock=10, opening_date=timezone.localdate())
order = WorkflowOrder.objects.create(request_id=uuid4(), created_by=admin, data={})
order.data = {'id': f'wf-order-{order.pk}', 'orderNumber': 'UI-PERSIST-ORDER', 'customerId': f'db:{customer.pk}',
    'customerName': customer.name, 'customerType': 'B2B', 'marketingPersonName': admin.username,
    'currency': 'USD', 'taxRate': 18, 'date': timezone.localdate().isoformat(), 'status': 'Approved',
    'salesOrderApproval': {'approvedBy': admin.username, 'approvedById': admin.pk, 'approvedAt': timezone.now().isoformat()},
    'jobCardIds': [], 'items': [{'id': 'ui-item1', 'itemId': product.pk, 'itemName': product.name,
        'quantity': 10, 'unit': 'ctn', 'unitPrice': 2, 'totalPrice': 20, 'fulfillmentSource': 'stock'}]}
order.save()
print('DISPOSABLE UI DB:', database.name, flush=True)
call_command('runserver', '127.0.0.1:8137', use_reloader=False, verbosity=1)
