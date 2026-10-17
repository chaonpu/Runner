"""A temporary exact-task permit; restore original OFF config after execution."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta

from scheduler.production_ports import PUBLIC, CONFIG, document, need
from scheduler.production_session import context, WORKFLOWS


class Lease:
    def __init__(self, ports):
        self.ports = ports
        self.before, self.armed = None, None

    def read(self):
        p = self.ports
        token = p.env["GH_TOKEN"]
        sha = p.requester(token, "GET", PUBLIC + "/branches/main")["commit"]["sha"]
        return document(p.requester(token, "GET", PUBLIC + "/contents/"+CONFIG+"?ref="+sha), CONFIG)

    def put(self, config, sha):
        import base64, json
        return self.ports.requester(self.ports.env["GH_TOKEN"], "PUT", PUBLIC + "/contents/"+CONFIG, {
            "branch":"main", "sha":sha, "message":"Update exact-task production permit",
            "content":base64.b64encode((json.dumps(config,indent=2)+"\n").encode()).decode()})

    def arm(self, task, day, mode):
        owner = context(self.ports.env)
        cfg, sha = self.read()
        need(task in WORKFLOWS and mode in ("formal","theoretical_catchup")
             and cfg.get("dispatchEnabled") is False and "activeLease" not in cfg
             and all(t.get("enabledForDispatch") is False for t in cfg["tasks"].values()),
             "production_permit_already_active_or_invalid")
        self.before = deepcopy(cfg)
        next_config = deepcopy(cfg)
        next_config["dispatchEnabled"] = True
        next_config["tasks"][task].update(enabledForDispatch=True,
            windowsWorkflow=WORKFLOWS[task], publicationMode=mode)
        next_config["activeLease"] = {"taskId":task+":"+day, "publicationMode":mode,
            "coordinatorRunId":owner,"expiresAt":(datetime.now(timezone.utc)+timedelta(hours=6)).isoformat(),
            "restoreTask": {"windowsWorkflow":cfg["tasks"][task].get("windowsWorkflow"),
                "publicationMode":cfg["tasks"][task].get("publicationMode")}}
        # Keep intended value even if read-back fails, so finally can safely
        # attempt restoration ONLY when exact ownership/config still matches.
        self.armed = next_config
        self.put(next_config, sha)
        confirmed, _ = self.read()
        need(confirmed == self.armed, "production_permit_confirmation_failed")

    def release(self):
        if self.armed is None:
            return
        current, sha = self.read()
        if current == self.before:
            self.armed = None
            return
        need(current == self.armed, "production_permit_changed_manual_review")
        self.put(self.before, sha)
        after, _ = self.read()
        need(after == self.before, "production_permit_restore_unconfirmed")
        self.armed = None


def cleanup(env):
    """Workflow always-step can close its own permit even after process abort."""
    from scheduler.production_ports import ReadOnlyPorts
    owner = context(env)
    lease = Lease(ReadOnlyPorts(env))
    cfg, sha = lease.read()
    permit = cfg.get("activeLease")
    if permit is None:
        return
    need(permit.get("coordinatorRunId") == owner, "permit_cleanup_owner_mismatch")
    task = permit["taskId"].split(":")[0]
    need(task in WORKFLOWS and all(not spec.get("enabledForDispatch") for key,spec in cfg["tasks"].items() if key != task),
         "permit_cleanup_scope_changed")
    restored = deepcopy(cfg)
    restored["dispatchEnabled"] = False
    restored.pop("activeLease")
    restored["tasks"][task]["enabledForDispatch"] = False
    restored["tasks"][task]["windowsWorkflow"] = permit["restoreTask"]["windowsWorkflow"]
    old_mode = permit["restoreTask"].get("publicationMode")
    if old_mode is None:
        restored["tasks"][task].pop("publicationMode", None)
    else:
        restored["tasks"][task]["publicationMode"] = old_mode
    lease.put(restored, sha)
    current, _ = lease.read()
    need(current == restored, "permit_cleanup_unconfirmed")


if __name__ == "__main__":
    import os
    cleanup(dict(os.environ))
    print('{"ownProductionPermitClosed":true}')
