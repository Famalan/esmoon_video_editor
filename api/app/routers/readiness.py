import json
from fastapi import APIRouter
from sqlalchemy import text
from redis import Redis
from app.db import engine
from app.config import settings
from app.services import storage
from shared.policy import POLICY, MAX_UPLOAD_BYTES

router = APIRouter()


@router.get('/health/ready')
def readiness():
    services = {}
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
        services['database'] = {'ready':True,'detail':'База данных доступна'}
    except Exception:
        services['database'] = {'ready':False,'detail':'База данных недоступна'}
    worker = {}
    try:
        redis = Redis.from_url(settings.redis_url,socket_timeout=2,socket_connect_timeout=2)
        redis.ping()
        raw = redis.get('video-slicer:worker-ready')
        worker = json.loads(raw) if raw else {}
        services['redis'] = {'ready':True,'detail':'Очередь доступна'}
    except Exception:
        services['redis'] = {'ready':False,'detail':'Очередь недоступна'}
    try:
        storage._client().head_bucket(Bucket=settings.minio_bucket)
        services['storage'] = {'ready':True,'detail':'Хранилище доступно'}
    except Exception:
        services['storage'] = {'ready':False,'detail':'Хранилище недоступно'}
    correct_model = worker.get('model') == POLICY['model'] and worker.get('reasoning') == POLICY['reasoning']
    services['worker'] = {'ready':bool(worker.get('ready') and worker.get('ffmpeg') and correct_model),
        'detail':worker.get('detail') or 'Нет свежего сигнала обработчика. Запустите scripts/start-local.sh.'}
    services['codex'] = {'ready':bool(worker.get('codex') and correct_model),
        'detail':'GPT-6 Astra · medium' if worker.get('codex') and correct_model else 'Проверьте авторизацию Codex и модель обработчика.'}
    return {'ready':all(s['ready'] for s in services.values()),'services':services,
        'model':POLICY['model'],'reasoning':POLICY['reasoning'],'policy':POLICY,'max_upload_bytes':MAX_UPLOAD_BYTES}
