import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import config

logger = logging.getLogger(__name__)


def _parse_schedule(schedule_str: str) -> list[tuple[int, int]]:
    times = []
    for entry in schedule_str.split(","):
        entry = entry.strip()
        parts = entry.split(":")
        if len(parts) == 2:
            times.append((int(parts[0]), int(parts[1])))
    return times


def create_scheduler(pipeline_fn) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=config.TIMEZONE)
    times = _parse_schedule(config.FETCH_SCHEDULE)

    for hour, minute in times:
        scheduler.add_job(
            pipeline_fn,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=config.TIMEZONE),
            id=f"pipeline_{hour:02d}{minute:02d}",
            replace_existing=True,
        )
        logger.info("Scheduled pipeline at %02d:%02d %s", hour, minute, config.TIMEZONE)

    return scheduler
