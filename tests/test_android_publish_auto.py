"""Automatic publication selection must preserve exact artifacts and at-most-once barriers."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import base64
import hashlib
import json
import unittest
from unittest.mock import patch

from scheduler import android_publish as p
import test_apk_stage as artifact_fixture
from test_android_publish import ENV, REQ, SOURCE, PUBLISH


def build_row(source=SOURCE, phase='signed_verified', created='2026-10-17T16:00:00+00:00'):
    return {'sourceSha':source,'phase':phase,'createdAt':created,'testRunId':123,'signingRunId':456}


class AutomaticPublicationTests(unittest.TestCase):
    def request(self, builds=None, releases=None, log=''):
        self.documents={p.STATE:{'version':1,'releases':releases or {}},
                        p.build.STATE_PATH:{'version':1,'builds':builds if builds is not None else {SOURCE:build_row()}}}
        def document(token,path):
            return deepcopy(self.documents[path]),'blob'
        with patch.object(p.build,'json_file',side_effect=document),\
             patch.object(p,'candidate_version',return_value=('2.3.5',118)) as candidate,\
             patch.object(p,'source_release_notes',return_value='修复图表标记尺寸。') as notes:
            result=p.automatic_request(ENV,log)
            self.candidate_calls=candidate.call_count
            self.notes_calls=notes.call_count
            if notes.called:
                notes.assert_called_once_with(ENV,SOURCE,'2.3.5',118)
            return result

    def test_signed_artifact_supplies_version_and_default_notes(self):
        req=self.request()
        self.assertEqual(req,{**REQ,'updateLog':'修复图表标记尺寸。'})
        self.assertEqual(self.candidate_calls,1)
        self.assertEqual(self.notes_calls,1)
        self.assertEqual(self.request(log='修复滑动')['updateLog'],'修复滑动')
        self.assertEqual(self.notes_calls,0)

    def test_newer_incomplete_build_cannot_fall_back_to_old_signed_build(self):
        for phase in ('reserved','test_started','test_verified','signer_reserved','signer_started'):
            with self.subTest(phase=phase),self.assertRaisesRegex(p.build.Hold,'latest_build_not_verified_signed'):
                self.request(builds={SOURCE:build_row(), 'b'*40:build_row('b'*40,phase,'2026-10-17T17:00:00+00:00')})

    def test_build_order_uses_creation_not_recovery_time(self):
        old=build_row('b'*40,created='2026-10-17T15:00:00+00:00')
        old['updatedAt']='2026-10-17T18:00:00+00:00'
        self.assertEqual(self.request(builds={SOURCE:build_row(),'b'*40:old})['sourceCommit'],SOURCE)

    def test_ambiguous_or_missing_creation_time_stops_selection(self):
        with self.assertRaisesRegex(p.build.Hold,'latest_build_ambiguous'):
            self.request(builds={SOURCE:build_row(),'b'*40:build_row('b'*40)})
        row=build_row();del row['createdAt']
        with self.assertRaisesRegex(p.build.Hold,'creation_time_unverified'):
            self.request(builds={SOURCE:row})

    def test_one_known_publication_continues_with_original_notes_without_download(self):
        for phase in ('prepared','publishing','published'):
            with self.subTest(phase=phase):
                req=self.request(releases={'118':dict(REQ,phase=phase,publishRequest=PUBLISH,publicRunId=789)})
                self.assertEqual(req,REQ)
                self.assertEqual(self.candidate_calls,0)
                self.assertEqual(self.notes_calls,0)

    def test_uncertain_publication_barriers_remain_blocked(self):
        for phase in ('staging_reserved','dispatch_reserved'):
            with self.subTest(phase=phase),self.assertRaisesRegex(p.build.Hold,'outcome_unknown'):
                self.request(releases={'118':dict(REQ,phase=phase)})

    def test_multiple_pending_or_changed_notes_cannot_choose_or_mutate(self):
        row=dict(REQ,phase='prepared')
        with self.assertRaisesRegex(p.build.Hold,'multiple_publications'):
            self.request(releases={'118':row,'119':dict(row,versionCode=119)})
        with self.assertRaisesRegex(p.build.Hold,'update_log_conflict'):
            self.request(releases={'118':row},log='Changed')
        with self.assertRaisesRegex(p.build.Hold,'identity_conflict'):
            self.request(releases={'118':dict(row,signingRunId=999)})

    def test_automatic_preview_and_publish_use_same_identity(self):
        for execute in (False,True):
            with patch.object(p,'automatic_request',return_value=REQ),patch.object(p,'run') as run:
                p.run_automatic(ENV,execute=execute)
                run.assert_called_once_with(ENV,source=SOURCE,version='2.3.5',code=118,
                                            log='Test update',mode='publish',execute=execute)

    def candidate(self, tamper=None):
        env=dict(ENV,CHAONPU_ACTIONS_READ_TOKEN='sign-token',TEST_ACTIONS_READ_TOKEN='test-token')
        def download(command,**kwargs):
            self.assertEqual(kwargs['env']['GH_TOKEN'],'sign-token')
            root=Path(command[command.index('--dir')+1])
            # Reuse real allowlisted artifact validation; only gh and Android
            # binaries are simulated, and no release API may be called.
            signed=artifact_fixture.APKStagingTests().fixture(root,REQ)
            for path in signed.iterdir():path.rename(root/path.name)
            signed.rmdir()
            if tamper=='source':(root/'source-commit.txt').write_text('b'*40)
            if tamper=='extra':(root/'private-source.py').write_text('private')
            if tamper=='idsig':(root/'ziwuxilv-native-v2.3.5.apk.idsig').write_bytes(b'incremental signature')
            return SimpleNamespace(returncode=0)
        with patch.object(p.apk_stage,'verify_provenance') as provenance,\
             patch.object(p.subprocess,'run',side_effect=download),\
             patch.object(p.apk_stage,'verify_apk_bytes') as apk,\
             patch.object(p,'public_api') as public:
            value=p.candidate_version(env,build_row())
            provenance.assert_called_once_with({'sourceCommit':SOURCE,'testRunId':123,'signingRunId':456},
                                                'sign-token','test-token')
            apk.assert_called_once();public.assert_not_called()
            return value

    def test_discovery_checks_private_provenance_artifact_hashes_and_apk_bytes(self):
        self.assertEqual(self.candidate(),('2.3.5',118))
        self.assertEqual(self.candidate('idsig'),('2.3.5',118))

    def test_discovery_rejects_foreign_source_or_extra_private_file(self):
        for tamper in ('source','extra'):
            with self.subTest(tamper=tamper),self.assertRaises(p.GuardError):
                self.candidate(tamper)


class SourceReleaseNotesTests(unittest.TestCase):
    def item(self, **changes):
        raw=json.dumps({'versionName':'2.3.5','versionCode':118,
                        'updateLog':'v2.3.5：\n1. 缩小 B/S 标记。\n2. 保持与 K 线居中对齐。',
                        **changes},ensure_ascii=False).encode()
        return {'type':'file','path':'release/version.json','encoding':'base64',
                'content':base64.b64encode(raw).decode(),
                'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}

    def test_reads_exact_signed_source_and_preserves_specific_multiline_notes(self):
        with patch.object(p.build,'api',return_value=self.item()) as api:
            notes=p.source_release_notes({'TEST_ACTIONS_READ_TOKEN':'test-token'},SOURCE,'2.3.5',118)
        self.assertEqual(notes,'v2.3.5：\n1. 缩小 B/S 标记。\n2. 保持与 K 线居中对齐。')
        api.assert_called_once_with('test-token',p.build.TEST,
                                    '/contents/release/version.json?ref='+SOURCE)

    def test_source_credential_takes_precedence(self):
        with patch.object(p.build,'api',return_value=self.item()) as api:
            p.source_release_notes({'TEST_SOURCE_READ_TOKEN':'source-token',
                                    'TEST_ACTIONS_READ_TOKEN':'actions-token'},SOURCE,'2.3.5',118)
        self.assertEqual(api.call_args.args[0],'source-token')

    def test_missing_or_generic_notes_and_other_versions_stop_publication(self):
        for changes,reason in [({'updateLog':''},'missing_or_invalid'),
                               ({'updateLog':'更新至 2.3.5（118）。'},'specific_changes'),
                               ({'versionName':'2.3.6'},'version_mismatch'),
                               ({'versionCode':119},'version_mismatch'),
                               ({'versionCode':'118'},'version_mismatch')]:
            with self.subTest(changes=changes),patch.object(p.build,'api',return_value=self.item(**changes)),\
                 self.assertRaisesRegex(p.build.Hold,reason):
                p.source_release_notes({'TEST_ACTIONS_READ_TOKEN':'test-token'},SOURCE,'2.3.5',118)

    def test_changed_blob_or_wrong_file_cannot_supply_notes(self):
        for field,value,reason in [('sha','0'*40,'blob_mismatch'),
                                   ('path','private.py','document_invalid'),
                                   ('content','not base64','document_invalid')]:
            item=self.item();item[field]=value
            with self.subTest(field=field),patch.object(p.build,'api',return_value=item),\
                 self.assertRaisesRegex(p.build.Hold,reason):
                p.source_release_notes({'TEST_ACTIONS_READ_TOKEN':'test-token'},SOURCE,'2.3.5',118)

    def test_no_main_fallback_when_exact_source_read_fails(self):
        with patch.object(p.build,'api',side_effect=p.build.Hold('read_unavailable')) as api,\
             self.assertRaisesRegex(p.build.Hold,'read_unavailable'):
            p.source_release_notes({'TEST_ACTIONS_READ_TOKEN':'test-token'},SOURCE,'2.3.5',118)
        self.assertEqual(api.call_count,1)
