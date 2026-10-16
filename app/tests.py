import unittest
from datetime import datetime,timedelta,timezone
from .modeling import feature
from .ingest import validate
class SafetyTests(unittest.TestCase):
 def test_features_ignore_unavailable_observations(self):
  issue=datetime(2026,9,1,tzinfo=timezone.utc)
  values={issue+timedelta(hours=h):float(h+1000) for h in range(-500,100)}
  expected=[feature(values,issue,h) for h in range(1,49)]
  for t in values:
   if t>issue-timedelta(hours=24): values[t]=99999999
  self.assertEqual(expected,[feature(values,issue,h) for h in range(1,49)])
 def test_missing_lag_blocks_forecast(self):
  issue=datetime(2026,9,1,tzinfo=timezone.utc)
  self.assertIsNone(feature({},issue,48))
 def test_invalid_demand_is_quarantined(self):
  row={'period':'2025-01-01T00','respondent':'CISO','type':'D','value':'NaN','value-units':'megawatthours'}
  self.assertIsNone(validate(row,'CISO'))
  row['value']='0';self.assertIsNone(validate(row,'CISO'))
  row['value']='100';self.assertIsNotNone(validate(row,'CISO'))
  self.assertIsNone(validate(row,'PJM'))
 def test_network_errors_do_not_expose_credentials(self):
  from unittest.mock import patch
  import requests
  from .ingest import download
  with patch('app.ingest.Path.read_text',return_value='FAKEPRIVATEKEY'), patch('app.ingest.requests.get',side_effect=requests.ConnectionError('https://example/?api_key=FAKEPRIVATEKEY')), patch('app.ingest.time.sleep'):
   with self.assertRaises(RuntimeError) as result:
    list(download('CISO',datetime(2025,1,1),datetime(2025,1,2)))
   self.assertNotIn('FAKEPRIVATEKEY',str(result.exception))
if __name__=='__main__': unittest.main()
