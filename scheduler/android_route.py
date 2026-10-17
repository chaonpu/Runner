"""Shared 100-minute cloud/self-hosted threshold for Android builds."""
from __future__ import annotations

HOSTED_WORKFLOW = "android-cloud-verified.yml"
WINDOWS_WORKFLOW = "test-self-hosted.yml"
HOSTED_ARTIFACT = "cloud-test-verified"
WINDOWS_ARTIFACT = "windows-test-verified"
from scheduler.policies import hosted_remaining, SELF_HOSTED_BELOW_MINUTES
ALLOWED_ROUTES = ("windows", "hosted")


def choose(task, billing):
    """Unknown pauses dispatch; only a balance below 100 selects Windows."""
    remaining = hosted_remaining(billing)
    common = {"hostedQuotaGuaranteed": False,
              "switchBelowMinutes": SELF_HOSTED_BELOW_MINUTES}
    if remaining is None:
        return {**common, "route": "hold", "reason": "hosted_allowance_unverified"}
    if remaining < SELF_HOSTED_BELOW_MINUTES:
        return {**common, "route": "windows", "reason": "remaining_allowance_below_100_minutes"}
    return {**common, "route": "hosted", "reason": "remaining_allowance_at_least_100_minutes"}


def workflow(route):
    if route == "hosted":
        return HOSTED_WORKFLOW
    if route == "windows":
        return WINDOWS_WORKFLOW
    raise ValueError("unknown_android_route")


def artifact(route):
    if route == "hosted":
        return HOSTED_ARTIFACT
    if route == "windows":
        return WINDOWS_ARTIFACT
    raise ValueError("unknown_android_route")
