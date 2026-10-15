import json,tempfile,unittest
from pathlib import Path
from engine import init_db,upsert_project,upsert_job
from merge_guard import approval_candidate,approve,assert_pr
SHA='a'*40
REPO='Joeltl25/ai-control-plane-v1'
def ticket():
    return dict(job_id='JOB-MERGE-TEST',repo=REPO,branch='ai/merge-test',exact_goal='Add fixture',
        files_allowed=['tests/fixture.txt'],files_not_allowed=['README.md'],evidence='test',
        command='bash test.sh',max_attempts=1,max_runtime=180,worker_choice='codex',
        stop_rules='Stop on failure',rollback_point='origin/main',slack_thread='1700000000.000001')
class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.con=init_db(str(Path(self.temp.name)/'db.sqlite'))
        upsert_project(self.con,dict(project_key='ai-workspace-control-plane',project_name='AI Workspace',repo=REPO))
        self.url='https://github.com/'+REPO+'/pull/123'
        self.payload={'coding_ticket':ticket(),'publisher_result':{'pr_url':self.url,'pr_number':123,'head_sha':SHA}}
        upsert_job(self.con,dict(job_id='JOB-MERGE-TEST',project_key='ai-workspace-control-plane',
           status='Needs Review',pr_url=self.url,branch='ai/merge-test',raw_json=json.dumps(self.payload)))
    def tearDown(self):self.con.close();self.temp.cleanup()
    def test_exact(self):self.assertEqual(approval_candidate(self.con,'JOB-MERGE-TEST')[0]['job_id'],'JOB-MERGE-TEST')
    def test_bare_unique(self):self.assertEqual(approval_candidate(self.con)[0]['job_id'],'JOB-MERGE-TEST')
    def test_wrong_job(self):
        with self.assertRaises(ValueError):approval_candidate(self.con,'JOB-NOPE')
    def test_wrong_user(self):
        with self.assertRaises(ValueError):approve(self.con,'JOB-MERGE-TEST','unauthorized')
    def test_approval_sha(self):
        record=approve(self.con,'JOB-MERGE-TEST','U0C2MN0DU49')
        row=self.con.execute('SELECT * FROM approvals WHERE approval_id=?',(record['approval_id'],)).fetchone()
        self.assertEqual(row['status'],'Approved')
        self.assertEqual(json.loads(row['approval_text'])['expected_head_sha'],SHA)
    def test_duplicate(self):
        approve(self.con,'JOB-MERGE-TEST','U0C2MN0DU49')
        with self.assertRaises(ValueError):approve(self.con,'JOB-MERGE-TEST','U0C2MN0DU49')
    def fakepr(self):return {'state':'open','merged':False,'draft':False,'head':{'repo':{'full_name':REPO},'ref':'ai/merge-test','sha':SHA},'base':{'ref':'main'}}
    def test_sha_change(self):
        p=self.fakepr();p['head']['sha']='b'*40
        with self.assertRaisesRegex(ValueError,'pr_commit_changed'):assert_pr(p,ticket(),self.payload['publisher_result'],SHA)
    def test_draft(self):
        p=self.fakepr();p['draft']=True
        with self.assertRaisesRegex(ValueError,'pr_not_open'):assert_pr(p,ticket(),self.payload['publisher_result'],SHA)
    def test_wrong_repo(self):
        p=self.fakepr();p['head']['repo']['full_name']='other/repo'
        with self.assertRaisesRegex(ValueError,'wrong_head_repo'):assert_pr(p,ticket(),self.payload['publisher_result'],SHA)
if __name__=='__main__':unittest.main()
