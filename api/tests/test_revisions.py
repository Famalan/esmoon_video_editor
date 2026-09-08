import uuid
import pytest
from sqlalchemy import select
from app.models import Job, JobStatus, Segment, SegmentRevision, Source, SegmentStatus
from shared.policy import policy_snapshot, PolicyError, validate_interval, validate_actual_duration


def seed(db):
    source = Source(source_key='youtube:'+str(uuid.uuid4()),source_type='url',title='Проверка',duration_sec=4000)
    db.add(source);db.flush()
    job = Job(source_id=source.id,source_type='url',created_by='test',status=JobStatus.SUCCEEDED,
        policy_snapshot=policy_snapshot(),attempt_id=uuid.uuid4(),attempt_no=1,analysis_version=1)
    db.add(job);db.flush()
    segments=[]
    for i,(start,end) in enumerate(((100,300),(500,700))):
        seg=Segment(job_id=job.id,index=i,start_sec=start,end_sec=end,title=f'Тема {i}',status=SegmentStatus.METADATA_READY)
        db.add(seg);db.flush()
        rev=SegmentRevision(segment_id=seg.id,number=1,start_sec=start,end_sec=end,status='ready',
            video_key=f'{seg.id}.mp4',actual_duration_sec=end-start,yt_title='Автоматический заголовок',
            stages={k:'succeeded' for k in ('render','verify','thumbnail','metadata')},
            validation={'technical':{'ok':True},'narrative':{'ok':True}})
        db.add(rev);db.flush();seg.current_revision_id=rev.id
        segments.append(seg)
    db.commit()
    return job,segments


@pytest.mark.parametrize('duration,ok', [(89.999,False),(90,True),(1500,True),(1500.001,False),(89.421,False)])
def test_exact_policy_edges(duration,ok):
    if ok:
        validate_interval(0,duration,2000);validate_actual_duration(duration)
    else:
        with pytest.raises(PolicyError):validate_interval(0,duration,2000)
        with pytest.raises(PolicyError):validate_actual_duration(duration)


def test_patch_bounds_preserves_old_files_and_other_clip(client,db_session,monkeypatch):
    from app.routers import segments as routes
    queued=[]
    monkeypatch.setattr(routes,'enqueue_revision',lambda *args:queued.append(args))
    job,(one,two)=seed(db_session)
    old=one.current_revision_id;other=two.current_revision_id
    manual=client.patch(f'/segments/{one.id}',json={'expected_revision':1,'yt_title':'Мой заголовок'})
    assert manual.status_code==200
    changed=client.patch(f'/segments/{one.id}',json={'expected_revision':2,'start_sec':101,'end_sec':301})
    assert changed.status_code==200,changed.text
    body=changed.json()
    assert body['media_revision']==2 and body['revision']==3
    assert body['yt_title']=='Мой заголовок' and body['metadata_needs_review']
    assert body['video_download_url'] is None
    assert db_session.get(SegmentRevision,old).video_key
    assert db_session.get(Segment,two.id).current_revision_id==other
    assert len(queued)==1 and str(queued[0][0])==str(one.id)
    assert len(client.get(f'/segments/{one.id}/revisions').json()['items'])==2
    assert client.patch(f'/segments/{one.id}',json={'expected_revision':2,'start_sec':102}).status_code==409


def test_api_rejects_invalid_and_multiple_ranges(client,db_session):
    job,(one,two)=seed(db_session)
    for payload in (
        {'start_sec':-1}, {'start_sec':3900,'end_sec':4100}, {'end_sec':189.999},
        {'start_sec':0,'end_sec':1500.001}, {'end_sec':600}, {'ranges':[[100,200],[500,600]]},
        {'start_sec':True}, {'end_sec':None},
    ):
        result=client.patch(f'/segments/{one.id}',json={'expected_revision':1,**payload})
        assert result.status_code==422,(payload,result.text)
    assert client.get(f'/segments/{one.id}').json()['revision']==1


def test_restore_rechecks_overlap(client,db_session):
    job,(one,two)=seed(db_session)
    assert client.patch(f'/segments/{two.id}',json={'expected_revision':1,'selection':'exclude'}).status_code==200
    # A competing selected interval can now be changed while this candidate is excluded.
    one.end_sec=600;db_session.commit()
    restored=client.patch(f'/segments/{two.id}',json={'expected_revision':2,'selection':'include'})
    assert restored.status_code==422


def test_export_snapshot_stays_fixed_and_pending_rejected(client,db_session):
    job,(one,two)=seed(db_session)
    response=client.post(f'/jobs/{job.id}/exports',headers={'Idempotency-Key':'snapshot'})
    assert response.status_code==201,response.text
    from app.models import Export
    export=db_session.get(Export,response.json()['id'])
    old=export.snapshot['clips'][0]['revision_id']
    client.patch(f'/segments/{one.id}',json={'expected_revision':1,'selection':'exclude'})
    assert len(export.snapshot['clips'])==2 and export.snapshot['clips'][0]['revision_id']==old
    duplicate=client.post(f'/jobs/{job.id}/exports',headers={'Idempotency-Key':'snapshot'})
    assert duplicate.json()['id']==str(export.id)
    rev=db_session.get(SegmentRevision,two.current_revision_id)
    rev.status='failed';rev.error='Metadata failed';db_session.commit()
    blocked=client.post(f'/jobs/{job.id}/exports')
    assert blocked.status_code==409 and 'Metadata failed' in blocked.text
    # Correct video remains available even though metadata failed.
    assert client.get(f'/segments/{two.id}').json()['video_download_url']


def test_retry_preserves_good_revisions_and_fences_bad_attempt(client,db_session):
    job,(one,two)=seed(db_session)
    good=db_session.get(SegmentRevision,one.current_revision_id)
    bad=db_session.get(SegmentRevision,two.current_revision_id)
    old_attempt=bad.attempt_id;good_attempt=good.attempt_id
    bad.status='failed';bad.stages={**bad.stages,'metadata':'failed'}
    job.status=JobStatus.PARTIAL;db_session.commit()
    response=client.post(f'/jobs/{job.id}/retry')
    assert response.status_code==200
    assert bad.attempt_id!=old_attempt and good.attempt_id==good_attempt
    assert bad.video_key and bad.stages['render']=='succeeded'
    retry_attempt=bad.attempt_id
    client.post(f'/jobs/{job.id}/retry')
    assert bad.attempt_id==retry_attempt


def test_transcript_result_is_not_reported_as_verified_mp4(client,db_session):
    job,segments=seed(db_session)
    source=db_session.get(Source,job.source_id)
    source.duration_sec=None
    job.transcript_snapshot={'key':'text.json','version':'panel-v1','duration_limit_sec':4000}
    job.progress={'analysis_complete':True,'media_deferred':True}
    for seg in segments:
        rev=db_session.get(SegmentRevision,seg.current_revision_id)
        rev.video_key=None;rev.actual_duration_sec=None;rev.status='analyzed'
        rev.validation={'narrative':{'ok':True}}
    db_session.commit()
    out=client.get(f'/jobs/{job.id}').json()
    assert out['counts']['analyzed']==2 and out['counts']['ready']==0
    assert client.post(f'/jobs/{job.id}/exports').status_code==409
    report=client.get(f'/jobs/{job.id}/analysis/download')
    assert report.status_code==200 and report.json()['actual_mp4_duration_verified'] is False
    assert len(report.json()['segments'])==2
    changed=client.patch(f'/segments/{segments[0].id}',json={'expected_revision':1,'start_sec':101,'end_sec':301})
    assert changed.status_code==200,changed.text
    assert changed.json()['video_download_url'] is None
