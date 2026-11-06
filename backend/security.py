import hmac
from fastapi.responses import JSONResponse
from backend.config import credentials, ORIGINS

def install_security(app, role='client'):
    @app.middleware('http')
    async def authorize(request, call_next):
        origin=request.headers.get('origin')
        if origin and origin not in ORIGINS:
            return JSONResponse({'detail':'不允許的 Origin'},status_code=403)
        host=request.headers.get('host','').split(':')[0]
        if host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'不允許的 Host'},status_code=403)
        if request.url.path.startswith('/api/') or (role=='service' and request.url.path!='/health'):
            supplied=request.headers.get('authorization','')
            if not hmac.compare_digest(supplied,'Bearer '+credentials()[role]):
                return JSONResponse({'detail':'請輸入本機存取權杖'},status_code=401)
        return await call_next(request)
