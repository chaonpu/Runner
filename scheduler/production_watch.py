"""Bounded foreground observation of the ORIGINAL production run only.

No CLI, dispatch, cancellation, timer registration or background service. This
loop runs only in the active coordinator job. An uncertain read/write stops; it
never repeats an uncertain state write or starts another business run.
"""
import time

from scheduler.production_registry import RegistryGuard
from scheduler.production_session import observe


def watch(ports, session, env, *, interval=30, deadline_seconds=14400,
          pause=time.sleep, monotonic=time.monotonic, observe_fn=observe):
    if (type(interval) is not int or not 15 <= interval <= 60
            or type(deadline_seconds) is not int or not 60 <= deadline_seconds <= 19800):
        raise RegistryGuard("production_watch_bounds_invalid")
    deadline = monotonic() + deadline_seconds
    while monotonic() < deadline:
        # Deliberately no exception retry: an observation may have included a
        # CAS write whose response was lost. Original task/Run ID remain intact.
        result = observe_fn(ports, session, env)
        if result.get("status") in ("awaiting_effect_verification", "hold", "verified"):
            return {"watch": "stopped", "status": result["status"],
                    "businessVerified": False, "safeToRetry": False}
        if result.get("status") not in ("dispatched", "running"):
            raise RegistryGuard("production_watch_state_untrusted")
        remaining = deadline - monotonic()
        if remaining <= 0:
            break
        pause(min(interval, remaining))
    raise RegistryGuard("production_watch_deadline_manual_review")
