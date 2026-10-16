import hashlib
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from scheduler.apk_stage import (
    CERT, GuardError, assert_clean_artifact, build_tools,
    guard_pre_release, package_public_transport, parse_request,
    validate_run, verify_apk_bytes,
)

SHA = "c" * 40

def params(version="2.3.5", code=118):
    return {
        "signingRunId": 456, "testRunId": 123,
        "sourceCommit": SHA, "versionName": version, "versionCode": code,
    }

def args(approval):
    return Namespace(signing_run_id="456", test_run_id="123",
                     source_commit=SHA, version_name="2.3.5",
                     version_code="118", approval=approval)

def environment():
    return {
        "GITHUB_ACTOR": "chaonpu", "GITHUB_REF": "refs/heads/main",
        "TEST_ACTIONS_READ_TOKEN": "fake", "CHAONPU_ACTIONS_READ_TOKEN": "fake",
        "GH_TOKEN": "fake",
    }

class APKStagingTests(unittest.TestCase):
    def fixture(self, root, req):
        signed = root / "signed"
        signed.mkdir()
        file = f"ziwuxilv-native-v{req['versionName']}.apk"
        apk = signed / file
        apk.write_bytes(b"test-only" * 200)
        sha = hashlib.sha256(apk.read_bytes()).hexdigest()
        (signed / (file + ".sha256")).write_text(sha + "  " + file + "\n")
        (signed / "source-commit.txt").write_text(req["sourceCommit"] + "\n")
        (signed / "signing-cert-sha256.txt").write_text(CERT + "\n")
        metadata = {
            "schemaVersion": 1,
            "artifactKind": "verified-private-windows-signed-apk",
            "sourceRepository": "anwpustudent-lab/Test",
            "sourceCommit": req["sourceCommit"], "testRunId": req["testRunId"],
            "signingRepository": "chaonpu/ziwuxilv-android",
            "signingRunId": req["signingRunId"],
            "apkName": file, "apkSha256": sha,
            "apkPackage": "com.ziwuxilv.nativeapp",
            "versionName": req["versionName"], "versionCode": req["versionCode"],
            "signingCertificateSha256": CERT,
            "publicationAuthorized": False, "publicTransportReady": False,
        }
        (signed / "publish-candidate-metadata.json").write_text(json.dumps(metadata))
        return signed

    def test_requires_explicit_version_and_run_specific_approval(self):
        approved = "STAGE-PUBLIC-APK-2.3.5-456"
        self.assertEqual(parse_request(args(approved), environment())["signingRunId"], 456)
        with self.assertRaisesRegex(GuardError, "explicit_staging_approval_missing"):
            parse_request(args("YES"), environment())

    def test_enforces_actor_and_main(self):
        env = environment()
        env["GITHUB_ACTOR"] = "untrusted"
        with self.assertRaisesRegex(GuardError, "untrusted_workflow_actor_or_ref"):
            parse_request(args("STAGE-PUBLIC-APK-2.3.5-456"), env)
        env = environment()
        env["GITHUB_REF"] = "refs/heads/feature"
        with self.assertRaisesRegex(GuardError, "untrusted_workflow_actor_or_ref"):
            parse_request(args("STAGE-PUBLIC-APK-2.3.5-456"), env)

    def test_rejects_missing_private_read_token(self):
        env = environment()
        del env["CHAONPU_ACTIONS_READ_TOKEN"]
        with self.assertRaisesRegex(GuardError, "private_read_credentials_missing"):
            parse_request(args("STAGE-PUBLIC-APK-2.3.5-456"), env)

    def test_run_provenance_exact_workflow_and_sha(self):
        run = dict(id=123, repository={"full_name": "anwpustudent-lab/Test"},
                   path=".github/workflows/test-self-hosted.yml@main",
                   status="completed", conclusion="success",
                   event="workflow_dispatch", head_branch="main", head_sha=SHA)
        validate_run(run, "anwpustudent-lab/Test",
                     ".github/workflows/test-self-hosted.yml", 123, SHA)
        with self.assertRaisesRegex(GuardError, "test_source_commit_mismatch"):
            validate_run(run, "anwpustudent-lab/Test",
                         ".github/workflows/test-self-hosted.yml", 123, "e" * 40)
        run["conclusion"] = "failure"
        with self.assertRaisesRegex(GuardError, "workflow_run_not_verified"):
            validate_run(run, "anwpustudent-lab/Test",
                         ".github/workflows/test-self-hosted.yml", 123)

    def test_verified_test_main_push_allowed_but_signer_push_is_rejected(self):
        test = dict(id=123, repository={"full_name": "anwpustudent-lab/Test"},
                    path=".github/workflows/test-self-hosted.yml",
                    status="completed", conclusion="success",
                    event="push", head_branch="main", head_sha=SHA)
        validate_run(test, "anwpustudent-lab/Test",
                     ".github/workflows/test-self-hosted.yml", 123, SHA)
        signer = dict(id=456, repository={"full_name": "chaonpu/ziwuxilv-android"},
                      path=".github/workflows/windows-signing-verified-handoff.yml",
                      status="completed", conclusion="success",
                      event="push", head_branch="main")
        with self.assertRaisesRegex(GuardError, "workflow_run_not_verified"):
            validate_run(signer, "chaonpu/ziwuxilv-android",
                         ".github/workflows/windows-signing-verified-handoff.yml", 456)
        signer["event"] = "workflow_dispatch"
        validate_run(signer, "chaonpu/ziwuxilv-android",
                     ".github/workflows/windows-signing-verified-handoff.yml", 456)

    def test_candidate_artifact_matches_all_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            req = params()
            root = Path(d)
            signed = self.fixture(root, req)
            _, candidate, names = assert_clean_artifact(signed, req)
            self.assertEqual(candidate["sourceCommit"], SHA)
            self.assertIn("publish-candidate-metadata.json", names)
            archived = root / "candidate.zip"
            digest = package_public_transport(signed, names, archived)
            self.assertEqual(digest, hashlib.sha256(archived.read_bytes()).hexdigest())

    def test_unrecognized_file_forbidden_to_block_private_source(self):
        with tempfile.TemporaryDirectory() as d:
            req = params()
            signed = self.fixture(Path(d), req)
            (signed / "release-source.zip").write_bytes(b"PRIVATE SOURCE")
            with self.assertRaisesRegex(GuardError, "artifact_files_not_allowlisted"):
                assert_clean_artifact(signed, req)

    def test_tampered_bytes_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            req = params()
            signed = self.fixture(Path(d), req)
            (signed / "ziwuxilv-native-v2.3.5.apk").write_bytes(b"changed" * 200)
            with self.assertRaisesRegex(GuardError, "apk_sha_sidecar_mismatch"):
                assert_clean_artifact(signed, req)

    def test_stale_or_equal_version_never_staged(self):
        for code in (117, 116):
            with self.assertRaisesRegex(GuardError, "staging_requires_version_newer"):
                guard_pre_release(params(code=code), 117)
        guard_pre_release(params(code=118), 117)

    def test_apk_certificate_and_version_checked_by_native_tools(self):
        with tempfile.TemporaryDirectory() as d:
            apk = Path(d) / "signed.apk"
            apk.write_bytes(b"test-only" * 200)
            def cmd(args):
                if args[0] == "signer":
                    return "Number of signers: 1\nV3.0 Signer: certificate SHA-256 digest: " + CERT + "\n"
                return "package: name='com.ziwuxilv.nativeapp' versionCode='118' versionName='2.3.5'\n"
            verify_apk_bytes(apk, params(), tools=("signer", "aapt"), command=cmd)
            def corrupted(args):
                if args[0] == "signer":
                    return "Number of signers: 1\nSigner #1 certificate SHA-256 digest: " + "a" * 64 + "\n"
                return ""
            with self.assertRaisesRegex(GuardError, "android_production_certificate_mismatch"):
                verify_apk_bytes(apk, params(), tools=("signer", "aapt"), command=corrupted)

    def test_formal_publish_not_called_by_staging_functions(self):
        import inspect
        from scheduler import apk_stage
        source = inspect.getsource(apk_stage)
        self.assertIn('"--prerelease"', source)
        self.assertNotIn('"--latest"', source)
        self.assertNotIn('"gh", "release", "upload"', source)
        self.assertNotIn("android-publish-request.json", source)


if __name__ == "__main__":
    unittest.main()
