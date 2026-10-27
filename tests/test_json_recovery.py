"""Deterministic format boundaries; successful recovery is not semantic repair."""
import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('fixture_rule_json',ROOT/'src/rule_json.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
recover=module.recover

class JsonRecoveryTests(unittest.TestCase):
    def assertRejected(self,raw):
        result=recover(raw)
        self.assertFalse(result['ok'])
        self.assertIsNone(result['value'])
        self.assertIsNone(result['normalized_json'])
        self.assertFalse(result['audit']['semantic_repair_claimed'])
        return result
    def test_strict_object_preserves_number_and_field(self):
        raw='{"id":123456789,"value":36.8,"denied":true}'
        result=recover(raw)
        self.assertTrue(result['ok']);self.assertEqual(result['status'],'strict')
        self.assertEqual(result['normalized_json'],raw)
        self.assertEqual(result['audit']['edits'],[])
    def test_single_fence_preserves_outside_as_non_fact(self):
        result=recover('Text before\n```json\n{"id":12}\n```\nText after')
        self.assertTrue(result['ok']);self.assertEqual(result['value'],{'id':12})
        self.assertTrue(result['audit']['wrapper']['outside_text_is_not_parsed_fact'])
    def test_unescaped_nonterminal_quote_has_audited_escape_only(self):
        raw='{"note":"the "fixture" term"}'
        result=recover(raw)
        self.assertTrue(result['ok']);self.assertEqual(result['status'],'recovered')
        self.assertEqual(result['value'],{'note':'the "fixture" term'})
        self.assertTrue(all(e['operation']=='insert' and e['text']=='\\' for e in result['audit']['edits']))
        self.assertFalse(result['audit']['semantic_repair_claimed'])
    def test_truncation_never_manufactures_closing_container(self):
        self.assertRejected('{"bindings":[{"id":1}')
    def test_duplicate_keys_are_not_silently_overwritten(self):
        self.assertRejected('{"id":1,"id":2}')
    def test_nonfinite_number_is_not_a_legal_fact(self):
        for raw in ('{"v":NaN}','{"v":Infinity}','{"v":1e999}'):
            with self.subTest(raw=raw):self.assertRejected(raw)
    def test_multiple_objects_and_ambiguous_wrapper_rejected(self):
        for raw in ('{"a":1}{"b":2}','Before {"a":1} after [12]'):
            with self.subTest(raw=raw):self.assertRejected(raw)
    def test_budget_rejection_has_no_partial_value(self):
        result=recover('{"a":"long text"}',{'max_chars':5})
        self.assertFalse(result['ok']);self.assertEqual(result['reason'],'input_budget')
        self.assertIsNone(result['value'])
if __name__=='__main__':unittest.main()
