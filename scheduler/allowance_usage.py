"""Repository-separated estimate of the user's confirmed monthly allowance.

No raw billing records or repository names are returned. An estimate does not
authorize dispatch: current visibility is not historical visibility, reporting
can lag, and private discounts may include free Pages/Dependabot usage.
"""
from datetime import date
import math
import re

REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
STANDARD = {"actions_linux", "actions_windows", "actions_macos",
            "actions_linux_arm", "actions_windows_arm", "actions_linux_slim"}
REASONS = {"monthly_allowance_missing", "usage_items_missing", "invalid_usage_item",
           "usage_period_conflict", "usage_quantity_invalid", "usage_repository_unresolved",
           "repository_probe_limit", "repository_visibility_unresolved", "user_allowance_only"}


def calculate(owner, allowance, data, metadata_get, instant):
    if type(allowance) is not int or allowance <= 0:
        raise ValueError("monthly_allowance_missing")
    rows = data.get("usageItems")
    if not isinstance(rows, list):
        raise ValueError("usage_items_missing")
    month = instant.strftime("%Y-%m")
    private_linux, public_standard, public_rows = 0.0, 0.0, 0
    visibility = {}
    unsupported = False
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_usage_item")
        if str(row.get("product", "")).lower() != "actions":
            continue
        if str(row.get("unitType", "")).lower() != "minutes":
            continue
        day = date.fromisoformat(str(row.get("date", ""))[:10])
        if day.strftime("%Y-%m") != month or day > instant.date():
            raise ValueError("usage_period_conflict")
        quantity = row.get("quantity")
        if type(quantity) not in (int, float) or not math.isfinite(quantity) or quantity < 0:
            raise ValueError("usage_quantity_invalid")
        if quantity == 0:
            continue
        sku = re.sub(r"\s+", "_", str(row.get("sku", "")).lower())
        if sku in ("actions_self_hosted", "actions_self-hosted"):
            continue
        repo = row.get("repositoryName")
        if isinstance(repo, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            repo = owner + "/" + repo
        if not isinstance(repo, str) or not REPO.fullmatch(repo) or repo.split("/")[0].lower() != owner.lower():
            raise ValueError("usage_repository_unresolved")
        if repo not in visibility:
            if len(visibility) >= 40:
                raise ValueError("repository_probe_limit")
            record = metadata_get("/repos/" + repo)
            if record.get("full_name", "").lower() != repo.lower() or type(record.get("private")) is not bool:
                raise ValueError("repository_visibility_unresolved")
            visibility[repo] = record["private"]
        if visibility[repo] is False and sku in STANDARD:
            public_standard += quantity
            public_rows += 1
        elif visibility[repo] is True and sku == "actions_linux":
            private_linux += quantity
        else:
            # Do not invent Windows/macOS/ARM minute conversion coefficients.
            unsupported = True
    result = {"read": "readable", "period": month,
              "monthlyIncludedMinutes": allowance,
              "allowanceSource": "user_confirmed",
              "publicStandardUsageExcluded": True,
              "publicStandardUsagePresent": public_rows > 0,
              "conversionSupported": not unsupported,
              "exactRemainingVerified": False,
              "independentlyVerified": False,
              "status": "unknown"}
    if not unsupported:
        result.update(estimateStatus="private_standard_linux_only",
                      privateLinuxMinutes=private_linux,
                      excludedPublicStandardMinutes=public_standard,
                      remainingMinutesEstimate=max(0, allowance - math.ceil(private_linux)))
    else:
        result["estimateStatus"] = "hold_unconfirmed_runner_conversion"
    return result


def probe(owner, spec, env, instant, *, reader_factory):
    from scheduler.probes import safe_error
    result = {"read": "secret_missing", "status": "unknown",
              "exactRemainingVerified": False, "independentlyVerified": False}
    billing = env.get(spec.get("billingSecret", ""), "")
    metadata = env.get(spec.get("actionsSecret", ""), "")
    if not billing or not metadata:
        return result
    try:
        if spec.get("kind") != "user":
            raise ValueError("user_allowance_only")
        data = reader_factory(billing).get(
            f"/users/{owner}/settings/billing/usage?year={instant.year}&month={instant.month}")
        return calculate(owner, spec.get("includedMinutes"), data,
                         reader_factory(metadata).get, instant)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        result["read"] = safe_error(exc)
        result["reason"] = str(exc) if str(exc) in REASONS else "unavailable_or_invalid_schema"
        return result
