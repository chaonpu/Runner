"""Conservative hosted quota candidate check; unknown means verified Windows.

GitHub's usage API is historical, may lag, and cannot *prove* a realtime
remaining balance. A large extra guard is required before electing private
hosted Linux. This is a routing policy, never a billing guarantee.
"""
from __future__ import annotations

HOSTED_WORKFLOW = "android-cloud-verified.yml"
WINDOWS_WORKFLOW = "test-self-hosted.yml"
HOSTED_ARTIFACT = "cloud-test-verified"
WINDOWS_ARTIFACT = "windows-test-verified"
HOSTED_TIMEOUT_MINUTES = 100
REPORTING_LAG_GUARD_MINUTES = 400
ALLOWED_ROUTES = ("windows", "hosted")


def choose(task, billing):
    """Never infer a free quota from public repository usage or missing data."""
    fallback = {"route": "windows", "reason": "hosted_allowance_unverified",
                "hostedQuotaGuaranteed": False}
    if not isinstance(task, dict) or not isinstance(billing, dict):
        return fallback
    estimate = billing.get("allowanceEstimate")
    if (billing.get("read") != "readable" or not isinstance(estimate, dict)
            or estimate.get("read") != "readable"
            or estimate.get("conversionSupported") is not True
            or estimate.get("estimateStatus") != "private_standard_linux_only"
            or estimate.get("publicStandardUsageExcluded") is not True
            or estimate.get("period") != billing.get("period")):
        return fallback
    remaining = estimate.get("remainingMinutesEstimate")
    reserve = task.get("reserveMinutes", 200)
    if (type(remaining) is not int or type(reserve) is not int or reserve < 200):
        return fallback
    threshold = HOSTED_TIMEOUT_MINUTES + reserve + REPORTING_LAG_GUARD_MINUTES
    if remaining < threshold:
        return {**fallback, "reason": "remaining_estimate_below_conservative_threshold"}
    return {"route": "hosted",
            "reason": "conservative_reported_private_linux_allowance_estimate",
            "hostedQuotaGuaranteed": False,
            "safetyThresholdMinutes": threshold}


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
