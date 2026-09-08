from starlette.responses import JSONResponse
from shared.policy import MAX_UPLOAD_BYTES


class UploadTooLarge(Exception):
    pass


class UploadLimitMiddleware:
    """Reject oversize multipart requests before parsing/spooling unbounded bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/jobs/upload':
            return await self.app(scope,receive,send)
        # Multipart framing has a separate small allowance; file has the exact 10GB limit.
        limit = MAX_UPLOAD_BYTES+1024*1024
        headers = dict(scope.get('headers',[]))
        try:
            length = int(headers.get(b'content-length',b'0'))
        except ValueError:
            return await JSONResponse({'detail':'Некорректный Content-Length.'},status_code=400)(scope,receive,send)
        if length > limit:
            return await JSONResponse({'detail':'Максимальный размер файла — 10 ГБ.'},status_code=413)(scope,receive,send)
        received = 0
        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message['type'] == 'http.request':
                received += len(message.get('body',b''))
                if received > limit:
                    raise UploadTooLarge()
            return message
        try:
            await self.app(scope,bounded_receive,send)
        except UploadTooLarge:
            await JSONResponse({'detail':'Максимальный размер файла — 10 ГБ. Начните загрузку заново.'},status_code=413)(scope,receive,send)
