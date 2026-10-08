"""Offline objective facts, finding precision/recall and structural citations."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def grade(record):
    case = record["case_id"]
    p = ROOT / "cases" / case
    oracle = json.loads((p / "oracle.json").read_text())
    idx = json.loads((p / "index.json").read_text())
    allowed = {d["id"] for d in idx["documents"]}
    output = record.get("output") or {}
    rows = output.get("facts", [])
    facts = {}
    duplicates = []
    invalid = []
    for row in rows:
        if row["name"] in facts:
            duplicates.append(row["name"])
        facts[row["name"]] = row
        invalid.extend(i for i in row["evidence_ids"] if i not in allowed)
    correct = []
    wrong = []
    missing = []
    supported = []
    for name, expected in oracle["facts"].items():
        if name not in facts:
            missing.append(name)
            continue
        row = facts[name]
        actual = row["value"]
        equal = type(actual) is type(expected) and actual == expected
        if type(expected) in (int, float) and type(actual) in (int, float):
            equal = actual == expected
        if equal:
            correct.append(name)
        else:
            wrong.append({"name": name, "expected": expected, "actual": actual})
        if (
            set(oracle["required_sources"][name]) <= set(row["evidence_ids"])
            and row["derivation"].strip()
        ):
            supported.append(name)
    reported = [f["key"] for f in output.get("findings", [])]
    expected = set(oracle["findings"])
    actual = set(reported)
    tp = actual & expected
    fp = actual - expected
    fn = expected - actual
    for row in output.get("findings", []):
        invalid.extend(i for i in row["evidence_ids"] if i not in allowed)
    precision = len(tp) / len(actual) if actual else (1.0 if not expected else 0.0)
    recall = len(tp) / len(expected) if expected else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    finding_supported = [
        row["key"]
        for row in output.get("findings", [])
        if row["key"] in expected
        and set(oracle["finding_sources"][row["key"]]) <= set(row["evidence_ids"])
        and row["explanation"].strip()
    ]
    return {
        "completed": record.get("status") == "completed",
        "factual_points": len(correct),
        "factual_total": len(oracle["facts"]),
        "factual_accuracy_pct": 100 * len(correct) / len(oracle["facts"]),
        "correct": correct,
        "wrong": wrong,
        "missing": missing,
        "unexpected_facts": sorted(set(facts) - set(oracle["facts"])),
        "duplicate_facts": duplicates,
        "finding_true_positives": sorted(tp),
        "finding_false_positives": sorted(fp),
        "finding_missed": sorted(fn),
        "finding_precision_pct": 100 * precision,
        "finding_recall_pct": 100 * recall,
        "finding_f1_pct": 100 * f1,
        "duplicate_findings": sorted({x for x in reported if reported.count(x) > 1}),
        "required_fact_source_coverage_pct": 100
        * len(supported)
        / len(oracle["facts"]),
        "required_finding_source_coverage_pct": (
            100 * len(set(finding_supported)) / len(expected) if expected else 100
        ),
        "citation_note": "Source-ID coverage and nonempty derivation/explanation only; semantic entailment and prose quality not independently graded.",
        "invalid_source_ids": sorted(set(invalid)),
        "case_id_correct": output.get("case_id") == case,
        "critical_decision_correct": all(
            k in facts and type(facts[k]["value"]) is type(v) and facts[k]["value"] == v
            for k, v in oracle["facts"].items()
            if k.startswith("all.")
            and k in ("all.hold_billing", "all.selected_vendor", "all.open_all_on_time")
        ),
        "null_value_correct_pct": (
            100
            * sum(
                k in facts and facts[k]["value"] is None
                for k, v in oracle["facts"].items()
                if v is None
            )
            / max(1, sum(v is None for v in oracle["facts"].values()))
            if any(v is None for v in oracle["facts"].values())
            else None
        ),
    }
