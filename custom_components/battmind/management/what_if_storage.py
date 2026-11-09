"""Persistence for the last completed what-if simulation of an entry."""
from __future__ import annotations

import asyncio
from threading import Event
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from ..engine.planner.replay import WhatIfCancelled


class DashboardJob:
    """Entry-owned dashboard job; cancellation is safe from the worker thread."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.cancelled = Event()
        self.committing = False
        self.progress: dict[str, int | str] | None = None

    def report(self, progress: dict) -> None:
        """Store an actual coordinator progress callback for dashboard status polling."""
        self.progress = dict(progress)

    def check(self) -> None:
        if self.cancelled.is_set():
            raise WhatIfCancelled()


class WhatIfStorage:
    """Serialize access and keep entry-owned jobs alive across caller disconnects."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self.store = Store(
            hass, 1, f"{DOMAIN}.{entry_id}.what_if_last",
            private=True, atomic_writes=True, serialize_in_event_loop=False,
        )
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.closed = False
        self.dashboard_job: DashboardJob | None = None

    def track(self, task: asyncio.Task) -> None:
        self.tasks.add(task)

        def done(finished: asyncio.Task) -> None:
            self.tasks.discard(finished)
            if not finished.cancelled():
                # Consume the exception even when the caller disconnected.
                finished.exception()

        task.add_done_callback(done)

    async def load(self) -> dict[str, Any] | None:
        async with self.lock:
            return await self.store.async_load()

    async def save(self, record: dict[str, Any], job: DashboardJob | None = None) -> None:
        async with self.lock:
            if job is not None:
                job.check()
                # This is the commit point: subsequent cancellation is too late.
                job.committing = True
            # A write must finish before unload/removal, even if the job is cancelled.
            write = asyncio.create_task(self.store.async_save(record))
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                await write
                raise

    async def close(self) -> None:
        self.closed = True
        if self.dashboard_job is not None and not self.dashboard_job.committing:
            self.dashboard_job.cancelled.set()
        tasks = list(self.tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
