"""Offline contract checks. No model calls, no database access.

These tests verify that the shipped data and the evaluation code reproduce the
numbers reported in the paper. They are engineering/data-integrity controls,
NOT clinical validation.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import eval6d_full  # noqa: E402


class TestGoldIntegrity(unittest.TestCase):
    def test_gold_shape(self):
        gold = json.loads((ROOT / "data" / "gold_real100.json").read_text(encoding="utf-8"))
        cases = gold["gold"]
        self.assertEqual(len(cases), 100)
        mentions = [m for c in cases for m in c["mentions"]]
        self.assertEqual(len(mentions), 1013)
        for m in mentions:
            self.assertIn("span", m)
            for field in ("concept_id", "assertion", "subject", "value",
                          "specimen", "site", "site_concept_id"):
                self.assertIn(field, m)
            cid = m.get("concept_id")
            self.assertTrue(cid is None or isinstance(cid, int))
            if m.get("alt_ids"):
                self.assertTrue(all(isinstance(x, int) for x in m["alt_ids"]))
        ids = {c["id"] for c in cases}
        self.assertEqual(ids, {f"R{i:03d}" for i in range(1, 101)})

    def test_strategy_library_shape(self):
        lines = (ROOT / "data" / "strategies_frozen10.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len([l for l in lines if l.strip()]), 1)
        for line in lines:
            if not line.strip():
                continue
            s = json.loads(line)
            for key in ("name", "trigger", "action"):
                self.assertIn(key, s)

    def test_rulings_shape(self):
        rulings = json.loads((ROOT / "data" / "rulings.json").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(rulings), 31)


class TestReportedNumbers(unittest.TestCase):
    def _eval(self, pred, gold):
        with tempfile.TemporaryDirectory() as td:
            old = os.getcwd()
            os.chdir(td)
            try:
                out = eval6d_full.main(str(ROOT / "data" / pred), str(ROOT / "data" / gold))
            finally:
                os.chdir(old)
        return out

    def test_full_system_six_dim_100(self):
        out = self._eval("predictions100_6d.json", "gold_real100.json")
        self.assertEqual(out["overall"]["concept"]["k"], 1013)
        self.assertEqual(out["overall"]["all"]["k"], 1013)
        self.assertEqual(out["overall"]["all"]["n"], 1013)

    def test_zeroshot_baseline(self):
        out = self._eval("predictions_zeroshot.json", "gold_real100.json")
        concept = out["overall"]["concept"]
        # 33.0% concept hit
        self.assertAlmostEqual(concept["k"] / concept["n"], 0.330, delta=0.01)
        self.assertEqual(out["overall"]["all"]["k"], 0)

    def test_fold1_final_version(self):
        out = self._eval("preds_fold1_v10.json", "gold_fold1_final.json")
        self.assertEqual(out["overall"]["all"]["k"], out["overall"]["all"]["n"])
        self.assertEqual(out["overall"]["all"]["n"], 199)


if __name__ == "__main__":
    unittest.main()
