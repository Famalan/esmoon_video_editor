import io
import uuid
from sqlalchemy import select, func
from app.models import Job, Source

URL = 'https://www.youtube.com/live/oDWCfXxrmpk?is=tracking'


def test_create_job_with_url_returns_201(client):
    response = client.post('/jobs',json={'source_type':'url','source_url':URL},headers={'X-User':'alice'})
    assert response.status_code == 201
    body = response.json()
    assert body['status'] == 'queued'
    assert body['source_url'] == 'https://www.youtube.com/watch?v=oDWCfXxrmpk'
    assert body['created_by'] == 'alice'
    assert body['analysis_version'] == 1
    assert body['policy_snapshot']['model'] == 'gpt-6-astra'
    assert body['policy_snapshot']['reasoning'] == 'medium'


def test_url_required_and_only_youtube(client):
    for data in ({'source_type':'url'}, {'source_type':'file'}, {'source_url':'http://localhost/private'}, {'source_url':'https://youtube.com/watch?v=abc'}):
        assert client.post('/jobs',json=data).status_code == 422


def test_corrupt_upload_creates_no_job(client,db_session):
    before = db_session.scalar(select(func.count()).select_from(Job))
    response = client.post('/jobs/upload',files={'file':('broken.mp4',io.BytesIO(b'FAKEMP4'),'video/mp4')})
    assert response.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Job)) == before


def test_file_job_reuses_source_without_url(client,db_session):
    source = Source(source_type='file',source_key='sha256:'+('a'*64),filename='test.mp4',title='Test',original_key='test/original')
    db_session.add(source);db_session.commit()
    response = client.post('/jobs',json={'source_id':str(source.id)})
    assert response.status_code == 201
    assert response.json()['source_url'] is None


def test_idempotent_click_and_rerun_share_source(client,db_session):
    headers={'Idempotency-Key':'double-click'}
    first = client.post('/jobs',json={'source_url':URL},headers=headers).json()
    second = client.post('/jobs',json={'source_url':URL},headers=headers).json()
    assert second['id'] == first['id']
    assert db_session.scalar(select(func.count()).select_from(Job)) == 1
    conflict = client.post('/jobs',json={'source_url':URL,'topic':'different'},headers=headers)
    assert conflict.status_code == 409
    rerun = client.post('/jobs/'+first['id']+'/rerun',json={},headers={'Idempotency-Key':'rerun'}).json()
    assert rerun['id'] != first['id']
    assert rerun['source_id'] == first['source_id']
    assert rerun['analysis_version'] == 2
    assert client.get('/jobs/'+first['id']).json()['analysis_version'] == 1


def test_get_and_list_jobs(client):
    job = client.post('/jobs',json={'source_url':URL}).json()
    assert client.get('/jobs/'+job['id']).json()['id'] == job['id']
    assert len(client.get('/jobs').json()['items']) == 1
    assert client.get('/jobs/'+str(uuid.uuid4())).status_code == 404


def test_oversize_rejected_before_body_read(client):
    response = client.post('/jobs/upload',headers={'Content-Length':'10002000000'},content=b'')
    assert response.status_code == 413
