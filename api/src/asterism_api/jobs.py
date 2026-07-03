"""In-process async job runner for long LLM calls (Phase 4 M1a).

``propose_schema`` (and later ``refine``) take minutes and stream their output.
A synchronous request would time out and can't be reconnected to. This module
runs such a call as a background :class:`asyncio.Task`, records lifecycle
**events** on an async queue, and lets one or more SSE clients replay + follow
them via :meth:`JobManager.stream`.

Scope note (M1a): ``LLMClient.complete()`` returns the *full* proposal string
(the Anthropic streaming is reassembled inside step0), so we emit lifecycle
events — ``started`` / ``running`` / ``done`` (with the result) / ``error`` /
``cancelled`` — rather than token-by-token text. A transient ``heartbeat``
event (never recorded in the job's event log) is additionally sent to idle SSE
subscribers so the client can tell "still working" from "connection dead".
Token-level streaming would require extending step0's ``LLMClient`` Protocol
to yield deltas; deferred to a later milestone.

Cancellation is **cooperative**: :meth:`JobManager.cancel` marks the job
cancelled immediately (the SSE stream ends with a ``cancelled`` event) and sets
a :class:`threading.Event` the work can poll via its ``should_cancel``
callable. A worker thread cannot be killed, so the late result / exception of
a cancelled (or timed-out) job is discarded silently.

Security (D7): the user's API key is passed into :meth:`JobManager.start` only
to build the LLM client for that run. It is never stored on the Job, never
logged, and never written to disk.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

# A job's terminal + interim states.
_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_ERROR = "error"
_CANCELLED = "cancelled"
_TERMINAL = frozenset({_DONE, _ERROR, _CANCELLED})


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
    # Cooperative cancel flag: a threading.Event because the work usually runs on
    # a worker thread (asyncio.to_thread) — ``cancel_event.is_set`` is the
    # ``should_cancel`` callable handed to the work.
    cancel_event: threading.Event = field(default_factory=threading.Event)
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

    def __init__(
        self,
        max_jobs: int = 200,
        *,
        heartbeat_seconds: float = 15.0,
        job_timeout_seconds: float | None = None,
    ) -> None:
        self._jobs: dict[str, _Job] = {}
        self._counter = 0
        # Bound retained jobs so a flood of requests cannot grow this dict (and the
        # full result + event log each job holds) without limit.
        self._max_jobs = max(1, max_jobs)
        # How long an SSE subscriber waits for the next event before receiving a
        # transient heartbeat frame (keeps proxies open + signals liveness).
        self._heartbeat_seconds = heartbeat_seconds
        # Wall-clock cap on ONE job's work. None / non-positive disables it. On
        # expiry the job errors out (and its cancel_event is set so a cooperative
        # worker thread stops at its next checkpoint) instead of hanging forever
        # — the "400-minute propose with no signal" failure mode.
        self._job_timeout_seconds = job_timeout_seconds

    def _next_id(self) -> str:
        self._counter += 1
        return f"job-{self._counter}"

    def _evict(self) -> None:
        """Drop the oldest TERMINAL jobs once over the cap (running jobs are kept)."""
        if len(self._jobs) <= self._max_jobs:
            return
        for jid in list(self._jobs):  # insertion order = oldest first
            if len(self._jobs) <= self._max_jobs:
                break
            if self._jobs[jid].status in _TERMINAL:
                del self._jobs[jid]

    def start(self, work: Callable[[Callable[[], bool]], Any]) -> str:
        """Schedule ``work`` (a blocking callable) on a worker thread.

        ``work(should_cancel)`` runs the actual LLM call; ``should_cancel()``
        (the job's ``cancel_event.is_set``) lets it stop cooperatively at its
        next checkpoint after :meth:`cancel`. Its return value becomes the job's
        ``result`` (emitted in the ``done`` event). Exceptions become ``error``.
        Returns the new ``job_id`` immediately.
        """
        job = _Job(job_id=self._next_id())
        self._jobs[job.job_id] = job
        self._evict()
        job.task = asyncio.create_task(self._run(job, work))
        return job.job_id

    async def _run(self, job: _Job, work: Callable[[Callable[[], bool]], Any]) -> None:
        job.status = _RUNNING
        job.emit("started", job_id=job.job_id)
        job.emit("running", message="LLM call in progress")
        await self._settle(job, asyncio.to_thread(work, job.cancel_event.is_set))

    def start_coro(
        self,
        make_coro: Callable[[Callable[..., None], Callable[[], bool]], Awaitable[Any]],
    ) -> str:
        """Schedule an async job that can report **interim progress**.

        ``make_coro(emit, should_cancel)`` returns the awaitable to run;
        ``emit(**data)`` appends a ``running`` event (a progress frame) and
        ``should_cancel()`` reports a pending :meth:`cancel` for the parts of
        the work the coroutine offloads to worker threads. Because the
        coroutine runs on the event loop (not a worker thread), calling
        ``emit`` from it — e.g. from a streaming-upload progress callback — is
        safe (no cross-thread ``Event`` signalling). The awaitable's return
        value becomes the job ``result``. Used by the scalable ingest
        (materialize → chunked upload with progress).
        """
        job = _Job(job_id=self._next_id())
        self._jobs[job.job_id] = job
        self._evict()

        def emit(**data: Any) -> None:
            job.emit("running", **data)

        job.task = asyncio.create_task(self._run_coro(job, make_coro, emit))
        return job.job_id

    async def _run_coro(
        self,
        job: _Job,
        make_coro: Callable[[Callable[..., None], Callable[[], bool]], Awaitable[Any]],
        emit: Callable[..., None],
    ) -> None:
        job.status = _RUNNING
        job.emit("started", job_id=job.job_id)
        await self._settle(job, make_coro(emit, job.cancel_event.is_set))

    async def _settle(self, job: _Job, work: Awaitable[Any]) -> None:
        """Await the job's work (with the optional wall-clock timeout) and record
        the outcome — UNLESS the job already reached a terminal state (a user
        :meth:`cancel`), in which case the late result / exception is discarded
        silently: the worker thread cannot be killed, only outlived."""
        timeout = self._job_timeout_seconds
        try:
            if timeout is not None and timeout > 0:
                result = await asyncio.wait_for(work, timeout=timeout)
            else:
                result = await work
        except TimeoutError:
            if job.status in _TERMINAL:
                return
            # Ask the (unkillable) worker to stop at its next cooperative
            # checkpoint, and fail the job now so the client stops waiting.
            job.cancel_event.set()
            job.status = _ERROR
            job.error = (
                f"job timed out after {int(timeout or 0)}s (ASTERISM_JOB_TIMEOUT_SECONDS); "
                "the LLM call may be stuck — cancel/retry with a smaller input, a "
                "faster model, or a lower max-tokens setting"
            )
            job.emit("error", message=job.error)
        except Exception as exc:  # surface any failure to the SSE client
            if job.status in _TERMINAL:
                return
            job.status = _ERROR
            job.error = str(exc) or type(exc).__name__
            job.emit("error", message=job.error)
        else:
            if job.status in _TERMINAL:
                return
            job.status = _DONE
            job.result = result
            job.emit("done", result=result)
        finally:
            job._updated.set()

    def cancel(self, job_id: str) -> bool:
        """Cancel one job (idempotent). Returns False only for an unknown id.

        Runs on the event loop (like every manager method), so no locking is
        needed: the job is marked terminal and its SSE stream ends with a
        ``cancelled`` event immediately; the cooperative ``cancel_event`` tells
        the work to stop, and :meth:`_settle` discards whatever it still
        returns or raises."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status in _TERMINAL:
            return True
        job.cancel_event.set()
        job.status = _CANCELLED
        job.emit("cancelled", message="cancelled by user")
        return True

    def get(self, job_id: str) -> _Job | None:
        return self._jobs.get(job_id)

    async def stream(self, job_id: str) -> AsyncIterator[str]:
        """Yield SSE-formatted lines for ``job_id``: replay then follow.

        Each yielded chunk is a complete SSE event (``event:`` + ``data:`` +
        blank line). Ends after the terminal (``done`` / ``error`` /
        ``cancelled``) event. An idle wait yields transient ``heartbeat``
        frames every ``heartbeat_seconds``.
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
                await asyncio.wait_for(job._updated.wait(), timeout=self._heartbeat_seconds)
            except TimeoutError:
                # Heartbeat: keeps proxies from closing an idle connection AND
                # gives the client a liveness signal ("still working" vs "dead
                # connection"). Transient — sent to THIS subscriber only, never
                # appended to job.events (a replay must not accumulate them).
                yield _sse({"event": "heartbeat", "status": job.status})


def _sse(event: dict[str, Any]) -> str:
    """Render one event dict as an SSE frame.

    ``event:`` is the event name; ``data:`` is the JSON payload (sans the name).
    """
    name = event.get("event", "message")
    payload = {k: v for k, v in event.items() if k != "event"}
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
