from celery import Celery
from src.config import settings

celery_app = Celery(
    "arbitrage_tasks",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

@celery_app.task
def process_new_listing(url: str):
    # In a real scenario, this would be an async task wrapper or use synchronous SQLAlchemy
    pass
