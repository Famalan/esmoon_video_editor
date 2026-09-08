from __future__ import annotations

import json
import uuid
import pytest

from shared.policy import policy_snapshot
from shared.stages import Stage
from worker.models import Job, JobStatus, Segment, SegmentRevision, Source, SourceType


def test_segment_analysis_publishes_one_fenced_revision_in_postgres(db_session, monkeypatch):
    from worker.tasks import segment as task_module

    source = Source(
        source_key=f"sha256:{uuid.uuid4().hex}", source_type="file", title="Smoke source",
        duration_sec=500.0, original_key="sources/smoke/original.mp4", status="ready",
        preview_key="sources/smoke/preview.mp4", preview_status="ready",
    )
    db_session.add(source)
    db_session.flush()
    attempt = uuid.uuid4()
    job = Job(
        source_type=SourceType.FILE, source_id=source.id, status=JobStatus.RUNNING,
        current_stage=Stage.SEGMENT, created_by="smoke@test", analysis_version=1,
        policy_snapshot=policy_snapshot(), transcript_snapshot={"key": "transcript.json", "version": "v1"},
        attempt_id=attempt, attempt_no=1, progress={},
    )
    db_session.add(job)
    db_session.commit()
    cues = [
        {"start": 100.0, "end": 140.0, "text": "Контекст и постановка вопроса."},
        {"start": 140.0, "end": 180.0, "text": "Аргументы и пример."},
        {"start": 180.0, "end": 200.0, "text": "Завершённый вывод."},
    ]
    monkeypatch.setattr(task_module.storage, "download_bytes", lambda _key: json.dumps(cues, ensure_ascii=False).encode())
    monkeypatch.setattr(task_module, "publish_progress", lambda *args, **kwargs: None)

    def fake_llm(**kwargs):
        if kwargs["schema_name"] == "video_segments":
            return {
                "chapters": [{"start": 0.0, "title": "Начало"}],
                "segments": [{
                    "start": 100.0, "end": 200.0, "title": "Цельный ответ", "summary": "Полный ответ",
                    "relevance": 90, "pain": 90, "hook": 80, "value": 95,
                    "decision": "publish", "rejection_reason": None,
                }],
            }
        return {"reviews": [{"candidate_id": 0, "ok": True, "reason": "Ответ завершён", "start": 100.0, "end": 200.0}]}

    monkeypatch.setattr(task_module.llm, "call_json", fake_llm)

    task_module.run.run(str(job.id), str(attempt))

    db_session.expire_all()
    segment = db_session.query(Segment).filter_by(job_id=job.id).one()
    revision = db_session.get(SegmentRevision, segment.current_revision_id)
    assert revision.number == 1
    assert revision.validation["narrative"]["ok"] is True
    assert revision.transcript_text == "Контекст и постановка вопроса. Аргументы и пример. Завершённый вывод."


def test_progress_rejects_a_superseded_attempt(db_session):
    from worker.progress import publish_progress
    from worker.runtime import StaleAttempt

    attempt = uuid.uuid4()
    job = Job(
        source_type=SourceType.FILE, status=JobStatus.RUNNING, current_stage=Stage.FETCH,
        created_by="fence@test", attempt_id=attempt, attempt_no=2, progress={},
    )
    db_session.add(job)
    db_session.commit()

    with pytest.raises(StaleAttempt):
        publish_progress(str(job.id), Stage.DONE, "done", attempt_id=str(uuid.uuid4()))

    db_session.expire_all()
    assert db_session.get(Job, job.id).current_stage == Stage.FETCH


def test_successful_thumbnail_retry_reconciles_ready_without_new_metadata(db_session):
    from worker.runtime import aggregate_job
    source=Source(source_key='sha256:'+uuid.uuid4().hex,source_type='file',duration_sec=500)
    db_session.add(source);db_session.flush()
    job=Job(source_id=source.id,source_type=SourceType.FILE,status=JobStatus.PARTIAL,created_by='retry',attempt_id=uuid.uuid4(),attempt_no=2)
    db_session.add(job);db_session.flush()
    seg=Segment(job_id=job.id,index=0,start_sec=100,end_sec=200)
    db_session.add(seg);db_session.flush()
    rev=SegmentRevision(segment_id=seg.id,number=1,start_sec=100,end_sec=200,status='failed',video_key='video.mp4',
        actual_duration_sec=100,error='Old thumbnail failure',stages={name:'succeeded' for name in ('render','verify','thumbnail','metadata')},
        validation={'technical':{'ok':True},'narrative':{'ok':True}})
    db_session.add(rev);db_session.flush();seg.current_revision_id=rev.id;db_session.commit()
    assert aggregate_job(str(job.id),str(job.attempt_id))==JobStatus.SUCCEEDED
    db_session.expire_all()
    assert db_session.get(SegmentRevision,rev.id).status=='ready'
    assert db_session.get(SegmentRevision,rev.id).error is None


def test_render_cannot_bypass_failed_narrative_on_retry(monkeypatch):
    from worker.tasks import cut,segment
    monkeypatch.setattr(segment,'ensure_revision_narrative',lambda *args:False)
    monkeypatch.setattr(cut.ffmpeg,'cut_segment',lambda **kwargs:pytest.fail('rejected narrative must never render'))
    with pytest.raises(ValueError,match='смысловую проверку'):
        cut.render_one.__wrapped__('segment','revision','attempt')


def test_heartbeat_restarts_immediately_between_stages(monkeypatch):
    from worker import progress
    started=[]
    class Thread:
        def __init__(self,**kwargs):self.target=kwargs['target']
        def start(self):started.append(self)
    monkeypatch.setattr(progress.threading,'Thread',Thread)
    key=('heartbeat-test','attempt')
    progress._ensure_job_heartbeat(*key)
    old=progress._heartbeats[key]
    progress._stop_job_heartbeat(*key)
    progress._ensure_job_heartbeat(*key)
    assert old.is_set() and progress._heartbeats[key] is not old
    assert len(started)==2
    progress._stop_job_heartbeat(*key)


def test_metadata_failure_keeps_mp4_and_retry_preserves_manual_title(db_session,monkeypatch):
    from worker.tasks import metadata
    from worker.runtime import aggregate_job
    from worker.services.llm import LLMError
    source=Source(source_key='sha256:'+uuid.uuid4().hex,source_type='file',duration_sec=500)
    db_session.add(source);db_session.flush()
    job=Job(source_id=source.id,source_type=SourceType.FILE,status=JobStatus.RUNNING,created_by='metadata-test',attempt_id=uuid.uuid4())
    db_session.add(job);db_session.flush()
    seg=Segment(job_id=job.id,index=0,start_sec=100,end_sec=200)
    db_session.add(seg);db_session.flush()
    full_text='Аргументы и примеры. '*60+'Последний важный вывод.'
    rev=SegmentRevision(segment_id=seg.id,number=1,start_sec=100,end_sec=200,status='processing',video_key='immutable-existing.mp4',
        actual_duration_sec=100,yt_title='Ручной заголовок',manual_fields=['yt_title'],metadata_needs_review=True,transcript_text=full_text,
        stages={'render':'succeeded','verify':'succeeded','thumbnail':'succeeded','metadata':'pending'},validation={'technical':{'ok':True},'narrative':{'ok':True}})
    db_session.add(rev);db_session.flush();seg.current_revision_id=rev.id;db_session.commit()
    ids=(str(seg.id),str(rev.id),str(rev.attempt_id))
    def unavailable(**kwargs):raise LLMError('Codex недоступен')
    monkeypatch.setattr(metadata.llm,'call_json',unavailable)
    with pytest.raises(LLMError):metadata.metadata_one(*ids)
    assert aggregate_job(str(job.id),str(job.attempt_id))==JobStatus.PARTIAL
    db_session.expire_all()
    assert db_session.get(SegmentRevision,rev.id).video_key=='immutable-existing.mp4'
    def restored(**kwargs):
        assert full_text in kwargs['user']
        return {'title':'Не должен затереть ручной','description':'Новое описание','tags':['тема']}
    monkeypatch.setattr(metadata.llm,'call_json',restored)
    metadata.metadata_one(*ids)
    assert aggregate_job(str(job.id),str(job.attempt_id))==JobStatus.SUCCEEDED
    db_session.expire_all()
    current=db_session.get(SegmentRevision,rev.id)
    assert current.yt_title=='Ручной заголовок' and current.yt_description=='Новое описание'
    assert current.video_key=='immutable-existing.mp4' and current.metadata_needs_review


def test_reanalysis_reuses_source_and_current_transcript_without_download(db_session,monkeypatch):
    from worker.tasks import fetch,transcribe
    source=Source(source_key='youtube:'+uuid.uuid4().hex,source_type='url',source_url='https://www.youtube.com/watch?v=oDWCfXxrmpk',title='Сохранённый источник',duration_sec=500,status='ready',
        original_key='cached/original.mp4',preview_key='cached/preview.mp4',preview_status='ready',subs_key='cached/subs.vtt',transcript_key='cached/transcript.json',transcript_version='vtt-clean-v2:known-hash')
    db_session.add(source);db_session.flush()
    job=Job(source_id=source.id,source_type=SourceType.URL,created_by='cache-test',attempt_id=uuid.uuid4(),policy_snapshot=policy_snapshot(),analysis_version=2)
    db_session.add(job);db_session.commit()
    monkeypatch.setattr(fetch.storage,'object_exists',lambda key:True)
    monkeypatch.setattr(fetch.storage,'download_file',lambda *args:pytest.fail('cached source must not download'))
    monkeypatch.setattr(fetch.storage,'download_bytes',lambda *args:pytest.fail('cached transcript must not regenerate'))
    monkeypatch.setattr(fetch,'_ytdlp_download',lambda *args:pytest.fail('cached URL must not redownload'))
    monkeypatch.setattr(fetch,'publish_progress',lambda *args,**kwargs:None)
    monkeypatch.setattr(transcribe,'publish_progress',lambda *args,**kwargs:None)
    fetch.run.run(str(job.id),str(job.attempt_id))
    transcribe.run.run(str(job.id),str(job.attempt_id))
    db_session.expire_all()
    assert db_session.get(Job,job.id).transcript_snapshot=={
        'key':'cached/transcript.json','version':'vtt-clean-v2:known-hash',
        'duration_limit_sec':500.0,
        'timing_note':'Таймкоды ограничены фактической длительностью исходника.',
    }
    source=db_session.get(Source,source.id);source.transcript_key='later/transcript.json';db_session.commit()
    transcribe.run.run(str(job.id),str(job.attempt_id))
    db_session.expire_all()
    assert db_session.get(Job,job.id).transcript_snapshot['key']=='cached/transcript.json'


def test_cached_transcript_without_source_duration_gets_conservative_bound(db_session, monkeypatch):
    from worker.tasks import transcribe

    source = Source(
        source_key='youtube:'+uuid.uuid4().hex, source_type='url', source_url='https://youtu.be/fytyk6VhlMI',
        transcript_key='cached/transcript.json', transcript_version='youtube-panel-v1', original_key=None,
    )
    db_session.add(source); db_session.flush()
    job = Job(
        source_id=source.id, source_type=SourceType.URL, created_by='cache-bound', attempt_id=uuid.uuid4(),
        policy_snapshot=policy_snapshot(), analysis_version=1, progress={},
    )
    db_session.add(job); db_session.commit()
    cues = [{"start": 10.0, "end": 20.0, "text": "Начало"}, {"start": 120.0, "end": 135.5, "text": "Конец"}]
    monkeypatch.setattr(transcribe.storage, 'object_exists', lambda key: True)
    monkeypatch.setattr(transcribe.storage, 'download_bytes', lambda key: json.dumps(cues).encode())
    monkeypatch.setattr(transcribe.storage, 'download_file', lambda *args: pytest.fail('media must not be downloaded'))
    monkeypatch.setattr(transcribe.ffmpeg, 'extract_audio', lambda **kwargs: pytest.fail('audio must not be extracted'))
    monkeypatch.setattr(transcribe, 'publish_progress', lambda *args, **kwargs: None)

    transcribe.run.run(str(job.id), str(job.attempt_id))
    db_session.expire_all()
    assert db_session.get(Job, job.id).transcript_snapshot['duration_limit_sec'] == 135.5


def test_persist_analysis_without_original_creates_analyzed_revisions(db_session, monkeypatch):
    from worker.tasks import segment as task_module

    source = Source(source_key='youtube:'+uuid.uuid4().hex, source_type='url', duration_sec=None, original_key=None)
    db_session.add(source); db_session.flush()
    attempt = uuid.uuid4()
    job = Job(
        source_id=source.id, source_type=SourceType.URL, status=JobStatus.RUNNING, current_stage=Stage.SEGMENT,
        created_by='trusted-persist', attempt_id=attempt, policy_snapshot=policy_snapshot(), progress={},
        transcript_snapshot={'key':'cached/transcript.json','version':'v1','duration_limit_sec':500.0},
    )
    db_session.add(job); db_session.commit()
    cues = [{"start":0.0,"end":100.0,"text":"Вопрос"},{"start":100.0,"end":200.0,"text":"Ответ и вывод"}]
    monkeypatch.setattr(task_module.storage, 'download_bytes', lambda key: json.dumps(cues).encode())
    candidate = {
        'start':0.0,'end':200.0,'title':'Ответ','summary':'Полный ответ','relevance':95,'pain':90,
        'hook':80,'value':90,'decision':'publish','rejection_reason':None,
        'narrative':{'ok':True,'reason':'Ответ завершён','start_sec':0.0,'end_sec':200.0},
    }

    task_module.persist_analysis(str(job.id), str(attempt), [{'start':0.0,'title':'Начало'}], [candidate])
    db_session.expire_all()
    segment_obj = db_session.query(Segment).filter_by(job_id=job.id).one()
    revision = db_session.get(SegmentRevision, segment_obj.current_revision_id)
    persisted_job = db_session.get(Job, job.id)
    assert revision.status == 'analyzed'
    assert revision.video_key is None and revision.actual_duration_sec is None
    assert set(revision.stages.values()) == {'not_requested'}
    assert persisted_job.progress['media_deferred'] is True


def test_pipeline_never_renders_when_original_is_deferred(db_session, monkeypatch):
    from worker.tasks import chain, cut, fetch, metadata, segment as task_segment, thumbnail, transcribe

    source = Source(source_key='youtube:'+uuid.uuid4().hex, source_type='url', original_key=None)
    db_session.add(source); db_session.flush()
    attempt = uuid.uuid4()
    job = Job(
        source_id=source.id, source_type=SourceType.URL, status=JobStatus.QUEUED, created_by='deferred-chain',
        attempt_id=attempt, policy_snapshot=policy_snapshot(), progress={'analysis_complete':True},
        transcript_snapshot={'key':'cached.json','version':'v1','duration_limit_sec':500.0},
    )
    db_session.add(job); db_session.flush()
    segment_obj = Segment(
        job_id=job.id,index=0,start_sec=0,end_sec=200,title='Ответ',summary='Полный ответ',
        relevance=90,pain=90,hook=90,value=90,decision='publish',selection='auto',
    )
    db_session.add(segment_obj); db_session.flush()
    revision = SegmentRevision(
        segment_id=segment_obj.id,number=1,start_sec=0,end_sec=200,attempt_id=uuid.uuid4(),status='queued',
        stages={'render':'pending','verify':'pending','thumbnail':'pending','metadata':'pending'},
        validation={'narrative':{'ok':True}},transcript_text='Полный ответ',
    )
    db_session.add(revision); db_session.flush(); segment_obj.current_revision_id=revision.id; db_session.commit()

    monkeypatch.setattr(fetch, 'run', lambda *args: None)
    monkeypatch.setattr(transcribe, 'run', lambda *args: None)
    monkeypatch.setattr(task_segment, 'run', lambda *args: None)
    monkeypatch.setattr(cut, 'run', lambda *args: pytest.fail('cut stage must not run without an original'))
    monkeypatch.setattr(thumbnail, 'run', lambda *args: None)
    monkeypatch.setattr(metadata, 'run', lambda *args: None)
    monkeypatch.setattr(chain, 'publish_progress', lambda *args, **kwargs: None)

    chain.build_pipeline.run(str(job.id), str(attempt))
    db_session.expire_all()
    persisted_job = db_session.get(Job, job.id)
    persisted_revision = db_session.get(SegmentRevision, revision.id)
    assert persisted_job.status == JobStatus.SUCCEEDED
    assert persisted_job.progress['media_deferred'] is True
    assert persisted_revision.status == 'analyzed'


def test_deferred_boundary_review_never_calls_media_tasks(db_session,monkeypatch):
    from worker.tasks import chain
    source=Source(source_key='youtube:'+uuid.uuid4().hex,source_type='url',duration_sec=None)
    db_session.add(source);db_session.flush()
    job=Job(source_id=source.id,source_type=SourceType.URL,created_by='boundary-test',attempt_id=uuid.uuid4(),status=JobStatus.RUNNING,
        transcript_snapshot={'key':'cached.json','duration_limit_sec':300},progress={'media_deferred':True,'analysis_complete':True})
    db_session.add(job);db_session.flush()
    seg=Segment(job_id=job.id,index=0,start_sec=10,end_sec=110)
    db_session.add(seg);db_session.flush()
    rev=SegmentRevision(segment_id=seg.id,number=1,start_sec=10,end_sec=110,status='queued',validation={'narrative':{'ok':True}})
    db_session.add(rev);db_session.flush();seg.current_revision_id=rev.id;db_session.commit()
    monkeypatch.setattr(chain.segment,'ensure_revision_narrative',lambda *args:True)
    monkeypatch.setattr(chain,'publish_progress',lambda *args,**kwargs:None)
    monkeypatch.setattr(chain.cut,'render_one',lambda *args:pytest.fail('No media rendering permitted'))
    monkeypatch.setattr(chain.thumbnail,'thumbnail_one',lambda *args:pytest.fail('No thumbnails permitted'))
    monkeypatch.setattr(chain.metadata,'metadata_one',lambda *args:pytest.fail('No media metadata permitted'))
    chain.render_revision.run(str(seg.id),str(rev.id),str(rev.attempt_id))
    db_session.expire_all()
    assert db_session.get(SegmentRevision,rev.id).status=='analyzed'
    assert db_session.get(SegmentRevision,rev.id).video_key is None
    assert db_session.get(Job,job.id).status==JobStatus.SUCCEEDED
