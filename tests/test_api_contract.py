"""3 種類の decision 形式を検査する。"""

import unittest

from kev.api import SystemOneRequest, to_answers, to_record


class DecisionApiContractTest(unittest.TestCase):
    """API 入出力の契約を検査する。"""

    def test_choice_noul_score(self) -> None:
        """choice、yes/no、score を一つの要求で処理できる。"""
        request = SystemOneRequest.model_validate(
            {
                "state": "The customer has a duplicate card charge.",
                "questions": {
                    "route": {"type": "choice", "criteria": {"billing": "Payment", "shipping": "Delivery"}},
                    "billing_issue": {"type": "noul", "instructions": "Is this a billing issue?"},
                    "urgency": {"type": "score", "criteria": ["low", "medium", "high"]},
                },
            }
        )
        record, meta = to_record(request)
        self.assertEqual(len(record["questions"]), 3)
        answers = to_answers([[0.8, 0.2], [0.1, 0.9], [0.1, 0.2, 0.7]], meta)
        self.assertEqual(answers["route"]["choice"], "billing")
        self.assertEqual(answers["billing_issue"]["noul"], 0.9)
        self.assertEqual(answers["urgency"]["score"], 1.6)

    def test_empty_choice_rejected(self) -> None:
        """候補がない choice はモデル実行前に拒否される。"""
        with self.assertRaises(ValueError):
            SystemOneRequest.model_validate(
                {"state": "x", "questions": {"route": {"type": "choice", "criteria": {}}}}
            )


if __name__ == "__main__":
    unittest.main()
