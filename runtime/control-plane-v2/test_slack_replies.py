import json,sqlite3,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import slack_replies as s
from engine import init_db

class SlackResponseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.c=init_db(str(Path(self.temp.name)/'test.sqlite'))
        self.real_url='https://hooks.slack.com/commands/example'
        self.init=patch.object(s,'init_db',return_value=self.c)
        self.key=patch.object(s,'secret_key',return_value=b'k'*32)
        self.init.start();self.key.start()
    def tearDown(self):
        self.key.stop();self.init.stop();self.c.close();self.temp.cleanup()
    def test_hosts_exact(self):
        self.assertTrue(s.allowed_url(self.real_url))
        for value in ('http://hooks.slack.com/commands/x','https://hooks.slack.com.attacker/commands/x',
                      'https://evil.test/commands/x','https://hooks.slack.com@evil.test/commands/x',
                      'https://hooks.slack.com/not-commands/x'):
            self.assertFalse(s.allowed_url(value),value)
    def test_encrypted_saved_without_plaintext(self):
        record=s.remember('JOB-SLACK-1',self.real_url)
        self.assertTrue(record['saved'])
        blob=self.c.execute("SELECT encrypted_url FROM slack_response_urls WHERE job_id='JOB-SLACK-1'").fetchone()[0]
        self.assertNotIn(self.real_url.encode(),blob)
        self.assertTrue(s.callback_ready('JOB-SLACK-1'))
    def test_refuses_other_job_decryption(self):
        s.remember('JOB-SLACK-1',self.real_url)
        self.c.execute("UPDATE slack_response_urls SET job_id='JOB-SLACK-2' WHERE job_id='JOB-SLACK-1'")
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with patch.object(s.urllib.request,'urlopen',return_value=Response()) as urlopen,patch.object(s,'post_slack_webhook',return_value={'sent':False}):
            self.assertFalse(s.notify('JOB-SLACK-2','hello')['sent'])
            urlopen.assert_not_called()
    def test_limit_and_expiry(self):
        s.remember('JOB-SLACK-1',self.real_url)
        self.c.execute("UPDATE slack_response_urls SET sent_count=? WHERE job_id='JOB-SLACK-1'",(s.MAX_SENDS,))
        self.assertFalse(s.callback_ready('JOB-SLACK-1'))
        self.c.execute("UPDATE slack_response_urls SET sent_count=0,created_at=? WHERE job_id='JOB-SLACK-1'",(int(time.time())-s.MAX_AGE-5,))
        self.assertFalse(s.callback_ready('JOB-SLACK-1'))
    def test_post_one_response(self):
        s.remember('JOB-SLACK-1',self.real_url)
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
        with patch.object(s.urllib.request,'urlopen',return_value=Response()) as request:
            result=s.notify('JOB-SLACK-1','PR ready')
            self.assertTrue(result['sent'])
            self.assertEqual(result['method'],'signed_slack_response_url')
            sent=json.loads(request.call_args[0][0].data)
            self.assertEqual(sent['text'],'PR ready')
            self.assertEqual(self.c.execute("SELECT sent_count FROM slack_response_urls WHERE job_id='JOB-SLACK-1'").fetchone()[0],1)

if __name__=='__main__':unittest.main()
