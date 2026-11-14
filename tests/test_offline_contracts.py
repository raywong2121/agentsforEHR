"""Offline module checks. No model calls, no database access, no data files.

These tests verify that the shipped source imports cleanly and that the
documented prompt roles and deterministic rules are present. They are
engineering contract checks, NOT clinical validation.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


class TestModulesImport(unittest.TestCase):
    def test_core_modules(self):
        import eval6d
        import eval6d_full
        import pipeline
        import retrieve
        import strategy
        self.assertTrue(all([eval6d, eval6d_full, pipeline, retrieve, strategy]))

    def test_eval_helpers(self):
        from eval6d_full import SUBJ_MAP, val_norm
        self.assertEqual(val_norm("165mmHg"), val_norm("165"))
        self.assertEqual(val_norm("12.5×10^9/L"), val_norm("12.5"))
        self.assertEqual(val_norm(None), None)
        self.assertEqual(SUBJ_MAP["患者"], SUBJ_MAP["患儿"])
        self.assertEqual(SUBJ_MAP["患者家属"], SUBJ_MAP["患者父亲"])
        self.assertNotEqual(SUBJ_MAP["患者"], SUBJ_MAP["胎儿"])

    def test_strategy_targets_deterministic_path(self):
        import json
        import tempfile
        import strategy

        fixtures = [
            {"name": "hemoglobin recall", "trigger": "hemoglobin with value",
             "action": "prefer the LOINC name", "tags": ["lab"],
             "examples": ["hemoglobin -> Hemoglobin [Mass/volume] in Blood"],
             "targets": ["hemoglobin [mass/volume] in blood"]},
            {"name": "productive cough", "trigger": "cough with sputum",
             "action": "pick the symptom, not the substance", "tags": ["symptom"],
             "examples": ["cough -> Productive cough"],
             "targets": ["productive cough"]},
        ]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "strategies.jsonl"
            path.write_text(
                "\n".join(json.dumps(s, ensure_ascii=False) for s in fixtures),
                encoding="utf-8")
            strategy.STRAT_PATH = str(path)
            strategy.relevant = lambda *a, **k: []  # offline: skip model relevance
            out = strategy.targets("hemoglobin", "hemoglobin", "Measurement")
            self.assertIn("hemoglobin [mass/volume] in blood", out)
            out2 = strategy.targets("cough", "cough", "Condition")
            self.assertIn("productive cough", out2)
            # no matching strategy -> no targets
            self.assertEqual(strategy.targets("temperature", "temperature", "Measurement"), [])


class TestPromptDoc(unittest.TestCase):
    def test_prompts_doc_lists_rules(self):
        doc = (ROOT / "prompts" / "PROMPTS.md").read_text(encoding="utf-8")
        for rule in ("Abstention veto", "Generic bare-domain rejection",
                     "Gene-variant blacklist", "Site convention"):
            self.assertIn(rule, doc)

    def test_pipeline_has_four_prompt_roles(self):
        import pipeline
        import strategy
        self.assertTrue(pipeline.EXTRACT_SYS)
        self.assertTrue(pipeline.REFORMULATE_PROMPT)
        self.assertTrue(pipeline.VERIFY_PROMPT)
        self.assertTrue(strategy.SELECT_PROMPT)


if __name__ == "__main__":
    unittest.main()
