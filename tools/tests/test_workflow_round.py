import copy
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workflow_round as rounds


def reports():
    return {key: {"criterion": key, "max_score": maximum, "complete": True,
        "inputs_unchanged": True, "model": {"model": "fixed"}, "review_config": {"fixed": True},
        "document_sha256": {f"{mid}.md": "article" for mid in rounds.IDS}, "material_sha256": {"source.pdf": "source"},
        "generation_review_sha256": "fixed",
        "evaluator_sha256": {name: "fixed" for name in [*rounds.EVALUATOR_FILES, "pipeline/generate.py"]},
        "hard_checks_passed": True,
        "results": [{"material_id": mid, "score": maximum * .8, "risk": "低", "pending": []}
            for mid in sorted({"group"} if key == "format" else rounds.IDS)]}
        for key, maximum in rounds.MAXIMA.items()}


class ComparisonTests(unittest.TestCase):
    def test_average_improvement_accepts_one_declining_target_article(self):
        baseline = reports()
        candidate = copy.deepcopy(baseline)
        for entry, score in zip(candidate["expression"]["results"], (15, 18, 18, 18)):
            entry["score"] = score
        result = rounds.compare(baseline, candidate, "expression")
        self.assertTrue(result["numerical_gate_passed"], result["blockers"])
        self.assertTrue(result["requires_content_review"])

    def test_unverified_score_cannot_be_averaged_as_zero_or_full_score(self):
        baseline = reports()
        candidate = copy.deepcopy(baseline)
        candidate["accuracy"]["results"][0].update(score=None, pending=["未覆盖"])
        result = rounds.compare(baseline, candidate, "accuracy")
        self.assertFalse(result["numerical_gate_passed"])
        self.assertTrue(any("有效完整评分" in item for item in result["blockers"]))

    def test_changed_judge_and_other_metric_regression_block_promotion(self):
        baseline = reports()
        candidate = copy.deepcopy(baseline)
        for entry in candidate["expression"]["results"]:
            entry["score"] = 18
        for entry in candidate["accuracy"]["results"]:
            entry["score"] = 10
        candidate["coverage"]["evaluator_sha256"]["pipeline/criterion.py"] = "changed"
        result = rounds.compare(baseline, candidate, "expression")
        self.assertFalse(result["numerical_gate_passed"])
        self.assertTrue(any("平均降幅" in item for item in result["blockers"]))
        self.assertTrue(any("评审依赖已变化" in item for item in result["blockers"]))

    def test_full_format_score_is_maintained_and_requires_rejection_checks(self):
        baseline = reports()
        baseline["format"]["results"][0]["score"] = 10
        candidate = copy.deepcopy(baseline)
        self.assertFalse(rounds.compare(baseline, candidate, "format")["numerical_gate_passed"])
        candidate["format"]["rejection_checks"] = {"passed": True, "tests_run": 25,
            "evaluator_sha256": {name: "fixed" for name in ("pipeline/contracts.py", "pipeline/validate.py", "pipeline/package.py")}}
        self.assertTrue(rounds.compare(baseline, candidate, "format")["numerical_gate_passed"])
        candidate["format"]["results"][0]["score"] = 9.5
        result = rounds.compare(baseline, candidate, "format")
        self.assertFalse(result["numerical_gate_passed"])
        self.assertTrue(any("须保持10/10" in item for item in result["blockers"]))

    def test_generation_tuning_does_not_change_review_fingerprint_but_validator_edits_do(self):
        path = Path(__file__).resolve().parents[2] / "1_优化源码/source_code/pipeline/generate.py"
        source = path.read_text(encoding="utf-8")
        original = rounds.generation_review_fingerprint(source)
        tuned = source.replace("target_words: int,", "target_words: int = 1800,", 1)
        self.assertNotEqual(source, tuned)
        self.assertEqual(original, rounds.generation_review_fingerprint(tuned))
        changed_validator = source.replace("len(quote.strip()) < 8", "len(quote.strip()) < 12", 1)
        self.assertNotEqual(source, changed_validator)
        self.assertNotEqual(original, rounds.generation_review_fingerprint(changed_validator))

    def test_generation_logic_can_change_while_review_helpers_remain_fixed(self):
        baseline = reports()
        candidate = copy.deepcopy(baseline)
        for report in candidate.values():
            report["evaluator_sha256"]["pipeline/generate.py"] = "new-writing-logic"
        for entry in candidate["expression"]["results"]:
            entry["score"] = 18
        self.assertTrue(rounds.compare(baseline, candidate, "expression")["numerical_gate_passed"])
        candidate["accuracy"]["generation_review_sha256"] = "changed-quote-validator"
        result = rounds.compare(baseline, candidate, "expression")
        self.assertFalse(result["numerical_gate_passed"])
        self.assertTrue(any("校验、分片或模型客户端已变化" in item for item in result["blockers"]))


if __name__ == "__main__":
    unittest.main()
