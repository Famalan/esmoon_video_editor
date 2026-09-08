from io import BytesIO
import pytest
from starlette.requests import Request
from fastapi import HTTPException
from app.services import storage


class Body:
    def __init__(self,data):self.data=data;self.closed=False
    def iter_chunks(self,chunk_size):yield self.data
    def close(self):self.closed=True


class S3:
    def head_object(self,**kwargs):return {'ContentLength':100,'ContentType':'video/mp4','ETag':'abc'}
    def get_object(self,**kwargs):
        self.range=kwargs.get('Range');self.body=Body(b'x'*100);return {'Body':self.body}


@pytest.mark.parametrize('header,status,content_range,length',[
    (None,200,None,'100'),('bytes=10-19',206,'bytes 10-19/100','10'),
    ('bytes=-10',206,'bytes 90-99/100','10'),('bytes=95-',206,'bytes 95-99/100','5'),
])
def test_range_headers(monkeypatch,header,status,content_range,length):
    client=S3();monkeypatch.setattr(storage,'_client',lambda:client)
    request=Request({'type':'http','method':'HEAD','headers':[(b'range',header.encode())] if header else []})
    response=storage.media_response('video',request)
    assert response.status_code==status and response.headers['content-length']==length
    assert response.headers.get('content-range')==content_range
    assert response.headers['accept-ranges']=='bytes'


@pytest.mark.parametrize('header',['bytes=100-101','bytes=20-10','bytes=0-1,3-4','bytes=-0','invalid'])
def test_invalid_range_is_416(monkeypatch,header):
    monkeypatch.setattr(storage,'_client',lambda:S3())
    with pytest.raises(HTTPException) as error:
        storage.media_response('video',Request({'type':'http','method':'GET','headers':[(b'range',header.encode())]}))
    assert error.value.status_code==416
