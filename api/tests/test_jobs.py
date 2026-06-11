"""JobManager retention cap (DoS hardening).

The manager keeps jobs in memory; without a cap a flood of requests would grow
the dict (and the full result + event log each job holds) without limit. Eviction
drops the oldest TERMINAL jobs first and never evicts a still-running job.
"""
from __future__ import annotations

from asterism_api.jobs import _DONE, _RUNNING, JobManager, _Job


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
