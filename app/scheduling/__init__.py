"""
Scheduled reports package.

Public surface (imported by ``app.main`` and the schedule CRUD routes):

  * ``start_scheduler(app_state_module)`` / ``stop_scheduler()`` — lifespan
    hooks that bring an ``AsyncIOScheduler`` up/down.
  * ``sync_schedule_job(schedule)`` — upsert an APScheduler job for a row.
  * ``remove_schedule_job(schedule_id)`` — remove a job by id.
  * ``run_scheduled_report(schedule_id, triggered_by)`` — the worker
    callable; also exposed so the "Run now" button can invoke it directly.
  * ``cadence_to_cron(cadence, **kwargs)`` — convert preset cadences
    (daily/weekly/monthly) to cron expressions at save time.
  * ``resolve_filter_macros(filter_params, *, tz)`` — expand rolling-window
    macros like ``{{last_7_days}}`` into concrete ``start_date`` /
    ``end_date`` strings.

Worker imports are kept inside ``runner.py``/``service.py`` rather than
here so that test code and CRUD routes can import the public helpers
without pulling APScheduler into the import graph.
"""

from app.scheduling.cron import cadence_to_cron
from app.scheduling.macros import resolve_filter_macros

__all__ = [
    "cadence_to_cron",
    "resolve_filter_macros",
    # The scheduler/runner symbols are re-exported lazily via ``service``
    # and ``runner`` to avoid importing APScheduler until it's needed.
]
