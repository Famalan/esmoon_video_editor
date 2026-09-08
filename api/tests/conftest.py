import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

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
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, autoflush=False, join_transaction_mode="create_savepoint")
        yield session
        session.close()
        transaction.rollback()


@pytest.fixture(autouse=True)
def _stub_celery(monkeypatch):
    monkeypatch.setattr(celery, "send_task", lambda *a, **k: None)


@pytest.fixture
def client(db_session):
    def dependency():
        try:
            yield db_session
        except BaseException:
            db_session.rollback()
            raise
    app.dependency_overrides[get_session] = dependency
    yield TestClient(app)
    app.dependency_overrides.clear()
