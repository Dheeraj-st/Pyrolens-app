"""Celery tasks for scheduled ingestion and non-blocking intelligence work."""
import asyncio

from celery import Celery

from .config import get_settings
from .providers import FirmsNotConfiguredError, firms_provider
from .repository import repository

settings = get_settings()
celery_app = Celery("pyrolens", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    task_always_eager=settings.celery_task_always_eager,
    beat_schedule={"refresh-firms-feed": {"task": "pyrolens.ingest_firms", "schedule": settings.firms_refresh_seconds}},
)


@celery_app.task(bind=True, name="pyrolens.ingest_firms")
def ingest_firms(self) -> dict:
    """Fetch FIRMS events through the provider adapter and persist new records."""
    task_id = getattr(self.request, "id", None)
    if task_id:
        repository.update_job(task_id, status="running")
    try:
        events = asyncio.run(firms_provider.fetch({"source": "firms"}))
        written = repository.save_many(events)
        repository.record_ingestion("firms", len(events), written, status="completed")
        res = {"records_seen": len(events), "records_written": written}
        if task_id:
            repository.update_job(task_id, status="completed", result=res)
        return res
    except FirmsNotConfiguredError as exc:
        repository.record_ingestion("firms", 0, 0, status="failed", error=str(exc))
        if task_id:
            repository.update_job(task_id, status="failed", error=str(exc))
        raise
    except Exception as exc:
        repository.record_ingestion("firms", 0, 0, status="failed", error=str(exc))
        if task_id:
            repository.update_job(task_id, status="failed", error=str(exc))
        raise


@celery_app.task(name="pyrolens.dispatch_alert")
def dispatch_alert_task(alert_id: str, event_id: str, channels: list[str], recipients: list[str], message: str | None = None) -> dict:
    """Background task to dispatch SMS/Email notifications for an issued alert."""
    from .alerts import send_alert
    event = repository.get(event_id)
    if not event:
        return {"alert_id": alert_id, "status": "event_not_found"}
    deliveries = asyncio.run(send_alert(event, channels, recipients, message))
    return {"alert_id": alert_id, "deliveries": deliveries}
