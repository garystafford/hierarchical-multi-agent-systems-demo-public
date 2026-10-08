"""Offline adversarial checks for the post-hoc citation dependency rubric."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import citation_calibration as calibration

ROOT = Path(__file__).resolve().parent


def report(case):
    oracle = json.loads((ROOT / "cases" / case / "oracle.json").read_text())
    return {
        "case_id": case,
        "run_id": "offline-" + case,
        "mode": "single",
        "status": "completed",
        "output": {
            "case_id": case,
            "facts": [
                {
                    "name": k,
                    "value": v,
                    "evidence_ids": list(oracle["required_sources"][k]),
                    "derivation": "Computed from authoritative sources.",
                }
                for k, v in oracle["facts"].items()
            ],
            "findings": [
                {
                    "key": k,
                    "evidence_ids": list(oracle["finding_sources"][k]),
                    "explanation": "Authoritative source comparison.",
                }
                for k in oracle["findings"]
            ],
        },
    }


def fact(record, key):
    return next(row for row in record["output"]["facts"] if row["name"] == key)


def claim(review, key):
    return next(row for row in review["claims"] if row["key"] == key)


class CalibrationChecks(unittest.TestCase):
    def test_original_perfect_reports_and_inputs_preserved(self):
        for path in sorted((ROOT / "cases").iterdir()):
            record = report(path.name)
            before = copy.deepcopy(record)
            result = calibration.calibrate(record)
            self.assertTrue(result["original_strict_success"])
            self.assertTrue(result["calibrated_strict_success"])
            self.assertEqual(record, before)

    def test_cancellation_needs_orders_and_ledger_but_not_fulfillment(self):
        record = report("operations-compact")
        row = fact(record, "aspen.cancelled_billed_orders")
        row["evidence_ids"].remove("ASPEN-FULFILLMENT")
        self.assertTrue(calibration.calibrate(record)["calibrated_strict_success"])
        row["evidence_ids"].remove("ASPEN-ORDERS")
        self.assertFalse(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )

    def test_duplicate_negative_certificate_and_factual_error(self):
        record = report("operations-compact")
        row = fact(record, "aspen.duplicate_invoice_orders")
        row["evidence_ids"] = ["OPS-POLICY", "ASPEN-LEDGER"]
        self.assertTrue(calibration.calibrate(record)["calibrated_strict_success"])
        row["value"] = 1
        self.assertFalse(calibration.calibrate(record)["calibrated_strict_success"])

    def test_incomplete_ledger_null_does_not_require_orders(self):
        record = report("operations-uncertain")
        row = fact(record, "juniper.cancelled_billed_orders")
        self.assertIsNone(row["value"])
        self.assertEqual(set(row["evidence_ids"]), {"OPS-POLICY", "JUNIPER-LEDGER"})
        self.assertTrue(calibration.calibrate(record)["calibrated_strict_success"])

    def test_effective_field_requires_governing_document(self):
        record = report("procurement-large")
        row = fact(record, "quartz.capacity_pass")
        row["evidence_ids"] = ["BUY-POLICY", "QUARTZ-AMENDMENTS"]
        self.assertTrue(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )
        row["evidence_ids"] = ["BUY-POLICY", "QUARTZ-ANNEX"]
        self.assertFalse(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )
        row = fact(record, "quartz.uptime_pass")
        row["evidence_ids"] = ["BUY-POLICY", "QUARTZ-ANNEX"]
        self.assertTrue(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )

    def test_future_amendment_is_not_current_authority(self):
        index, docs, oracle = calibration.read_case("procurement-large")
        # EU processing changes only after cutoff: the signed annex governs.
        bundles, _ = calibration.requirements(
            index, docs, oracle, "fact", "quartz.eu_processing_pass"
        )
        self.assertEqual(bundles, [{"BUY-POLICY", "QUARTZ-ANNEX"}])

    def test_stale_baseline_witness_still_needs_executed_change(self):
        record = report("planning-independent")
        row = next(
            x
            for x in record["output"]["findings"]
            if x["key"] == "aurora.stale_duration"
        )
        row["evidence_ids"].remove("AURORA-TASKS")
        self.assertTrue(
            claim(calibration.calibrate(record), row["key"])["calibrated_supported"]
        )
        row["evidence_ids"].remove("AURORA-CHANGES")
        self.assertFalse(
            claim(calibration.calibrate(record), row["key"])["calibrated_supported"]
        )

    def test_calendar_capacity_certificate_requires_calendar(self):
        record = report("planning-uncertain")
        row = fact(record, "harbor.completion_day")
        row["evidence_ids"] = ["PLAN-POLICY", "PLAN-CALENDAR"]
        self.assertTrue(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )
        row["evidence_ids"] = ["PLAN-POLICY"]
        self.assertFalse(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )

    def test_supported_local_dependency_and_broken_chain(self):
        record = report("planning-large")
        row = fact(record, "drift.completion_day")
        row["evidence_ids"] = ["PLAN-POLICY", "DRIFT-TASKS"]
        row["derivation"] = (
            "From the optimized inspection start, add inspection and successor durations."
        )
        result = calibration.calibrate(record)
        self.assertTrue(claim(result, row["name"])["calibrated_supported"])
        self.assertEqual(
            claim(result, row["name"])["dependency_facts"], ["drift.inspection_start"]
        )
        fact(record, "drift.inspection_start")["evidence_ids"].remove("DRIFT-CHANGES")
        self.assertFalse(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )

    def test_wrong_upstream_fact_cannot_support_dependency(self):
        record = report("planning-large")
        row = fact(record, "drift.deadline_slack")
        row["evidence_ids"] = ["PLAN-POLICY", "DRIFT-TASKS"]
        row["derivation"] = "Deadline minus completion."
        fact(record, "drift.completion_day")["value"] += 1
        self.assertFalse(
            claim(calibration.calibrate(record), row["name"])["calibrated_supported"]
        )

    def test_empty_explanation_and_invalid_id_still_fail(self):
        record = report("operations-compact")
        fact(record, "aspen.cancelled_billed_orders")["derivation"] = ""
        self.assertFalse(calibration.calibrate(record)["calibrated_strict_success"])
        record = report("operations-compact")
        fact(record, "aspen.cancelled_billed_orders")["evidence_ids"].append(
            "SECRET-KEY"
        )
        self.assertFalse(calibration.calibrate(record)["calibrated_strict_success"])


if __name__ == "__main__":
    unittest.main()
