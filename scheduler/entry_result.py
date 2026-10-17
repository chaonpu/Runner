"""Show truthful mobile-friendly outcomes for the four manual Runner entries.

Always run as a final workflow step; only parse allowlisted public-safe JSON keys.
No credentials, source files, holdings or private job logs are included.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path

NAMES = {
    "stock_a": "A股策略", "stock_us": "美股策略",
    "android_build": "APK 构建与签名", "android_publish": "APK 正式发布",
}
REASONS = {
    "previously_verified_task_no_redispatch": "该交易日已有经核验的结果，本次未运行策略。",
    "authoritative_market_already_current": "权威账本与快照已更新，本次只完成预检。",
    "private_readiness_failed": "下游预检未通过，正式策略未派发。",
    "no_verified_android_signing_route": "未找到符合额度与运行器条件的签名路线。",
    "source_already_signed_verified_no_rebuild": "该源码 SHA 已完成测试、构建与签名。本次跳过重复构建，原有签名产物仍可使用。",
    "network_retry_exhausted_same_run_no_publication": "私有源码检出遇到 GitHub 网络问题；本次没有签名或发布。",
}


def read_rows(path):
    if not path or not Path(path).is_file():
        return []
    rows = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        raw = raw.strip()
        if not raw.startswith("{") or len(raw) > 8192:
            continue
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and (
                value.get("status") in ("verified", "skipped", "published", "read_only", "hold")
                or value.get("phase") in ("signed_verified", "read_only", "test_checkout_recovered")):
            rows.append(value)
    return rows


def summarize(kind, rows, job_result, preview):
    if job_result != "success":
        last = next((r for r in reversed(rows) if r.get("status") == "hold"), {})
        reason = last.get("reason", "")
        return "失败", REASONS.get(reason, "工作流或下游校验未成功；不得将其视为已执行。"), False
    if preview:
        return "仅检查", "仅查看当前条件，没有执行策略、签名或正式发布。", False
    last = next((r for r in reversed(rows) if r.get("status") in ("verified", "skipped", "published")
                 or r.get("phase") == "signed_verified"), {})
    if last.get("status") == "skipped":
        if last.get("completionMode") == "recovered_original_commit":
            return "已恢复", "沿用之前已恢复且核验的原始结果；本次未重复计算。", False
        reason = last.get("reason", "")
        return "已跳过", REASONS.get(reason, "检测到已有结果，本次未重复执行。"), False
    if last.get("status") == "verified":
        return "已执行", "原始下游 Run 和账本回执均已核验。", True
    if last.get("status") == "published" and last.get("published") is True:
        return "已执行", "已验证公开发布与下载文件。", True
    if last.get("phase") == "signed_verified":
        was_retry = any(r.get("phase") == "test_checkout_recovered" for r in rows)
        return ("已恢复" if was_retry else "已执行"), (
            "已在同一源码 SHA 完成一次安全检出恢复，签名产物已校验，尚未正式发布。"
            if was_retry else "签名 APK 和私有产物已核验，尚未正式发布。"), True
    return "失败", "当前运行缺少可独立识别的成功业务回执，不应将绿色工作流视为业务成功。", False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entry", choices=tuple(NAMES), required=True)
    p.add_argument("--result", choices=("success", "failure", "cancelled", "skipped"), required=True)
    p.add_argument("--preview", choices=("true", "false"), default="false")
    p.add_argument("--log", required=True)
    a = p.parse_args()
    rows = read_rows(a.log)
    title, explanation, confirmed = summarize(a.entry, rows, a.result, a.preview == "true")
    lines = [
        "### " + NAMES[a.entry] + "｜执行结果",
        "",
        "**状态：" + title + "**",
        "",
        explanation,
        "",
        "本次业务执行并核验：" + ("是" if confirmed else "否"),
        "",
        "说明：GitHub Actions 的 Success 仅代表入口工作流正常结束，并不自动代表重新计算或重新发布。",
    ]
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as out:
            out.write("\n".join(lines) + "\n")
    print(json.dumps({"entry": a.entry, "outcome": title,
                      "businessExecutedAndVerified": confirmed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
