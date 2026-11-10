import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from daily import activation_status, finish_round, main
from intake import canonical_source, read, save
from storage import import_group
from test_intake import group
from test_storage import FakeEagle
from PIL import Image


class RoundTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = self.root/'state'
        self.g = group()
        self.image = self.root/'source.png'
        Image.new('RGB', (8, 8), 'red').save(self.image)
        self.g.update(folderId='F', tags=[])
        self.g['media'] = [{'order': 1, 'localPath': str(self.image)}]
        self.manifest = {'groups': [self.g], 'sources': {
            'pinterest': {'status': 'readable', 'evidence': 'Observed detail image'},
            'xiaohongshu': {'status': 'readable', 'evidence': 'Observed detail image'}}}
        self.gateway = FakeEagle(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def execute(self, eid):
        receipt = {**import_group(self.g, self.state, gateway=self.gateway), 'executionId': eid}
        save(self.state/'executions'/(eid+'.json'), {'status': 'finished', 'results': [receipt]})
        return [receipt]

    def test_replay_of_same_execution_cannot_count_as_another_round(self):
        receipts = self.execute('one')
        finish_round(self.manifest, receipts, 'first', self.state)
        with self.assertRaises(ValueError):
            finish_round(self.manifest, receipts, 'second', self.state)

    def test_old_receipt_cannot_close_added_pages_or_changed_purpose(self):
        receipts = self.execute('one')
        changed = copy.deepcopy(self.manifest)
        changed['groups'][0]['media'].append({'order': 2, 'localPath': str(self.image)})
        with self.assertRaises(ValueError):
            finish_round(changed, receipts, 'wrong', self.state)
        changed = copy.deepcopy(self.manifest)
        changed['groups'][0]['usableFor'][0]['purpose'] = '精确制版'
        with self.assertRaises(ValueError):
            finish_round(changed, receipts, 'wrong', self.state)

    def test_failure_breaks_three_consecutive_rounds(self):
        finish_round(self.manifest, self.execute('a'), 'a', self.state)
        finish_round(self.manifest, self.execute('b'), 'b', self.state)
        self.g['pageUrl'] = 'https://www.xiaohongshu.com/explore/abc'
        finish_round(self.manifest, self.execute('c'), 'c', self.state)
        self.assertTrue(activation_status(self.state)['ready'])
        empty = {'groups': [], 'sources': self.manifest['sources']}
        finish_round(empty, [], 'failed', self.state)
        self.assertFalse(activation_status(self.state)['ready'])

    def test_one_platform_saved_is_not_two_platform_end_to_end_validation(self):
        for name in ('a', 'b', 'c'):
            finish_round(self.manifest, self.execute(name), name, self.state)
        self.assertFalse(activation_status(self.state)['ready'])

    def test_saved_group_must_have_its_own_execution_binding(self):
        receipts = self.execute('a')
        del receipts[0]['executionId']
        with self.assertRaises(ValueError):
            finish_round(self.manifest, receipts, 'wrong', self.state)

    def test_partial_save_can_report_success_and_blocker_without_activating(self):
        receipts = self.execute('a')
        second = copy.deepcopy(self.g)
        second['pageUrl'] = 'https://www.pinterest.com/pin/456/'
        manifest = {**self.manifest, 'groups': [self.g, second]}
        receipt = finish_round(manifest, receipts, 'partial', self.state)
        self.assertEqual(len(receipt['savedGroups']), 1)
        self.assertEqual(len(receipt['blockedGroups']), 1)
        self.assertFalse(receipt['liveValidated'])

    def test_zero_result_apply_creates_empty_receipts_and_finish_works(self):
        manifest = self.root/'empty.json'
        out = self.root/'saved.json'
        save(manifest, {'groups': [], 'sources': self.manifest['sources']})
        with patch('daily.archive_index', return_value=({}, {})), patch('storage.write_lock'):
            main(['--state-dir', str(self.state), 'import', '--manifest', str(manifest), '--out', str(out), '--apply'])
        self.assertEqual(read(out), [])
        receipt = finish_round(read(manifest), read(out), 'empty', self.state)
        self.assertEqual(receipt['collectionGroups'], 0)
        self.assertFalse(receipt['liveValidated'])


if __name__ == '__main__':
    unittest.main()
