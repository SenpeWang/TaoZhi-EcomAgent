"""ASGI request-body limit before multipart parsing or JSON decoding."""
from starlette.responses import JSONResponse
class RequestBodyLimit:
    def __init__(self,app,max_upload_bytes):
        self.app=app
        self.max_upload_bytes=max_upload_bytes
    async def __call__(self,scope,receive,send):
        if scope["type"]!="http" or scope["method"] not in ("POST","PUT","PATCH"):
            await self.app(scope,receive,send);return
        limit=self.max_upload_bytes+1024*1024 if scope["path"]=="/api/knowledge/upload" else 2*1024*1024
        messages=[];total=0
        while True:
            message=await receive()
            if message["type"]=="http.disconnect":return
            total+=len(message.get("body",b""))
            if total>limit:
                await JSONResponse({"detail":"请求体超过大小限制"},status_code=413)(scope,receive,send)
                return
            messages.append(message)
            if not message.get("more_body",False):break
        async def bounded_receive():
            if messages:return messages.pop(0)
            return await receive()
        await self.app(scope,bounded_receive,send)
