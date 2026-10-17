"""Offline contracts for mobile result summaries and rerun semantics."""
import unittest
from scheduler.entry_result import summarize

class EntryResultTests(unittest.TestCase):
    def test_skip_is_not_a_business_run(self):
        status, _, ran = summarize("stock_a", [{"status":"skipped",
             "reason":"authoritative_market_already_current"}], "success", False)
        self.assertEqual(status, "已跳过")
        self.assertFalse(ran)

    def test_completed_android_source_is_a_clear_noop(self):
        status, detail, ran = summarize("android_build", [{
            "phase": "already_signed", "status": "skipped",
            "reason": "source_already_signed_verified_no_rebuild",
            "testRunId": 321, "signingRunId": 456}], "success", False)
        self.assertEqual(status, "已跳过")
        self.assertIn("已完成测试、构建与签名", detail)
        self.assertFalse(ran)

    def test_prior_recovery_is_not_a_fresh_run(self):
        status, _, ran = summarize("stock_us", [{"status":"skipped",
             "completionMode":"recovered_original_commit"}], "success", False)
        self.assertEqual(status, "已恢复")
        self.assertFalse(ran)

    def test_production_attestation_requires_verification(self):
        status, _, ran = summarize("stock_a", [{"status":"verified",
            "businessVerified": True}], "success", False)
        self.assertEqual(status, "已执行")
        self.assertTrue(ran)

    def test_green_without_receipt_is_failure(self):
        status, _, ran = summarize("stock_a", [], "success", False)
        self.assertEqual(status, "失败")
        self.assertFalse(ran)

    def test_checkout_recovery_does_not_mean_public_release(self):
        status, detail, ran = summarize("android_build", [
            {"phase":"test_checkout_recovered"},
            {"phase":"signed_verified"}], "success", False)
        self.assertEqual(status, "已恢复")
        self.assertIn("尚未正式发布", detail)
        self.assertTrue(ran)

    def test_preview_does_not_execute(self):
        status, _, ran = summarize("android_publish", [], "success", True)
        self.assertEqual(status, "仅检查")
        self.assertFalse(ran)

    def test_failed_job_cannot_pass_from_stale_success(self):
        status, _, ran = summarize("android_publish", [
            {"status":"published","published":True},
            {"status":"hold","reason":"publication_blocked"}], "failure", False)
        self.assertEqual(status, "失败")
        self.assertFalse(ran)

if __name__ == "__main__":
    unittest.main()
