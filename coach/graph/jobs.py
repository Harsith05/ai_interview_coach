"""Background answer analysis.

`start()` runs the analysis subgraph on a worker thread as soon as an answer is submitted.
It exposes two futures:
  * transcript - resolved as soon as the `transcribe` step finishes (~2-3 s). The interview
    graph uses it to pick a follow-up / the next difficulty without waiting for grading.
  * result     - the full analysis, also saved to Postgres (answer_results table).
`collect()` returns the result for the final report: from the running job, else from the
database (e.g. after a server restart), else by analyzing again.
"""
import json
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from coach.db import db_session
from coach.graph.analysis import analysis_graph, fallback_result
from coach.models import AnswerResult

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="answer-analysis")
_lock = threading.Lock()


@dataclass
class Job:
    transcript: Future = field(default_factory=Future)
    result: Future = field(default_factory=Future)


_jobs: dict[tuple[str, int], Job] = {}


def _save(interview_id: str, result: dict) -> None:
    grade = (result.get("grade") or {}).get("overall")
    values = dict(interview_id=interview_id, turn=result["turn"], question=result["q"],
                  is_follow_up=bool(result.get("is_follow_up")), difficulty=result.get("difficulty"),
                  grade=grade, result=result)
    stmt = insert(AnswerResult).values(**values)
    stmt = stmt.on_conflict_do_update(index_elements=["interview_id", "turn"],
                                      set_={k: stmt.excluded[k] for k in values if k not in ("interview_id", "turn")})
    with db_session() as db:
        db.execute(stmt)


def _to_builtin(o):
    if hasattr(o, "item"):  # numpy scalar
        return o.item()
    if hasattr(o, "tolist"):  # numpy array
        return o.tolist()
    return str(o)


def _cleanup(submission: dict) -> None:
    webm = submission.get("webm_path")
    if webm:
        Path(webm).unlink(missing_ok=True)
        Path(webm).with_suffix(".wav").unlink(missing_ok=True)


def _run(job: Job, interview_id: str, mode: str, item: dict, submission: dict) -> None:
    result = None
    try:
        state = {"interview_id": interview_id, "mode": mode, "item": item, "submission": submission}
        for update in analysis_graph.stream(state, stream_mode="updates"):
            if "transcribe" in update and not job.transcript.done():
                job.transcript.set_result(update["transcribe"].get("transcript", ""))
            if "assemble" in update:
                result = update["assemble"]["result"]
        if result is None:
            raise RuntimeError("analysis produced no result")
    except Exception as e:
        log.exception("Analysis failed for %s turn %d", interview_id[:8], item["turn"])
        result = fallback_result(item, submission, str(e))
    finally:
        if not job.transcript.done():
            job.transcript.set_result(submission.get("live_transcript", ""))
    # Plain JSON types only: numpy scalars would break the LangGraph checkpointer.
    result = json.loads(json.dumps(result, default=_to_builtin))
    try:
        _save(interview_id, result)
        _cleanup(submission)  # only delete the recording once the result is safely stored
    except Exception:
        log.exception("Could not save analysis for %s turn %d", interview_id[:8], item["turn"])
    job.result.set_result(result)


def start(interview_id: str, mode: str, item: dict, submission: dict) -> Job:
    job = Job()
    with _lock:
        _jobs[(interview_id, item["turn"])] = job
    _executor.submit(_run, job, interview_id, mode, item, submission)
    return job


def quick_transcript(job: Job, fallback: str, timeout: float = 10.0) -> str:
    try:
        return job.transcript.result(timeout=timeout) or fallback
    except Exception:
        return fallback


def collect(interview_id: str, mode: str, item: dict, submission: dict, timeout: float = 300) -> dict:
    with _lock:
        job = _jobs.pop((interview_id, item["turn"]), None)
    if job is not None:
        try:
            return job.result.result(timeout=timeout)
        except Exception:
            log.exception("Waiting for analysis of turn %d failed", item["turn"])
    with db_session() as db:
        row = db.query(AnswerResult).filter_by(interview_id=interview_id, turn=item["turn"]).first()
        if row is not None:
            return row.result
    # Not running and not saved (e.g. server restarted mid-analysis): analyze now.
    job = Job()
    _run(job, interview_id, mode, item, submission)
    return job.result.result()


def status(interview_id: str) -> dict[int, bool]:
    with _lock:
        return {t: j.result.done() for (iid, t), j in _jobs.items() if iid == interview_id}


def forget(interview_id: str) -> None:
    with _lock:
        for key in [k for k in _jobs if k[0] == interview_id]:
            _jobs.pop(key)
