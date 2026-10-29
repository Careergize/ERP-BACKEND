from uuid import uuid4
from io import BytesIO
from zipfile import ZipFile
from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase
from accounts.models import Role
from .models import EntryRevision

class LabourJobTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user=get_user_model().objects.create_user('labour',role='recycling_operator',assigned_role=Role.objects.get(code='recycling_operator'))
        self.client.force_authenticate(self.user)
        self.row=dict(id='1',date='2026-06-13',customer='Crestank',material='PVC',color='be,rd,grn',direction='IN',quantity='4120',notes='',status='Active',correctionReason='')
    def save(self,rows,version=0,command=None):
        return self.client.put('/api/shared-entries/labour-job-entries/',dict(content=rows,expectedVersion=version,commandId=command or str(uuid4())),format='json')
    def report(self,**params):
        return self.client.get('/api/recycling/labour-jobs/report/',{'month':'2026-06',**params})
    def test_balances_and_periods(self):
        rows=[self.row,{**self.row,'id':'2','customer':'Poa Tank','material':'Tank','color':'bk','quantity':'3020'},{**self.row,'id':'3','customer':'Poa Tank','material':'Tank','color':'bk','quantity':'100','direction':'OUT'},{**self.row,'id':'4','date':'2026-05-31','quantity':'20.125'},{**self.row,'id':'5','date':'2026-07-01'},{**self.row,'id':'6','color':'white','quantity':'1','direction':'OUT'}]
        self.assertEqual(self.save(rows).status_code,200)
        d=self.report().data
        self.assertEqual((d['quantityIn'],d['quantityOut']),('7140','101'))
        self.assertEqual(len(d['entries']),4)
        self.assertEqual([g['closing'] for g in d['summary']],['4140.125','-1','2920'])
        for m in ['', '2026-6','2026-13','0000-01']: self.assertEqual(self.report(month=m).status_code,400)
    def test_retry_correction_void_audit(self):
        command=str(uuid4())
        self.assertEqual(self.save([self.row],command=command).status_code,200)
        self.assertEqual(self.save([self.row],command=command).status_code,200)
        self.assertEqual(self.save([self.row]).status_code,409)
        self.assertEqual(self.save([],1).status_code,400)
        changed={**self.row,'quantity':'4000'}
        self.assertEqual(self.save([changed],1).status_code,400)
        changed['correctionReason']='Scale correction'
        self.assertEqual(self.save([changed],1).status_code,200)
        void={**changed,'status':'Voided','correctionReason':'Duplicate'}
        self.assertEqual(self.save([void],2).status_code,200)
        self.assertEqual(self.report().data['quantityIn'],'0')
        self.assertEqual(len(self.report().data['entries']),1)
        self.assertEqual(self.save([changed],3).status_code,400)
        self.assertEqual(EntryRevision.objects.filter(entry_id='labour-job-entries').count(),3)
    def test_validation(self):
        for field,value in [('quantity','0'),('quantity','-1'),('quantity','1.0001'),('quantity','NaN'),('quantity','1000000001'),('date','2026-02-30'),('direction','SALE'),('customer',' '),('material',''),('color',''),('notes','\x00')]:
            with self.subTest(field=field): self.assertEqual(self.save([{**self.row,field:value}]).status_code,400)
        self.assertEqual(self.save([self.row,self.row]).status_code,400)
        self.assertEqual(self.save([{**self.row,'amount':'1'}]).status_code,400)
    def test_export(self):
        self.assertEqual(self.save([{**self.row,'notes':'=SUM(1,2)'}]).status_code,200)
        response=self.report(export='xlsx')
        self.assertEqual(response.status_code,200)
        with ZipFile(BytesIO(response.content)) as z:
            xml=z.read('xl/worksheets/sheet2.xml').decode()
            self.assertIn('=SUM(1,2)',xml)
            self.assertNotIn('<f>',xml)
            self.assertIn('<v>4120</v>',xml)
        self.assertEqual(self.report(export='pdf').status_code,400)
    def test_permissions(self):
        for role,read in [('project_manager',200),('qc_inspector',403)]:
            user=get_user_model().objects.create_user(role,role=role,assigned_role=Role.objects.get(code=role))
            self.client.force_authenticate(user)
            self.assertEqual(self.report().status_code,read)
            self.assertEqual(self.save([self.row]).status_code,403)
        self.user.is_active=False
        self.user.save()
        self.client.force_authenticate(self.user)
        self.assertEqual(self.report().status_code,403)
        self.client.force_authenticate(None)
        self.assertEqual(self.report().status_code,401)

    def test_saved_material_and_colour_options(self):
        url = '/api/shared-entries/labour-job-options/'
        rows = [dict(id='m1', kind='material', name='PVC'), dict(id='c1', kind='color', name='Blue')]
        def put(content, version=0):
            return self.client.put(url, dict(content=content, expectedVersion=version, commandId=str(uuid4())), format='json')
        self.assertEqual(put(rows).status_code, 200)
        data = self.client.get('/api/shared-entries/').data
        self.assertEqual(next(r['content'] for r in data['entries'] if r['key'] == 'labour-job-options'), rows)
        for invalid in [rows + [dict(id='m2', kind='material', name='pvc')], rows[:1], [{**rows[0], 'name':'Changed'}, rows[1]], rows + [dict(id='x', kind='other', name='X')]]:
            self.assertEqual(put(invalid, 1).status_code, 400)
        reader = get_user_model().objects.create_user('option-reader', role='project_manager', assigned_role=Role.objects.get(code='project_manager'))
        self.client.force_authenticate(reader)
        self.assertIn('labour-job-options', self.client.get('/api/shared-entries/').data['readable'])
        self.assertEqual(put(rows, 1).status_code, 403)
