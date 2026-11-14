"""Offline checks of the deterministic strategy-target matching and eval helpers.

No model calls: `strategy.relevant` is stubbed out; only the deterministic
path of `strategy.targets` is exercised.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import strategy  # noqa: E402
from eval6d_full import SUBJ_MAP, val_norm  # noqa: E402


class TestStrategyTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        strategy.STRAT_PATH = str(ROOT / "data" / "strategies_frozen10.jsonl")
        strategy.relevant = lambda *a, **k: []  # 离线：跳过模型相关性

    def test_hemoglobin_target(self):
        out = strategy.targets("血红蛋白", "血红蛋白", "Measurement")
        self.assertIn("hemoglobin [mass/volume] in blood", out)

    def test_cough_target(self):
        out = strategy.targets("咳痰", "咳痰", "Condition")
        self.assertIn("productive cough", out)

    def test_fasting_glucose_target(self):
        out = strategy.targets("空腹血糖", "空腹血糖", "Measurement")
        self.assertIn("fasting blood glucose measurement", out)

    def test_no_strategy_no_targets(self):
        out = strategy.targets("体温", "体温", "Measurement")
        self.assertEqual(out, [])


class TestEvalHelpers(unittest.TestCase):
    def test_val_norm(self):
        self.assertEqual(val_norm("165mmHg"), val_norm("165"))
        self.assertEqual(val_norm("12.5×10^9/L"), val_norm("12.5"))
        self.assertEqual(val_norm(None), None)

    def test_subject_map(self):
        self.assertEqual(SUBJ_MAP["患者"], SUBJ_MAP["患儿"])
        self.assertEqual(SUBJ_MAP["患者家属"], SUBJ_MAP["患者父亲"])
        self.assertNotEqual(SUBJ_MAP["患者"], SUBJ_MAP["胎儿"])


if __name__ == "__main__":
    unittest.main()
