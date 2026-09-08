import io
import tempfile
from fastapi import UploadFile, HTTPException
import pytest
from app.services.ingestion import probe_upload, upload_source
from shared.policy import MAX_UPLOAD_BYTES


def test_real_sparse_oversize_file_is_rejected_without_probe():
    with tempfile.TemporaryFile() as stream:
        stream.truncate(MAX_UPLOAD_BYTES+1)
        with pytest.raises(HTTPException) as error:
            probe_upload(UploadFile(filename='too-big.mp4',file=stream))
        assert error.value.status_code==413


def test_interruption_aborts_multipart_and_never_creates_job(monkeypatch,db_session):
    from app.services import ingestion
    class Interrupted:
        def read(self,amount):raise ConnectionError('upload lost')
    class Client:
        aborted=False
        def create_multipart_upload(self,**kwargs):return {'UploadId':'attempt'}
        def abort_multipart_upload(self,**kwargs):self.aborted=True
    client=Client()
    monkeypatch.setattr(ingestion,'probe_upload',lambda file:(100,120))
    monkeypatch.setattr(ingestion.storage,'ensure_bucket',lambda:None)
    monkeypatch.setattr(ingestion.storage,'_client',lambda:client)
    with pytest.raises(ConnectionError):
        upload_source(db_session,UploadFile(filename='video.mp4',file=Interrupted()))
    assert client.aborted
