"""In-process async job runner for long LLM calls (Phase 4 M1a).

``propose_schema`` (and later ``refine``) take minutes and stream their output.
A synchronous request would time out and can't be reconnected to. This module
runs such a call as a background :class:`asyncio.Task`, records lifecycle
**events** on an async queue, and lets one or more SSE clients replay + follow
them via :meth:`JobManager.stream`.

Scope note (M1a): ``LLMClient.complete()`` returns the *full* proposal string
(the Anthropic streaming is reassembled inside step0), so we emit lifecycle
events — ``started`` / ``running`` / ``done`` (with the result) / ``error`` —
rather than token-by-token text. Token-level streaming would require extending
step0's ``LLMClient`` Protocol to yield deltas; deferred to a later milestone.

Security (D7): the user's API key is passed into :meth:`JobManager.start` only
to build the LLM client for that run. It is never stored on the Job, never
logged, and never written to disk.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# A job's terminal + interim states.
_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_ERROR = "error"
_TERMINAL = frozenset({_DONE, _ERROR})


@dataclass
class _Job:
    job_id: str
    status: str = _PENDING
    # Ordered event log; new subscribers replay this then follow live.
    events: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    error: str | None = None
    # Notifies waiting streamers that a new event was appended or status changed.
    _updated: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None

    def emit(self, event: str, **data: Any) -> None:
        self.events.append({"event": event, **data})
        self._updated.set()


class JobManager:
    """Owns the set of in-flight jobs and their event streams.

    One instance lives on ``app.state``. Jobs are kept in memory only — a
    process restart loses them (acceptable for M1a; durable queues are a later
    concern noted in the design doc D2).
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"job-{self._counter}"

    def start(self, work: Callable[[], Any]) -> str:
        """Schedule ``work`` (a blocking callable) on a worker thread.

        ``work`` runs the actual LLM call. Its return value becomes the job's
        ``result`` (emitted in the ``done`` event). Exceptions become ``error``.
        Returns the new ``job_id`` immediately.
        """
        job = _Job(job_id=self._next_id())
        self._jobs[job.job_id] = job
        job.task = asyncio.create_task(self._run(job, work))
        return job.job_id

    async def _run(self, job: _Job, work: Callable[[], Any]) -> None:
        job.status = _RUNNING
        job.emit("started", job_id=job.job_id)
        job.emit("running", message="LLM call in progress")
        try:
            result = await asyncio.to_thread(work)
            job.status = _DONE
            job.result = result
            job.emit("done", result=result)
        except Exception as exc:  # surface any failure to the SSE client
            job.status = _ERROR
            job.error = str(exc)
            job.emit("error", message=str(exc))
        finally:
            job._updated.set()

    def start_coro(self, make_coro: Callable[[Callable[..., None]], Awaitable[Any]]) -> str:
        """Schedule an async job that can report **interim progress**.

        ``make_coro(emit)`` returns the awaitable to run; ``emit(**data)`` appends
        a ``running`` event (a progress frame). Because the coroutine runs on the
        event loop (not a worker thread), calling ``emit`` from it — e.g. from a
        streaming-upload progress callback — is safe (no cross-thread ``Event``
        signalling). The awaitable's return value becomes the job ``result``.
        Used by the scalable ingest (materialize → chunked upload with progress).
        """
        job = _Job(job_id=self._next_id())
        self._jobs[job.job_id] = job

        def emit(**data: Any) -> None:
            job.emit("running", **data)

        job.task = asyncio.create_task(self._run_coro(job, make_coro, emit))
        return job.job_id

    async def _run_coro(
        self,
        job: _Job,
        make_coro: Callable[[Callable[..., None]], Awaitable[Any]],
        emit: Callable[..., None],
    ) -> None:
        job.status = _RUNNING
        job.emit("started", job_id=job.job_id)
        try:
            result = await make_coro(emit)
            job.status = _DONE
            job.result = result
            job.emit("done", result=result)
        except Exception as exc:  # surface any failure to the SSE client
            job.status = _ERROR
            job.error = str(exc)
            job.emit("error", message=str(exc))
        finally:
            job._updated.set()

    def get(self, job_id: str) -> _Job | None:
        return self._jobs.get(job_id)

    async def stream(self, job_id: str) -> AsyncIterator[str]:
        """Yield SSE-formatted lines for ``job_id``: replay then follow.

        Each yielded chunk is a complete SSE event (``event:`` + ``data:`` +
        blank line). Ends after the terminal (``done`` / ``error``) event.
        """
        job = self._jobs.get(job_id)
        if job is None:
            yield _sse({"event": "error", "message": f"unknown job_id: {job_id}"})
            return

        sent = 0
        while True:
            # Drain any events appended since we last looked.
            while sent < len(job.events):
                ev = job.events[sent]
                sent += 1
                yield _sse(ev)
                if ev["event"] in _TERMINAL:
                    return
            if job.status in _TERMINAL and sent >= len(job.events):
                return
            # Wait for the next emit(), then loop to drain it.
            job._updated.clear()
            try:
                await asyncio.wait_for(job._updated.wait(), timeout=15.0)
            except TimeoutError:
                # Heartbeat keeps proxies from closing an idle connection.
                yield ": keep-alive\n\n"


def _sse(event: dict[str, Any]) -> str:
    """Render one event dict as an SSE frame.

    ``event:`` is the event name; ``data:`` is the JSON payload (sans the name).
    """
    name = event.get("event", "message")
    payload = {k: v for k, v in event.items() if k != "event"}
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
