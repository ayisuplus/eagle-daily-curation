import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image

from intake import read, sha
from storage import import_group, download
from test_intake import group


class FakeEagle:
    def __init__(self, root):
        self.root = Path(root)
        self.items = {}
        self.disks = {}
        self.adds = self.links = 0
        self.persist = True
        self.unknown_add = False
        self.cache_delay = 0

    def check(self):
        pass

    def rows(self):
        if self.adds and self.cache_delay:
            self.cache_delay -= 1
            return []
        return copy.deepcopy(list(self.items.values()))

    def get(self, iid):
        return copy.deepcopy(self.items[iid])

    def disk(self, item):
        return copy.deepcopy(self.disks.get(item['id']))

    def add(self, local, name, url, folder, tags):
        self.adds += 1
        iid = 'ID'+str(self.adds)
        dest = self.root/(iid+'.png')
        dest.write_bytes(Path(local).read_bytes())
        self.items[iid] = {'id': iid, 'name': name, 'url': url, 'annotation': url,
                           'star': 0, 'folders': [folder], 'tags': list(tags), 'filePath': str(dest)}
        if self.persist:
            self.disks[iid] = self.get(iid)
        if self.unknown_add:
            raise RuntimeError('simulated lost response')

    def link(self, item, url, folder, tags):
        self.links += 1
        x = self.items[item['id']]
        x['annotation'] += '\n\n'+url
        x['folders'] = list(dict.fromkeys(x['folders']+[folder]))
        x['tags'] = list(dict.fromkeys(x['tags']+tags))
        if self.persist:
            self.disks[x['id']] = self.get(x['id'])


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.media = self.root/'source.png'
        Image.new('RGB', (8, 8), (30, 60, 90)).save(self.media)
        self.g = group()
        self.g.update(folderId='F', tags=['用途/穿搭参考'], originalText='作者原文')
        self.g['media'] = [{'order': 1, 'localPath': str(self.media)}]
        self.gateway = FakeEagle(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def run_import(self, **kwargs):
        return import_group(self.g, self.root/'state', gateway=self.gateway, **kwargs)

    def test_rerun_reuses_media_and_preserves_post_text(self):
        first = self.run_import()
        second = self.run_import()
        self.assertEqual(self.gateway.adds, 1)
        self.assertEqual(second['media'][0]['status'], 'reused')
        self.assertEqual(read(first['sidecar'])['originalText'], '作者原文')

    def test_webp_without_url_suffix_works_without_windows_mime_registration(self):
        data = io.BytesIO()
        Image.new('RGB', (8, 8), 'white').save(data, format='WEBP')
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(data.getvalue())
                from email.message import Message
                self.headers = Message(); self.headers['Content-Type'] = 'image/webp'
                self.url = 'https://cdn.example.org/image!webp_variant'
        with patch('import_web_media.mimetypes.guess_extension', return_value=None):
            local = download({'mediaUrl': 'https://cdn.example.org/image!webp_variant',
                              'pageUrl': self.g['pageUrl']}, self.root,
                             lambda *args, **kwargs: Response())
        self.assertEqual(local.suffix, '.webp')

    def test_cross_source_reuse_preserves_user_fields(self):
        self.run_import()
        self.gateway.items['ID1'].update(star=4, tags=['已有标签'], name='用户命名')
        self.gateway.disks['ID1'] = self.gateway.get('ID1')
        self.g['pageUrl'] = 'https://www.xiaohongshu.com/explore/abc'
        self.run_import()
        self.assertEqual(self.gateway.adds, 1)
        item = self.gateway.get('ID1')
        self.assertEqual(item['star'], 4)
        self.assertEqual(item['name'], '用户命名')
        self.assertEqual(item['tags'], ['已有标签', '用途/穿搭参考'])

    def test_deleted_item_is_never_restored(self):
        self.run_import()
        self.gateway.items.clear()
        result = self.run_import()
        self.assertEqual(self.gateway.adds, 1)
        self.assertEqual(result['status'], 'not-collected')

    def test_lost_response_needs_read_only_reconciliation(self):
        self.gateway.unknown_add = True
        with self.assertRaises(RuntimeError):
            self.run_import()
        with self.assertRaises(RuntimeError):
            self.run_import()
        self.gateway.unknown_add = False
        self.assertEqual(self.run_import(reconcile=True)['status'], 'verified')
        self.assertEqual(self.gateway.adds, 1)
        self.assertEqual(self.gateway.links, 0)

    def test_reconciliation_cannot_import_a_fresh_group(self):
        with self.assertRaises(RuntimeError):
            self.run_import(reconcile=True)
        self.assertEqual(self.gateway.adds, 0)
        self.assertEqual(self.gateway.links, 0)

    def test_reconciliation_cannot_replace_verified_but_changed_media(self):
        result = self.run_import()
        Path(result['media'][0]['path']).write_bytes(b'changed-media')
        with self.assertRaises(RuntimeError):
            self.run_import(reconcile=True)
        self.assertEqual(self.gateway.adds, 1)
        self.assertEqual(self.gateway.links, 0)

    def test_reconciliation_cannot_add_unattempted_later_pages(self):
        self.gateway.unknown_add = True
        with self.assertRaises(RuntimeError):
            self.run_import()
        second = self.root/'later.png'
        Image.new('RGB', (8, 8), 'yellow').save(second)
        self.g['media'].append({'order': 2, 'localPath': str(second)})
        self.gateway.unknown_add = False
        with self.assertRaises(RuntimeError):
            self.run_import(reconcile=True)
        self.assertEqual(self.gateway.adds, 1)
        self.assertEqual(self.gateway.links, 0)

    def test_api_success_without_disk_save_is_blocked_and_not_rewritten(self):
        self.gateway.persist = False
        with patch('storage.time.sleep'):
            with self.assertRaises(RuntimeError):
                self.run_import()
        with self.assertRaises(RuntimeError):
            self.run_import()
        self.assertEqual(self.gateway.adds, 1)

    def test_delayed_inventory_is_polled_without_replaying_add(self):
        self.gateway.cache_delay = 2
        with patch('storage.time.sleep'):
            self.assertEqual(self.run_import()['status'], 'verified')
        self.assertEqual(self.gateway.adds, 1)

    def test_legacy_removed_content_is_not_reimported(self):
        result = self.run_import(legacy={'items': {'removed': {'sha256': sha(self.media)}}})
        self.assertEqual(result['status'], 'not-collected')
        self.assertEqual(self.gateway.adds, 0)

    def test_legacy_no_retry_metadata_is_not_written(self):
        self.run_import()
        self.g['pageUrl'] = 'https://www.xiaohongshu.com/explore/abc'
        with self.assertRaises(RuntimeError):
            self.run_import(legacy={'nextActions': [{'itemId': 'ID1', 'stopAutomaticRetries': True}]})
        self.assertEqual(self.gateway.links, 0)

    def test_uncertain_content_cannot_be_written_through_another_source(self):
        self.gateway.unknown_add = True
        with self.assertRaises(RuntimeError):
            self.run_import()
        self.g['pageUrl'] = 'https://www.xiaohongshu.com/explore/abc'
        with self.assertRaises(RuntimeError):
            self.run_import()
        self.assertEqual(self.gateway.links, 0)

    def test_ambiguous_new_media_stays_blocked_on_reconciliation(self):
        self.gateway.unknown_add = True
        with self.assertRaises(RuntimeError):
            self.run_import()
        self.gateway.unknown_add = False
        self.gateway.add(self.media, 'duplicate', self.g['pageUrl'], 'F', self.g['tags'])
        with self.assertRaises(RuntimeError):
            self.run_import(reconcile=True)
        self.assertEqual(self.gateway.links, 0)

    def test_different_analysis_pages_stay_distinct_and_ordered(self):
        other = self.root/'back.png'
        Image.new('RGB', (8, 8), 'white').save(other)
        self.g['role'] = 'mixed'
        self.g['media'].append({'order': 2, 'localPath': str(other)})
        result = self.run_import()
        self.assertEqual(self.gateway.adds, 2)
        self.assertEqual([m['order'] for m in result['media']], [1, 2])


if __name__ == '__main__':
    unittest.main()
