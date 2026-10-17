"""Fresh commit-pinned read-back and bounded read-only propagation handling."""
import base64
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from scheduler import android_publish as p
from test_android_publish import PUBLISH


class PublicVerificationTests(unittest.TestCase):
    data=b'signed APK verification fixture'*100

    def request(self):
        return dict(PUBLISH,apkSha256=hashlib.sha256(self.data).hexdigest())

    def manifest(self):
        req=self.request()
        return dict(req,downloadUrl='https://github.com/chaonpu/ziwuxilv/releases/download/v2.3.5/ziwuxilv-native-v2.3.5.apk')

    def test_commit_pinned_content_and_blob_hash_are_required(self):
        head='d'*40;raw=json.dumps(self.manifest()).encode()
        item={'type':'file','encoding':'base64','path':'android-native/version.json',
              'content':base64.b64encode(raw).decode(),
              'sha':hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()}
        with patch.object(p,'public_api',side_effect=[{'commit':{'sha':head}},item]) as api:
            self.assertEqual(p.read_public_manifest('read-token'),self.manifest())
            self.assertEqual(api.call_args_list[1].args,('/contents/android-native/version.json?ref='+head,'read-token'))
        with patch.object(p,'public_api',side_effect=[{'commit':{'sha':head}},dict(item,sha='f'*40)]):
            with self.assertRaisesRegex(p.build.Hold,'blob_mismatch'):p.read_public_manifest()

    def test_previous_cached_version_is_read_again_without_any_publication(self):
        current=self.manifest();old=dict(current,versionCode=117,versionName='2.3.4')
        with patch.object(p,'read_public_manifest',side_effect=[old,current]) as read,\
             patch.object(p.time,'sleep') as sleep,\
             patch.object(p.urllib.request,'urlopen',return_value=io.BytesIO(self.data)) as download,\
             patch.object(p,'public_api') as api:
            self.assertEqual(p.verify_public(self.request(),'read-token'),current['downloadUrl'])
            self.assertEqual(read.call_count,2);sleep.assert_called_once_with(5)
            download.assert_called_once();api.assert_not_called()

    def test_same_version_conflict_stops_immediately_with_field_name(self):
        current=self.manifest()
        for field,value in [('apkSha256','0'*64),('sourceCommit','0'*40),('updateLog','Other'),('forceUpdate',True)]:
            with self.subTest(field=field),patch.object(p,'read_public_manifest',return_value=dict(current,**{field:value})),\
                 patch.object(p.time,'sleep') as sleep,patch.object(p.urllib.request,'urlopen') as download:
                with self.assertRaisesRegex(p.build.Hold,'published_manifest_mismatch:'+field):
                    p.verify_public(self.request())
                sleep.assert_not_called();download.assert_not_called()

    def test_stale_manifest_has_finite_wait_and_does_not_download(self):
        with patch.object(p,'read_public_manifest',return_value=dict(self.manifest(),versionCode=117)) as read,\
             patch.object(p.time,'sleep') as sleep,patch.object(p.urllib.request,'urlopen') as download:
            with self.assertRaisesRegex(p.build.Hold,'propagation_timeout'):p.verify_public(self.request())
            self.assertEqual(read.call_count,7);self.assertEqual(sleep.call_count,6)
            download.assert_not_called()

    def test_wrong_public_apk_bytes_remain_rejected(self):
        with patch.object(p,'read_public_manifest',return_value=self.manifest()),\
             patch.object(p.urllib.request,'urlopen',return_value=io.BytesIO(b'bad APK'*200)):
            with self.assertRaisesRegex(p.build.Hold,'digest_mismatch'):p.verify_public(self.request())

    def test_wrong_download_location_is_rejected(self):
        with patch.object(p,'read_public_manifest',return_value=dict(self.manifest(),downloadUrl='https://example.invalid/app.apk')),\
             patch.object(p.urllib.request,'urlopen') as download:
            with self.assertRaisesRegex(p.build.Hold,'download_url_mismatch'):p.verify_public(self.request())
            download.assert_not_called()
