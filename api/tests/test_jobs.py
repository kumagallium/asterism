"""JobManager retention cap (DoS hardening) + cancel / timeout / heartbeat.

The manager keeps jobs in memory; without a cap a flood of requests would grow
the dict (and the full result + event log each job holds) without limit. Eviction
drops the oldest TERMINAL jobs first and never evicts a still-running job.

Cancellation is cooperative: ``cancel()`` marks the job terminal immediately and
the worker's late result / exception is DISCARDED (a thread cannot be killed).
The wall-clock timeout errors a stuck job out the same way, and ``stream()``
sends transient heartbeat frames so an idle SSE client can tell "still working"
from "connection dead". These run their own event loop via ``asyncio.run`` (the
endpoint-level behaviour is covered in test_main.py through TestClient).
"""
from __future__ import annotations

import asyncio
import threading

from asterism_api.jobs import _CANCELLED, _DONE, _ERROR, _RUNNING, JobManager, _Job


def test_evicts_oldest_terminal_jobs_over_cap() -> None:
    jm = JobManager(max_jobs=2)
    for i in range(5):
        job = _Job(job_id=f"job-{i}", status=_DONE)
        jm._jobs[job.job_id] = job
        jm._evict()
    assert len(jm._jobs) == 2
    # The survivors are the most-recent terminal jobs.
    assert set(jm._jobs) == {"job-3", "job-4"}


def test_never_evicts_a_running_job() -> None:
    jm = JobManager(max_jobs=1)
    jm._jobs["r"] = _Job(job_id="r", status=_RUNNING)
    for i in range(3):
        done = _Job(job_id=f"d{i}", status=_DONE)
        jm._jobs[done.job_id] = done
        jm._evict()
    # The running job survives even though it keeps us over the cap.
    assert "r" in jm._jobs


# ----------------------------------------------------------------------------
# cancel: immediate terminal state, late result discarded, idempotent
# ----------------------------------------------------------------------------


def test_cancel_marks_terminal_and_discards_late_result() -> None:
    async def main() -> None:
        jm = JobManager()
        started = threading.Event()
        release = threading.Event()

        def work(should_cancel):
            started.set()
            release.wait(timeout=5)
            return {"late": True}  # arrives AFTER the cancel — must be discarded

        job_id = jm.start(work)
        job = jm.get(job_id)
        assert job is not None
        # Wait (off-loop) until the worker thread is actually running.
        assert await asyncio.to_thread(started.wait, 5)

        assert jm.cancel(job_id) is True
        assert job.status == _CANCELLED
        assert job.cancel_event.is_set()

        # The stream ends on the cancelled event (terminal).
        frames = [f async for f in jm.stream(job_id)]
        assert any("event: cancelled" in f for f in frames)
        assert "cancelled by user" in frames[-1]

        # Let the worker finish late: the runner must NOT overwrite the
        # terminal status or append a done event.
        release.set()
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=5)
        assert job.status == _CANCELLED
        assert [e["event"] for e in job.events].count("done") == 0

        # Idempotent on an already-terminal job; unknown ids report False.
        assert jm.cancel(job_id) is True
        assert jm.cancel("nope") is False

    asyncio.run(main())


def test_cancelled_work_exception_is_discarded_silently() -> None:
    # A cancelled worker typically raises (LLMCancelledError) at its next
    # cooperative checkpoint — that late exception must not become an error event.
    async def main() -> None:
        jm = JobManager()
        started = threading.Event()
        release = threading.Event()

        def work(should_cancel):
            started.set()
            release.wait(timeout=5)
            assert should_cancel()  # the cancel_event reached the worker
            raise RuntimeError("cancelled mid-flight")

        job_id = jm.start(work)
        job = jm.get(job_id)
        assert job is not None
        assert await asyncio.to_thread(started.wait, 5)
        assert jm.cancel(job_id) is True
        release.set()
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=5)
        assert job.status == _CANCELLED
        names = [e["event"] for e in job.events]
        assert "error" not in names
        assert "done" not in names

    asyncio.run(main())


# ----------------------------------------------------------------------------
# wall-clock timeout
# ----------------------------------------------------------------------------


def test_job_timeout_errors_out_and_sets_cancel_event() -> None:
    async def main() -> None:
        jm = JobManager(job_timeout_seconds=0.05)
        release = threading.Event()

        def work(should_cancel):
            release.wait(timeout=5)
            return "too late"

        job_id = jm.start(work)
        job = jm.get(job_id)
        assert job is not None
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=5)
        assert job.status == _ERROR
        assert job.error is not None
        assert "timed out" in job.error
        assert "ASTERISM_JOB_TIMEOUT_SECONDS" in job.error
        # The cooperative flag tells the (unkillable) worker to stop.
        assert job.cancel_event.is_set()
        release.set()  # unblock the leaked worker thread

    asyncio.run(main())


def test_timeout_disabled_when_none() -> None:
    async def main() -> None:
        jm = JobManager(job_timeout_seconds=None)

        def work(should_cancel):
            return "ok"

        job_id = jm.start(work)
        job = jm.get(job_id)
        assert job is not None
        assert job.task is not None
        await asyncio.wait_for(job.task, timeout=5)
        assert job.status == _DONE
        assert job.result == "ok"

    asyncio.run(main())


# ----------------------------------------------------------------------------
# heartbeat: transient SSE frames while the job is quiet
# ----------------------------------------------------------------------------


def test_stream_yields_heartbeat_while_job_is_quiet() -> None:
    async def main() -> None:
        jm = JobManager(heartbeat_seconds=0.01)
        release = threading.Event()

        def work(should_cancel):
            release.wait(timeout=5)
            return "ok"

        job_id = jm.start(work)
        frames: list[str] = []
        async for frame in jm.stream(job_id):
            frames.append(frame)
            if "event: heartbeat" in frame:
                release.set()  # first heartbeat seen → let the job finish
        assert any("event: heartbeat" in f for f in frames)
        assert any("event: done" in f for f in frames)
        # Transient only: heartbeats are never recorded in the job's event log
        # (a replay must not accumulate them).
        job = jm.get(job_id)
        assert job is not None
        assert all(e["event"] != "heartbeat" for e in job.events)

    asyncio.run(main())
