import json, os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from engine import init_db,upsert_project,upsert_job,claim_next_job,complete_job
from coding_executor import prepare_ticket, execute, changed_files, ExecutionFailed
from github_publisher import GitHubPublisher,PublishError,token_status
from ticket_validator import validate


def ticket():
    return dict(job_id='JOB-SMOKE-123',repo='Joeltl25/ai-control-plane-v1',branch='ai/job-smoke-123',
        exact_goal='Add one fixture',files_allowed=['tests/smoke.txt'],files_not_allowed=['README.md'],
        evidence='Missing fixture',command='python3 -m pytest -q',max_attempts=1,max_runtime=180,
        worker_choice='codex',stop_rules='Stop if errors',rollback_point='origin/main',slack_thread='1700000000.000001')

class CodingPipelineTests(unittest.TestCase):
    def test_ticket_valid(self):self.assertTrue(validate(ticket())['ok'])
    def test_wrong_repo_rejected(self):
        x=ticket();x['repo']='other/repo'
        with self.assertRaises(ValueError):prepare_ticket(x)
    def test_disallowed_shell_rejected(self):
        x=ticket();x['command']='bash -c test'
        with self.assertRaises(ValueError):validate(x)
    def test_secret_path_rejected(self):
        x=ticket();x['files_allowed']=['.env']
        with self.assertRaises(ValueError):validate(x)
    def test_no_agent_preflight(self):self.assertFalse(execute(ticket(),agent=False)['agent_executed'])
    def test_publish_requires_agent(self):
        with self.assertRaises(ValueError):execute(ticket(),agent=False,publish=True)
    def test_lease_exact_job_and_attempts(self):
        with tempfile.TemporaryDirectory() as d:
            c=init_db(str(Path(d)/'test.sqlite'))
            upsert_project(c,dict(project_key='ai-workspace-control-plane',project_name='Test',repo='Joeltl25/ai-control-plane-v1'))
            upsert_job(c,dict(job_id='JOB-SMOKE-123',project_key='ai-workspace-control-plane',status='Ready',max_attempts=1,raw_json=json.dumps({'coding_ticket':ticket()})))
            self.assertFalse(claim_next_job(c,worker_key='test-worker',job_id='JOB-OTHER')['claimed'])
            claim=claim_next_job(c,worker_key='test-worker',job_id='JOB-SMOKE-123')
            self.assertTrue(claim['claimed'])
            self.assertEqual(c.execute("SELECT attempts FROM jobs WHERE job_id='JOB-SMOKE-123'").fetchone()['attempts'],1)
            self.assertFalse(claim_next_job(c,worker_key='test-worker',job_id='JOB-SMOKE-123')['claimed'])
            self.assertEqual(complete_job(c,'JOB-SMOKE-123','test-worker',status='Needs Review')['status'],'Needs Review')
    def test_missing_github_credentials(self):
        with patch('github_publisher.TOKEN_FILE',Path('/tmp/nonexistent-github-controlplane-test-20261015')):
            self.assertEqual(token_status()[1],'github_credential_missing')
    def test_github_publish_rejects_changed_main(self):
        publisher=object.__new__(GitHubPublisher)
        publisher.api=lambda *a,**kw:{'object':{'sha':'new-head'}}
        with self.assertRaisesRegex(PublishError,'main_changed_since_tests'):
            publisher.publish('ai/job-smoke-123','old-head',{'tests/smoke.txt':b'hi'},'JOB-SMOKE-123')
    def test_github_publisher_mock_success(self):
        pub=object.__new__(GitHubPublisher)
        calls=[]
        def fake_api(m,path,payload=None):
            calls.append((m,path))
            if path.endswith('/git/ref/heads/main'):return {'object':{'sha':'base'}}
            if '/git/ref/heads/ai/' in path:raise PublishError('github_http_404')
            if path.endswith('/git/commits/base'):return {'tree':{'sha':'oldtree'}}
            if path.endswith('/git/blobs'):return {'sha':'blobsha'}
            if path.endswith('/git/trees'):return {'sha':'treesha'}
            if path.endswith('/git/commits'):return {'sha':'commitsha'}
            if path.endswith('/pulls'):return {'html_url':'https://github.com/Joeltl25/ai-control-plane-v1/pull/888','number':888}
            return {}
        pub.api=fake_api
        result=pub.publish('ai/job-smoke-123','base',{'tests/smoke.txt':b'ok\n'},'JOB-SMOKE-123')
        self.assertEqual(result['pr_number'],888)
        self.assertEqual([m for m,p in calls].count('POST'),5)

if __name__=='__main__':unittest.main()
