"""Offline, post-hoc calibration of the frozen source-ID checklist.

Never changes the original grader, answer keys, requests, run records or scores.
The seven correction rules are source-derived and apply identically to both arms.
This is a dependency checklist, not a semantic-entailment or prose-quality judge.
"""

import argparse
import collections
import copy
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade

ROOT = Path(__file__).resolve().parent
RULES = {
    "cancelled_billing": "Cancellation and posted billing depend on policy, orders and ledger, not fulfillment.",
    "no_duplicate_candidate": "If each order has at most one distinct posted invoice by cutoff, the duplicate rule cannot hold; expected-value sources are unnecessary.",
    "effective_contract_field": "For an individual requirement, cite policy and the document governing that named field at cutoff; other fields' amendments and superseded annex values are unnecessary.",
    "stale_duration_witness": "A baseline and an executed duration change for the same task establish a stale baseline without the task graph.",
    "calendar_capacity_certificate": "If the global calendar has fewer distinct starts than required hubs, policy and calendar alone establish infeasibility and its mandated nulls.",
    "report_dependency": "A derived completion/deadline fact or negative all-hubs decision may reuse exact, source-supported prerequisite facts in the same report, with its own policy and directly used task/deadline sources.",
    "deadline_impossibility_witness": "A hub's unconstrained earliest completion beyond its deadline proves not all hubs can open on time without a calendar citation.",
}


def read_case(case):
    folder = ROOT / "cases" / case
    index = json.loads((folder / "index.json").read_text())
    docs = {
        x["id"]: json.loads((folder / x["path"]).read_text())
        for x in index["documents"]
    }
    oracle = json.loads((folder / "oracle.json").read_text())
    return index, docs, oracle


def strict(g):
    return bool(
        g.get("completed")
        and all(
            g.get(k) == 100
            for k in (
                "factual_accuracy_pct",
                "finding_f1_pct",
                "required_fact_source_coverage_pct",
                "required_finding_source_coverage_pct",
                "finding_precision_pct",
                "finding_recall_pct",
            )
        )
        and g.get("case_id_correct")
        and g.get("critical_decision_correct")
        and not any(
            g.get(k)
            for k in (
                "invalid_source_ids",
                "duplicate_facts",
                "duplicate_findings",
                "unexpected_facts",
                "finding_false_positives",
            )
        )
    )


def exact(actual, expected):
    if type(actual) in (int, float) and type(expected) in (int, float):
        return actual == expected
    return type(actual) is type(expected) and actual == expected


def requirements(index, docs, oracle, kind, key):
    """Return acceptable direct bundles plus a source-checked correction reason."""
    original = oracle["required_sources" if kind == "fact" else "finding_sources"][key]
    bundles = [set(original)]
    rule = None
    entity, field = key.split(".", 1)
    prefix = entity.upper()
    if index["domain"] == "operations" and entity != "all":
        if (
            field in ("cancelled_billed_orders", "cancelled_billed")
            and prefix + "-ORDERS" in docs
            and docs[prefix + "-LEDGER"]["complete"]
        ):
            bundles = [{"OPS-POLICY", prefix + "-ORDERS", prefix + "-LEDGER"}]
            rule = "cancelled_billing"
        elif (
            kind == "fact"
            and field == "duplicate_invoice_orders"
            and oracle["facts"][key] == 0
        ):
            ledger = docs[prefix + "-LEDGER"]
            invoices = collections.defaultdict(set)
            for row in ledger["invoices"]:
                if (
                    row["status"] == "posted"
                    and row["day"] <= docs["OPS-POLICY"]["cutoff_day"]
                ):
                    invoices[row["order_id"]].add(row["invoice_id"])
            if ledger["complete"] and all(len(ids) <= 1 for ids in invoices.values()):
                bundles = [{"OPS-POLICY", prefix + "-LEDGER"}]
                rule = "no_duplicate_candidate"
    elif index["domain"] == "procurement" and entity != "all":
        name = field.removesuffix("_pass") if kind == "fact" else field.split(".")[0]
        mapping = {
            "eu_processing": "eu_only",
            "uptime": "uptime_bps",
            "capacity": "capacity",
            "delivery": "delivery_weeks",
            "contractual_rto": "contractual_rto_minutes",
        }
        if name in mapping:
            source = prefix + "-ANNEX"
            for amendment in sorted(
                docs[prefix + "-AMENDMENTS"]["amendments"],
                key=lambda a: a["effective_day"],
            ):
                if (
                    amendment["status"] == "executed"
                    and amendment["effective_day"] <= docs["BUY-POLICY"]["cutoff_day"]
                    and mapping[name] in amendment["fields"]
                ):
                    source = prefix + "-AMENDMENTS"
            bundles = [{"BUY-POLICY", source}]
            rule = "effective_contract_field"
    elif index["domain"] == "planning":
        calendar = docs["PLAN-CALENDAR"]
        capacity_proof = len(set(calendar["available_starts"])) < len(
            calendar["hub_order"]
        )
        if kind == "finding" and field == "stale_duration":
            current = {}
            for change in sorted(
                docs[prefix + "-CHANGES"]["entries"], key=lambda a: a["effective_day"]
            ):
                if change["status"] == "executed":
                    current.update(change["durations"])
            if any(
                docs[prefix + "-BASELINE"]["durations"].get(task) != duration
                for task, duration in current.items()
            ):
                bundles = [{"PLAN-POLICY", prefix + "-BASELINE", prefix + "-CHANGES"}]
                rule = "stale_duration_witness"
        elif capacity_proof and (
            kind == "finding"
            and key == "shared.no_feasible_schedule"
            or kind == "fact"
            and (
                entity != "all"
                and field
                in (
                    "inspection_start",
                    "completion_day",
                    "deadline_slack",
                    "deadline_met",
                )
                and oracle["facts"][key] is None
                or key
                in ("all.makespan", "all.sum_completion_days", "all.open_all_on_time")
            )
        ):
            bundles = [{"PLAN-POLICY", "PLAN-CALENDAR"}]
            rule = "calendar_capacity_certificate"
        elif (
            kind == "fact"
            and key == "all.open_all_on_time"
            and oracle["facts"][key] is False
        ):
            for hub in calendar["hub_order"]:
                if (
                    oracle["facts"][hub + ".unconstrained_completion"]
                    > docs[hub.upper() + "-TASKS"]["deadline"]
                ):
                    bundles.append(
                        {
                            "PLAN-POLICY",
                            hub.upper() + "-TASKS",
                            hub.upper() + "-CHANGES",
                        }
                    )
                    rule = "deadline_impossibility_witness"
    return bundles, rule


def calibrate(record, original=None):
    index, docs, oracle = read_case(record["case_id"])
    original = original if original is not None else grade.grade(record)
    output = record.get("output") or {}
    facts = {row["name"]: row for row in output.get("facts", [])}
    audit = []
    support = {}
    fact_reviews = {}
    for kind, rows in (
        ("fact", output.get("facts", [])),
        ("finding", output.get("findings", [])),
    ):
        expected = oracle["required_sources" if kind == "fact" else "finding_sources"]
        for row in rows:
            key = row["name" if kind == "fact" else "key"]
            if key not in expected:
                continue
            cited = set(row["evidence_ids"])
            text = row["derivation" if kind == "fact" else "explanation"]
            bundles, rule = requirements(index, docs, oracle, kind, key)
            original_ok = bool(text.strip()) and set(expected[key]) <= cited
            ok = bool(text.strip()) and any(bundle <= cited for bundle in bundles)
            review = dict(
                kind=kind,
                key=key,
                original_supported=original_ok,
                calibrated_supported=ok,
                rule=rule or "unchanged",
                cited_ids=sorted(cited),
                original_missing_ids=sorted(set(expected[key]) - cited),
                acceptable_bundles=[sorted(bundle) for bundle in bundles],
                remaining_missing_options=[
                    sorted(bundle - cited) for bundle in bundles
                ],
                dependency_facts=[],
            )
            audit.append(review)
            if kind == "fact":
                support[key] = ok
                fact_reviews[key] = review
    # Accept local derivations only with the actual exact, supported upstream fact.
    # No report-wide union of citations and no borrowing from another assessment.
    if index["domain"] == "planning":
        for suffix, upstream in (
            ("completion_day", "inspection_start"),
            ("deadline_slack", "completion_day"),
            ("deadline_met", "deadline_slack"),
        ):
            for key, row in facts.items():
                entity, field = key.split(".", 1)
                if (
                    entity == "all"
                    or field != suffix
                    or key not in fact_reviews
                    or support[key]
                    or oracle["facts"][key] is None
                ):
                    continue
                dep = entity + "." + upstream
                required = {"PLAN-POLICY", entity.upper() + "-TASKS"}
                if suffix == "completion_day":
                    # Inspection readiness/calendar feasibility is already supported by the
                    # upstream fact; only inspection and its successors determine finish.
                    tasks = {
                        t["id"]: t for t in docs[entity.upper() + "-TASKS"]["tasks"]
                    }
                    downstream = {"inspection"}
                    while True:
                        expanded = downstream | {
                            name
                            for name, task in tasks.items()
                            if set(task["predecessors"]) & downstream
                        }
                        if expanded == downstream:
                            break
                        downstream = expanded
                    if any(
                        change["status"] == "executed"
                        and set(change["durations"]) & downstream
                        for change in docs[entity.upper() + "-CHANGES"]["entries"]
                    ):
                        required.add(entity.upper() + "-CHANGES")
                # The derivation must actually describe the local completion/slack calculation.
                terms = (
                    ("inspection",)
                    if suffix == "completion_day"
                    else (
                        ("completion", "opening", "finish")
                        if suffix == "deadline_slack"
                        else ("slack", "completion", "deadline")
                    )
                )
                if (
                    dep in facts
                    and support.get(dep)
                    and exact(facts[dep]["value"], oracle["facts"][dep])
                    and required <= set(row["evidence_ids"])
                    and row["derivation"].strip()
                    and any(term in row["derivation"].lower() for term in terms)
                ):
                    support[key] = True
                    fact_reviews[key].update(
                        calibrated_supported=True,
                        rule="report_dependency",
                        dependency_facts=[dep],
                        remaining_missing_options=[],
                    )
        key = "all.open_all_on_time"
        if key in facts and not support[key] and oracle["facts"][key] is False:
            row = facts[key]
            for hub in docs["PLAN-CALENDAR"]["hub_order"]:
                dep = hub + ".deadline_met"
                if (
                    dep in facts
                    and support.get(dep)
                    and facts[dep]["value"] is False
                    and oracle["facts"][dep] is False
                    and {"PLAN-POLICY", hub.upper() + "-TASKS"}
                    <= set(row["evidence_ids"])
                    and any(
                        term in row["derivation"].lower()
                        for term in ("deadline", "slack")
                    )
                ):
                    support[key] = True
                    fact_reviews[key].update(
                        calibrated_supported=True,
                        rule="report_dependency",
                        dependency_facts=[dep],
                        remaining_missing_options=[],
                    )
                    break
    calibrated = copy.deepcopy(original)
    calibrated["required_fact_source_coverage_pct"] = (
        100 * sum(support.values()) / len(oracle["facts"])
    )
    findings_supported = {
        x["key"] for x in audit if x["kind"] == "finding" and x["calibrated_supported"]
    }
    calibrated["required_finding_source_coverage_pct"] = (
        100 * len(findings_supported) / len(oracle["findings"])
        if oracle["findings"]
        else 100
    )
    calibrated["citation_note"] = (
        "Post-hoc, source-derived dependency checklist with explicit local-fact dependencies; not independent semantic entailment."
    )
    return dict(
        run_id=record["run_id"],
        case=record["case_id"],
        mode=record["mode"],
        original_strict_success=strict(original),
        calibrated_strict_success=strict(calibrated),
        original=original,
        calibrated=calibrated,
        claims=audit,
    )


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def protected_files():
    return sorted(
        set(
            list((ROOT / "results/study").glob("*"))
            + list((ROOT / "cases").rglob("*.json"))
            + [
                ROOT / "STUDY_MANIFEST.json",
                ROOT / "grade.py",
                ROOT / "reference.py",
                ROOT / "protocol.py",
                ROOT / "ws_runner.py",
                ROOT / "report.py",
                ROOT / "study.py",
                ROOT / "test_suite.py",
                ROOT / "analysis/analyze.py",
                ROOT / "analysis/comparison.json",
                ROOT / "analysis/runs.csv",
                ROOT / "analysis/citation-gaps.json",
                ROOT / "analysis/verification.json",
            ]
        )
    )


def comparison_view(reviews, original):
    """Create a separate current-score view; never rewrite the frozen comparison."""
    from analysis.analyze import summarize, QKEYS, mean

    current = copy.deepcopy(original)
    lookup = {r["run_id"]: r for r in reviews}
    assert set(lookup) == {r["run_id"] for r in current["runs"]}
    for row in current["runs"]:
        review = lookup[row["run_id"]]
        row["strict_success"] = review["calibrated_strict_success"]
        for key in QKEYS:
            row[key] = review["calibrated"][key]
    for pair in current["pairs"]:
        for mode in ("single", "native"):
            pair[mode + "_strict_success"] = lookup[pair[mode + "_run_id"]][
                "calibrated_strict_success"
            ]
    for case, value in current["by_case"].items():
        rows = [r for r in current["runs"] if r["case"] == case]
        value["arms"] = {
            m: summarize([r for r in rows if r["mode"] == m])
            for m in ("single", "native")
        }
        n, s = value["arms"]["native"], value["arms"]["single"]
        valid = value["paired_completed"] == 5 and n["native_activated"] == 5
        value["quality_no_worse"] = bool(
            valid
            and all(n["quality_means"][k] >= s["quality_means"][k] for k in QKEYS)
            and n["critical_decision_success"] >= s["critical_decision_success"]
            and n["strict_success"] >= s["strict_success"]
        )
        value["exploratory_benefit_signal"] = bool(
            value["quality_no_worse"]
            and any(
                value[k] is not None and value[k] <= 0.85
                for k in ("median_paired_time_ratio", "median_paired_cost_ratio")
            )
        )
    current["all_attempts"] = {
        m: summarize([r for r in current["runs"] if r["mode"] == m])
        for m in ("single", "native")
    }
    for mode in ("single", "native"):
        current["equal_case_weighted"][mode]["quality_means"] = {
            k: mean(
                [
                    v["arms"][mode]["quality_means"][k]
                    for v in current["by_case"].values()
                ]
            )
            for k in QKEYS
        }
    current["benefit_cases"] = [
        c for c, v in current["by_case"].items() if v["exploratory_benefit_signal"]
    ]
    current["scoring"] = "citation-dependencies-posthoc-v1"
    current["quality_note"] = (
        "Exact facts, finding keys and source-derived dependencies including supported local prerequisite facts; no independent semantic review."
    )
    current["calibration_note"] = (
        "Calibrated offline on the retained reports. Exploratory screening uses these current scores; original comparison remains unchanged."
    )
    return current


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "analysis/citation-calibration"
    )
    args = parser.parse_args()
    protected = {
        str(p.relative_to(ROOT)): file_hash(p) for p in protected_files() if p.is_file()
    }
    reviews = []
    for path in sorted((ROOT / "results/study").glob("*-attempt-01.json")):
        record = json.loads(path.read_text())
        saved = json.loads(path.with_suffix(".grading.json").read_text())
        assert (
            grade.grade(record) == saved
        ), "The retained original grade must reproduce exactly."
        reviews.append(calibrate(record, saved))
    if not reviews:
        raise SystemExit(
            "No saved study reports. This command never creates paid runs."
        )
    summaries = {}
    for arm in ("single", "native"):
        rows = [r for r in reviews if r["mode"] == arm]
        content = (
            lambda g: g["factual_accuracy_pct"] == 100
            and g["finding_f1_pct"] == 100
            and g["critical_decision_correct"]
        )
        case_means = {}
        for key in (
            "required_fact_source_coverage_pct",
            "required_finding_source_coverage_pct",
        ):
            case_means[key] = sum(
                sum(r["calibrated"][key] for r in rows if r["case"] == c)
                / sum(r["case"] == c for r in rows)
                for c in {r["case"] for r in rows}
            ) / len({r["case"] for r in rows})
        summaries[arm] = dict(
            attempts=len(rows),
            original_strict_success=sum(r["original_strict_success"] for r in rows),
            calibrated_strict_success=sum(r["calibrated_strict_success"] for r in rows),
            correct_content=sum(content(r["original"]) for r in rows),
            original_source_only_failures=sum(
                content(r["original"]) and not r["original_strict_success"]
                for r in rows
            ),
            calibrated_source_only_failures=sum(
                content(r["calibrated"]) and not r["calibrated_strict_success"]
                for r in rows
            ),
            equal_case_source_scores=case_means,
        )
    gaps = [
        dict(run_id=r["run_id"], case=r["case"], mode=r["mode"], **claim)
        for r in reviews
        for claim in r["claims"]
        if not claim["original_supported"]
    ]
    after = {
        str(p.relative_to(ROOT)): file_hash(p) for p in protected_files() if p.is_file()
    }
    assert after == protected, "Original evidence or scoring changed."
    summary = dict(
        rubric="citation-dependencies-posthoc-v1",
        status="post-hoc calibration on existing reports",
        api_calls=0,
        rules=RULES,
        script_sha256=file_hash(Path(__file__)),
        arms=summaries,
        original_gap_patterns=len(
            {
                (x["case"], x["kind"], x["key"], tuple(x["original_missing_ids"]))
                for x in gaps
            }
        ),
        original_gap_claims=len(gaps),
        resolved_gap_claims=sum(x["calibrated_supported"] for x in gaps),
        remaining_gap_claims=sum(not x["calibrated_supported"] for x in gaps),
        resolved_by_rule=dict(
            collections.Counter(x["rule"] for x in gaps if x["calibrated_supported"])
        ),
        limitations=[
            "Rules calibrated on these same reports; not independent held-out validation or human review.",
            "Correct facts and IDs do not prove semantic entailment or prose quality.",
            "Unchanged checklist rules, including aggregate policy-ID shortcuts, remain inherited from the original rubric.",
            "The frozen exploratory benefit rule, timing, costs and all original scores remain unchanged.",
        ],
        preservation=dict(
            passed=True, protected_files=len(protected), source_sha256=protected
        ),
    )
    args.output.mkdir(parents=True, exist_ok=True)
    current = comparison_view(
        reviews, json.loads((ROOT / "analysis/comparison.json").read_text())
    )
    for name, data in (
        ("summary.json", summary),
        ("reports.json", reviews),
        ("gap-review.json", gaps),
        ("comparison.json", current),
    ):
        (args.output / name).write_text(json.dumps(data, indent=2) + "\n")
    lines = [
        "# Citation rubric calibration\n",
        "Offline post-hoc analysis of the same 120 retained reports. No API calls. Original frozen scores, evidence and benefit screen are unchanged.\n",
        "| Configuration | Original strict pass | Calibrated strict pass | Correct facts/findings/decision | Remaining source-only failures |\n| --- | ---: | ---: | ---: | ---: |\n",
    ]
    for arm, label in (("single", "Single agent"), ("native", "Subagents enabled")):
        s = summaries[arm]
        lines.append(
            f"| {label} | {s['original_strict_success']}/{s['attempts']} | {s['calibrated_strict_success']}/{s['attempts']} | {s['correct_content']}/{s['attempts']} | {s['calibrated_source_only_failures']}/{s['attempts']} |\n"
        )
    lines += [
        f"\nReviewed {summary['original_gap_patterns']} distinct original gap patterns ({len(gaps)} claim occurrences); resolved {summary['resolved_gap_claims']}, retained {summary['remaining_gap_claims']}.\n",
        "\n## Rules\n",
    ]
    lines += [f"- `{key}`: {value}\n" for key, value in RULES.items()]
    lines += [
        "\n## Interpretation\n",
        "The calibrated metric still requires perfect factual, finding and decision scores and all other frozen structural checks. Only source dependencies change. Missing effective-duration sources remain failures when no supported local fact supplies them. No factual error earns credit from a citation correction.\n",
        "\n" + "\n".join("- " + x for x in summary["limitations"]) + "\n",
        f"\nSHA-256 preservation verified for {len(protected)} original files. Per-report claims, accepted bundles, dependency links and remaining gaps are in reports.json and gap-review.json.\n",
    ]
    (args.output / "CALIBRATION.md").write_text("".join(lines))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in (
                    "arms",
                    "original_gap_patterns",
                    "original_gap_claims",
                    "resolved_gap_claims",
                    "remaining_gap_claims",
                    "resolved_by_rule",
                )
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
