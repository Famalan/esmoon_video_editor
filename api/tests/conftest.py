import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.celery_client import celery
from app.db import Base, get_session
from app.main import app

_POSTGRES_URL = (
    "postgresql+psycopg://videoslicer:videoslicer@postgres:5432/videoslicer_test"
)


@pytest.fixture(scope="session")
def engine():
    eng = create_engine(_POSTGRES_URL)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)


@pytest.fixture
def db_session(engine):
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = SessionLocal()
    yield session
    session.rollback()
    session.close()


@pytest.fixture(autouse=True)
def _stub_celery(monkeypatch):
    monkeypatch.setattr(celery, "send_task", lambda *a, **k: None)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()
