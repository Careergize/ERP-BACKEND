"""Disposable file SQLite race checks; no operational settings, data or network."""
import os
import sys
import socket
import tempfile
import types
from pathlib import Path
from threading import Barrier
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4


def main():
    root=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(prefix='sales-test-',dir=root/'tmp') as directory:
        isolated=types.ModuleType('sales_isolated_settings');isolated.__file__=str(root/'config/settings.py')
        with patch.dict(os.environ,{'DJANGO_SECRET_KEY':'disposable-sales-only','DJANGO_DEBUG':'true','DJANGO_ALLOWED_HOSTS':'testserver'},clear=True),patch('dotenv.load_dotenv',return_value=False):
            exec(compile(Path(isolated.__file__).read_text(),isolated.__file__,'exec'),isolated.__dict__)
        isolated.DATABASES={'default':{'ENGINE':'django.db.backends.sqlite3','NAME':str(Path(directory)/'isolated.sqlite3'),'OPTIONS':{'timeout':10}}}
        isolated.EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend';sys.modules[isolated.__name__]=isolated;os.environ['DJANGO_SETTINGS_MODULE']=isolated.__name__
        def denied(*a,**k):raise RuntimeError('Outbound sockets blocked.')
        with patch.object(socket.socket,'connect',denied),patch.object(socket.socket,'connect_ex',denied):
            import django
            django.setup()
            from django.core.management import call_command
            from django.db import connections
            from erp.test_sales import SalesTests
            from erp.sales_models import SalesReturn,SalesCreditNote
            from erp.models import EntryRevision
            call_command('migrate',verbosity=0,interactive=False)
            fixture=SalesTests();fixture.setUp()
            def parallel(users,bodies,path):
                barrier=Barrier(len(users))
                def worker(pair):
                    user,body=pair
                    try:
                        c=fixture.client_for(user);barrier.wait(timeout=15);r=c.post('/api/sales/'+path,body,format='json');return r.status_code,r.data
                    finally:connections.close_all()
                with ThreadPoolExecutor(max_workers=len(users)) as pool:return list(pool.map(worker,zip(users,bodies)))
            try:
                a,_=fixture.create('6');b,_=fixture.create('6');assert a.status_code==201 and b.status_code==201
                body=lambda r:{'commandId':str(uuid4()),'expectedVersion':r['version'],'status':'Submitted','remarks':'Reserve safely'}
                # Compete on the same physical invoice line through different return rows.
                barrier=Barrier(2)
                def reserve(row):
                    try:
                        c=fixture.client_for(fixture.writer);barrier.wait(timeout=15);r=c.post(f"/api/sales/returns/{row['id']}/transition/",body(row),format='json');return r.status_code,r.data
                    finally:connections.close_all()
                with ThreadPoolExecutor(max_workers=2) as pool:raced=list(pool.map(reserve,[a.data,b.data]))
                assert sorted(s for s,_ in raced)==[200,400],raced
                assert sum(r.quantity for r in SalesReturn.objects.filter(status='Submitted'))==6
                pending=next(r for status,r in raced if status==200)
                inspected=fixture.inspect(pending,accepted='6');assert inspected.status_code==200,inspected.data
                pending,_=fixture.change(inspected.data,'Pending Approval',fixture.checker);assert pending.status_code==200,pending.data
                approve={'commandId':str(uuid4()),'expectedVersion':pending.data['version'],'status':'Approved','remarks':'Independent review'}
                replays=parallel([fixture.checker]*2,[approve,approve],f"returns/{pending.data['id']}/transition/");assert [s for s,_ in replays]==[200,200],replays
                credit={'commandId':str(uuid4()),'returnId':pending.data['id'],'date':pending.data['date'],'reason':'Return','remarks':'Financial evidence'}
                receipts=parallel([fixture.writer]*2,[credit,credit],'credit-notes/');assert [s for s,_ in receipts]==[201,201],receipts
                assert len({r['id'] for _,r in receipts})==1 and SalesCreditNote.objects.count()==1
                row=receipts[0][1]
                for status,user in [('Submitted',fixture.writer),('Pending Approval',fixture.checker),('Approved',fixture.checker)]:
                    response,_=fixture.change(row,status,user);assert response.status_code==200,response.data;row=response.data
                issue={'commandId':str(uuid4()),'expectedVersion':row['version'],'status':'Issued','remarks':'Issue operational credit'}
                issued=parallel([fixture.checker]*2,[issue,issue],f"credit-notes/{row['id']}/transition/");assert [s for s,_ in issued]==[200,200],issued
                assert SalesCreditNote.objects.get().status=='Issued'
                assert EntryRevision.objects.filter(command_id=issue['commandId']).count()==1
                assert fixture.request(fixture.checker,'get','dashboard/').data['currencies']['USD']['net']=='40.00'
                print('PASS: file-backed isolated SQLite + real JWT/separate connections: competing pending reservations, same-command approval replay, duplicate credit creation and concurrent issuance, single immutable receipts and exact net sales. No operational DB or stock/GL writes.')
            finally:connections.close_all()


if __name__=='__main__':main()
