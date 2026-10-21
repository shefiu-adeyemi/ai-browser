"""
One scheduled run, looked at for changes: compare what it read with the last time, keep what it read, say what happened.

Outcomes ("status"):
  baseline   the first time: what it read is kept to compare with next time (nothing to report yet)
  unchanged  the same as last time, after the user's noise settings: nothing to say
  changed    something is different: `summary` says what, `diff` has the details
  reset      the watch settings changed since last time, so the old snapshot can't be compared: a new baseline
  skipped    the run can't be compared (a page failed, or it read no table): nothing is kept, `reason` says why
"""

from typing import Optional

from aiba.scheduling import monitor
from aiba.scheduling.monitor import Watch
from aiba.storage.snapshots import SnapshotStore, subject_of


def observe(store: SnapshotStore, playbook_id: str, values: dict, run_id: str, outputs: list, steps: list,
            watch: Watch, now: Optional[float] = None, scope: str = "") -> dict:
    """`steps` are the run's step results as dicts (a failed step means the run is partial and is not compared)."""
    if any(isinstance(s, dict) and s.get("status") != "ok" for s in steps or []):
        return {"status": "skipped", "reason": "Some steps failed, so this run was not compared with the last one."}
    tables = monitor.tables_of(outputs)
    if not tables:
        return {"status": "skipped", "reason": "This run read no table, so there is nothing to compare."}
    subject, fp = subject_of(values, scope), monitor.fingerprint(watch)
    before = store.latest(playbook_id, subject)
    if before is None:
        store.add(playbook_id, subject, run_id, fp, tables, now)
        return {"status": "baseline"}
    if before.get("fp") != fp:
        store.add(playbook_id, subject, run_id, fp, tables, now)
        return {"status": "reset", "reason": "The watch settings changed, so this run starts a new comparison."}
    diff = monitor.compare(before["tables"], tables, watch)
    if diff.empty:
        # The reference stays as it was, so small moves (under the user's minimum) add up until they are big enough to report.
        store.touch(playbook_id, subject, now)
        return {"status": "unchanged"}
    store.add(playbook_id, subject, run_id, fp, tables, now)
    return {"status": "changed", "summary": monitor.summarize(diff), "diff": monitor.to_record(diff), **monitor.counts(diff)}
