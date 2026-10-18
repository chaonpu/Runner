"""Only remove the exact generated Runner prerelease after publication."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from scheduler import android_publish as p

TAG='staged-apk-v2.3.8-signrun-456'
ZIP='ziwuxilv-verified-v2.3.8.zip'
URL='https://github.com/chaonpu/Runner/releases/download/'+TAG+'/'+ZIP
ROW={'phase':'published','versionName':'2.3.8','signingRunId':456,
     'publishRequest':{'sourceUrl':URL}}
RELEASE={'id':987,'tag_name':TAG,'prerelease':True,'draft':False,
         'assets':[{'name':ZIP,'browser_download_url':URL},{'name':'staging-receipt.json'}]}

class CleanupTests(unittest.TestCase):
    def test_delete_only_bound_id_then_confirm_absence(self):
        with patch.object(p,'staging_release_api',side_effect=[RELEASE,None,None]) as api:
            result=p.cleanup_published_staging({'GH_TOKEN':'test'},ROW)
        self.assertEqual(result['status'],'deleted')
        self.assertEqual(api.call_args_list[0].args[1],'/releases/tags/'+TAG)
        self.assertEqual(api.call_args_list[1].args[1],'/releases/987')
        self.assertEqual(api.call_args_list[1].kwargs,{'method':'DELETE'})
        self.assertEqual(api.call_args_list[2].args[1],'/releases/987')

    def test_missing_release_is_idempotent(self):
        with patch.object(p,'staging_release_api',return_value=None) as api:
            self.assertEqual(p.cleanup_published_staging({},ROW)['status'],'already_absent')
        self.assertEqual(api.call_count,1)

    def test_no_cleanup_before_published_or_for_foreign_url(self):
        for changes in ({'phase':'prepared'}, {'publishRequest':{'sourceUrl':'https://github.com/chaonpu/ziwuxilv/releases/download/v2.3.8/app.apk'}}):
            with patch.object(p,'staging_release_api') as api:
                with self.assertRaises(p.build.Hold):p.cleanup_published_staging({},dict(ROW,**changes))
                api.assert_not_called()

    def test_reject_formal_release_wrong_tag_or_unexpected_assets(self):
        for field,value in [('prerelease',False),('draft',True),('tag_name','v2.3.8'),
                            ('assets',RELEASE['assets']+[{'name':'other.apk'}])]:
            release=deepcopy(RELEASE);release[field]=value
            with patch.object(p,'staging_release_api',return_value=release) as api:
                with self.assertRaisesRegex(p.build.Hold,'identity_mismatch'):
                    p.cleanup_published_staging({},ROW)
                self.assertEqual(api.call_count,1)

    def test_unconfirmed_delete_is_not_success(self):
        with patch.object(p,'staging_release_api',side_effect=[RELEASE,None,RELEASE]):
            with self.assertRaisesRegex(p.build.Hold,'delete_unconfirmed'):
                p.cleanup_published_staging({},ROW)

    def test_api_rejects_arbitrary_endpoint_without_sending_request(self):
        for path,method in [('/releases','DELETE'),('/git/refs/tags/v2.3.8','DELETE'),
                            ('/releases/tags/v2.3.8','GET')]:
            with self.assertRaisesRegex(p.build.Hold,'endpoint_forbidden'):
                p.staging_release_api({'GH_TOKEN':'test'},path,method=method)

if __name__=='__main__':unittest.main()
