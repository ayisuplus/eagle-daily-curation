import tempfile
import unittest
from pathlib import Path
from intake import build_plan, canonical_source, freeze_legacy, seed_candidates


def group(role='external-reference'):
    return {'title': '服装分层参考', 'pageUrl': 'https://www.pinterest.com/pin/123/',
            'role': role, 'decision': 'collect', 'selectionReason': '分层关系可直接使用',
            'usableFor': [{'purpose': '穿搭参考', 'basis': '单品与层次均清晰'}],
            'media': [{'order': 1, 'mediaUrl': 'https://i.pinimg.com/example.jpg'}],
            'evidence': {'viewedMedia': ['page image 1'], 'observation': '针织内搭与外套层次',
                         'requiredPagesComplete': True}}


class IntakeTests(unittest.TestCase):
    def test_ready_reference_and_beautiful_original_need_no_processing(self):
        for role in ('external-reference', 'original', 'mixed'):
            self.assertEqual(build_plan([group(role)])['tasks'], [])

    def test_missing_unrelated_dimensions_are_not_tasks(self):
        g = group()
        g['limitations'] = ['没有服装背面，不能用于精确制版']
        self.assertEqual(build_plan([g])['tasks'], [])

    def test_unreadable_or_incomplete_reference_cannot_be_collected_as_ready(self):
        g = group()
        g['evidence']['requiredPagesComplete'] = False
        with self.assertRaises(ValueError):
            build_plan([g])
        g['decision'] = 'hold'
        self.assertEqual(build_plan([g])['tasks'], [])

    def test_only_selected_beneficial_enrichment_is_scheduled(self):
        g = group('original')
        g['enrichment'] = {'purpose': '服装设计', 'benefit': '解释未标出的层次',
                           'deliverable': '简短文字关系说明'}
        self.assertEqual(len(build_plan([g])['tasks']), 1)
        with self.assertRaises(ValueError):
            build_plan([g, g])
        g['enrichment'].update(status='blocked', reason='关键参考缺失', resumeCondition='获得参考')
        self.assertEqual(build_plan([g])['tasks'], [])

    def test_enrichment_cannot_claim_completion_without_evidence(self):
        g = group()
        g['enrichment'] = {'purpose': '穿搭', 'benefit': '说明', 'deliverable': '文字', 'status': 'completed'}
        with self.assertRaises(ValueError):
            build_plan([g])

    def test_source_keys_ignore_tracking_on_observed_detail_urls(self):
        self.assertEqual(canonical_source('https://www.pinterest.com/pin/123/?utm_source=x'), 'pinterest:123')
        self.assertEqual(canonical_source('https://www.xiaohongshu.com/discovery/item/abc?xsec_token=x'), 'xiaohongshu:abc')

    def test_auto_outputs_and_unendorsed_auto_imports_cannot_train_taste(self):
        rows = [{'id': i, 'ext': 'jpg', 'folders': ['F']} for i in ['old', 'generated', 'auto']]
        legacy = {'items': {r['id']: {} for r in rows}, 'generatedItemIds': ['generated']}
        receipts = {'imports': {'x': {'itemId': 'auto'}}}
        self.assertEqual([r['id'] for r in seed_candidates(rows, legacy, receipts)], ['old'])
        self.assertEqual(len(seed_candidates(rows, legacy, receipts, {'auto': 'like'})), 2)

    def test_freeze_preserves_legacy_bytes_without_scheduling_or_mutating(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            legacy = root/'old'; legacy.mkdir()
            source = legacy/'dedup-index.json'
            original = b'{"nextActions": [{"pending": true}]}'
            source.write_bytes(original)
            receipt = freeze_legacy(root/'new', legacy)
            self.assertEqual(receipt['legacyTasksScheduled'], 0)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(Path(receipt['files'][0]['snapshot']).read_bytes(), original)
            self.assertEqual(freeze_legacy(root/'new', legacy), receipt)

    def test_old_results_missing_from_generated_id_list_cannot_seed_taste(self):
        rows = [{'id': 'old-card', 'ext': 'jpg'}]
        legacy = {'items': {'old-card': {'status': 'existing-result-needs-association-check'}}}
        self.assertEqual(seed_candidates(rows, legacy, {}), [])


if __name__ == '__main__':
    unittest.main()
