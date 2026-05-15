import json
import threading

import pytest
import redis
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from worker.celery_app import app as celery_app
from worker.config import settings
from worker.db import Base
from worker import db as worker_db
from worker.models import Job, JobStatus, SourceType
from worker.progress import CHANNEL
from shared.stages import Stage


@pytest.fixture(scope="session", autouse=True)
def _celery_eager():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield


_TEST_DB_URL = (
    "postgresql+psycopg://videoslicer:videoslicer@postgres:5432/videoslicer_test"
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(_TEST_DB_URL)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db_session(engine, monkeypatch):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    monkeypatch.setattr(worker_db, "SessionLocal", SessionLocal)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def fake_job(db_session):
    job = Job(
        source_type=SourceType.URL,
        source_url="https://example.com",
        status=JobStatus.QUEUED,
        current_stage=Stage.FETCH,
        created_by="t@e.st",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


@pytest.fixture
def capture_progress():
    events: list[dict] = []
    client = redis.from_url(settings.redis_url)
    pubsub = client.pubsub()
    pubsub.subscribe(CHANNEL)
    # drain the subscribe confirmation
    pubsub.get_message(timeout=1)

    stop = threading.Event()

    def _reader():
        while not stop.is_set():
            msg = pubsub.get_message(timeout=0.5, ignore_subscribe_messages=True)
            if msg and msg["type"] == "message":
                events.append(json.loads(msg["data"]))

    thread = threading.Thread(target=_reader, daemon=True)
    thread.start()
    yield events
    stop.set()
    thread.join(timeout=2)
    pubsub.close()
