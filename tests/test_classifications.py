import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from data.classifications import ClassificationRepository, normalize
from scripts.services.classification_sync import sync


class ClassificationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.repo=ClassificationRepository(Path(self.temp.name)/'test.sqlite3')
 def tearDown(self):self.temp.cleanup()
 def row(self,ratio=0.25):
  return dict(SECURITY_CODE='600118',REPORT_DATE='2025-12-31',MAINOP_TYPE='2',ITEM_NAME='卫星设备',MAIN_BUSINESS_INCOME=100,MBI_RATIO=ratio,MBR_RATIO=None,GROSS_RPOFIT_RATIO=0)
 def test_pct_null_zero_and_dimension(self):
  self.repo.save_segments('600118',[self.row()],'https://example.com')
  data=self.repo.read('600118');r=data['segments'][0]
  self.assertEqual(r['revenue_pct'],25);self.assertIsNone(r['profit_pct']);self.assertEqual(r['gross_margin_pct'],0)
  self.assertIsNone(data['concept_revenue_pct']);self.assertEqual(r['dimension'],'产品')
 def test_observed_history_and_source_separation(self):
  a=dict(id='a',name='商业航天',kind='数据商板块');b=dict(id='b',name='航天装备',kind='行业')
  self.repo.memberships('600118','eastmoney',[a],'url',stamp='2026-01-01')
  self.repo.memberships('600118','shenwan',[a],'url',stamp='2026-01-01')
  self.repo.memberships('600118','eastmoney',[b],'url',stamp='2026-02-01')
  self.assertEqual(len(self.repo.read('600118')['boards']),2)
  with self.repo.connect() as db:
   self.assertEqual(db.execute("SELECT observed_to FROM membership WHERE source='eastmoney' AND board_id='a'").fetchone()[0],'2026-02-01')
 def test_failed_sync_preserves_previous_data(self):
  self.repo.save_segments('600118',[self.row()],'url')
  with patch('scripts.services.classification_sync.fetch',side_effect=ValueError('bad response')):
   result=sync('600118',self.repo)
  self.assertEqual(len(result['segments']),1);self.assertEqual([s for s in result['sync'] if s['dataset']=='business'][0]['status'],'error')
 def test_mismatched_stock_is_rejected_atomically(self):
  self.repo.save_segments('600118',[self.row()],'url');r=self.row();r['SECURITY_CODE']='000001'
  with self.assertRaises(ValueError):self.repo.save_segments('600118',[r],'url')
  self.assertEqual(len(self.repo.read('600118')['segments']),1)
 def test_repeat_sync_no_duplicate_and_old_reports_remain(self):
  r=self.row();self.repo.save_segments('600118',[r],'url');self.repo.save_segments('600118',[r],'url')
  r=dict(r,REPORT_DATE='2026-06-30');self.repo.save_segments('600118',[r],'url')
  self.assertEqual(len(self.repo.read('600118')['segments']),2)
 def test_invalid_codes(self):
  with self.assertRaises(ValueError):normalize("600118' OR 1=1")
