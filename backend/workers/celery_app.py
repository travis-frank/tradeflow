from celery import Celery
from celery.schedules import crontab

from core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "tradeflow",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks"],
)

celery_app.conf.beat_schedule = {
    "warm-watchlist-cache": {
        "task": "workers.tasks.warm_watchlist_cache",
        "schedule": crontab(hour=14, minute=30, day_of_week="1-5"),
    },
    "refresh-fundamentals": {
        "task": "workers.tasks.refresh_fundamentals",
        "schedule": crontab(hour=0, minute=0, day_of_week=0),
    },
}
