"""Disposable, file-backed SQLite concurrency checks; never loads .env or live DBs."""
import os
from pathlib import Path
import socket
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4


def main():
    root=Path(__file__).resolve().parent
    isolated=types.ModuleType('catalogue_concurrency_settings')
    isolated.__file__=str(root/'config'/'settings.py')
    with tempfile.TemporaryDirectory(prefix='navapack-catalogue-test-', dir=root/'tmp') as directory:
        with patch.dict(os.environ,{'DJANGO_SECRET_KEY':'disposable-catalogue-tests-only','DJANGO_DEBUG':'true','DJANGO_ALLOWED_HOSTS':'testserver,localhost'},clear=True),patch('dotenv.load_dotenv',return_value=False):
            exec(compile(Path(isolated.__file__).read_text(),isolated.__file__,'exec'),isolated.__dict__)
        database=Path(directory)/'isolated.sqlite3'
        isolated.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(database),'OPTIONS':{'timeout':10}}}
        isolated.EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend'
        sys.modules[isolated.__name__]=isolated
        os.environ['DJANGO_SETTINGS_MODULE']=isolated.__name__
        def deny_network(*args,**kwargs):raise RuntimeError('Outbound network blocked.')
        with patch.object(socket.socket,'connect',deny_network),patch.object(socket.socket,'connect_ex',deny_network):
            import django
            django.setup()
            from django.conf import settings
            from django.core.management import call_command
            from django.db import connections
            from django.contrib.auth import get_user_model
            from rest_framework.test import APIClient
            from inventory.catalog_models import Consumable,ConsumableStockMovement
            from inventory.catalogue_service import balance
            assert Path(settings.DATABASES['default']['NAME']).parent==Path(directory)
            try:
                call_command('migrate',interactive=False,verbosity=0)
                actor=get_user_model().objects.create_superuser(username='isolated-catalogue-owner',password=None)
                def concurrent(bodies,path):
                    barrier=Barrier(len(bodies))
                    def worker(body):
                        try:
                            user=get_user_model().objects.get(pk=actor.pk)
                            client=APIClient();client.force_authenticate(user)
                            barrier.wait(timeout=20)
                            response=client.post(path,body,format='json')
                            return response.status_code,response.data
                        finally:connections.close_all()
                    with ThreadPoolExecutor(max_workers=len(bodies)) as pool:return list(pool.map(worker,bodies))
                create=lambda:{'name':'Concurrent pantry','category':'Pantry & Refreshments','subcategory':'Pantry & Refreshments','unit':'Each','creation_request_id':str(uuid4()),'opening_stock':'10'}
                responses=concurrent([create() for _ in range(8)],'/api/consumables/')
                assert all(status==201 for status,_ in responses),responses
                assert len({row['code'] for _,row in responses})==8
                retry=create();replays=concurrent([retry,retry],'/api/consumables/')
                assert all(status==201 for status,_ in replays),replays
                assert len({row['id'] for _,row in replays})==1
                item=replays[0][1];path=f"/api/consumables/{item['id']}/movements/"
                issue=lambda:{'request_id':str(uuid4()),'movement_type':'OUT','quantity':'6','unit':'Each','department':'Production','issued_to':'Operator','purpose':'Other'}
                issues=concurrent([issue(),issue()],path)
                assert sorted(status for status,_ in issues)==[201,400],issues
                assert balance(Consumable.objects.get(pk=item['id']))['current_stock']==4
                receipt={'request_id':str(uuid4()),'movement_type':'IN','quantity':'2','unit':'Each','department':'Stores','supplier':'Supplier','storage_location':'Store'}
                movements=concurrent([receipt,receipt],path)
                assert sorted(status for status,_ in movements)==[200,201],movements
                assert len({r['id'] for _,r in movements})==1
                assert balance(Consumable.objects.get(pk=item['id']))['current_stock']==6
                refs=list(ConsumableStockMovement.objects.values_list('transaction_reference',flat=True))
                assert len(refs)==len(set(refs))
                # Reopen the file through a fresh connection: no session-only persistence claims.
                connections.close_all()
                assert Consumable.objects.count()==9
                print('PASS: temporary file-backed SQLite; 8 concurrent unique codes, duplicate master/movement replays, concurrent overspending rejection, unique transaction references and database reload. Network blocked; shared DB untouched.')
                connections.close_all()
            finally:
                connections.close_all()



if __name__=='__main__':main()
