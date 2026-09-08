"""Apply byte limits while ASGI receives a body, including chunked requests."""
from __future__ import annotations
import re
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

CHUNK_PATH=re.compile(r'^/api/v1/uploads/[^/]+/chunks/[^/]+/?$')

class BodyLimitExceeded(HTTPException):
    def __init__(self):
        super().__init__(status_code=413,detail='请求超过大小限制')

class StreamBodyLimitMiddleware:
    """Count bytes without concatenating them or pre-reading the whole request.

    This must wrap routing: FastAPI parses JSON and multipart bodies before
    resolving endpoint dependencies. Endpoint-only limits are consequently too
    late for untrusted requests without a Content-Length header.
    """
    def __init__(self,app,config):
        self.app=app
        self.config=config

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':
            await self.app(scope,receive,send)
            return
        path=scope.get('path','').rstrip('/')
        if self.config.mode=='cloud' and any(path==prefix or path.startswith(prefix+'/') for prefix in ('/api/v1/imports','/api/v1/sources')):
            await JSONResponse({'detail':'此功能仅可在本地客户端使用'},status_code=404)(scope,receive,send)
            return
        limit=1024**2
        if self.config.mode=='local' and path=='/api/v1/imports':
            # File bytes are independently capped by the import endpoint; this
            # additional allowance covers multipart boundaries and metadata.
            limit=self.config.max_package_bytes+1024**2
        elif CHUNK_PATH.fullmatch(path):
            limit=self.config.max_chunk_bytes
        for name,value in scope.get('headers',[]):
            if name.lower()==b'content-length':
                try:
                    declared=int(value)
                    if declared<0:raise ValueError
                except ValueError:
                    await JSONResponse({'detail':'无效请求长度'},status_code=400)(scope,receive,send)
                    return
                if declared>limit:
                    await JSONResponse({'detail':'请求超过大小限制'},status_code=413)(scope,receive,send)
                    return
        received=0
        async def limited_receive():
            nonlocal received
            message=await receive()
            if message['type']=='http.request':
                received+=len(message.get('body',b''))
                if received>limit:
                    # Raising through request.stream also lets multipart's
                    # error cleanup close any already-created temporary file.
                    raise BodyLimitExceeded()
            return message
        await self.app(scope,limited_receive,send)
