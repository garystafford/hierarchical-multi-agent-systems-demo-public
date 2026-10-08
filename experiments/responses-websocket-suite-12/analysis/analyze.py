"""Offline analysis of the frozen twelve-case study; never changes run evidence."""

import csv, hashlib, json, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT.parents[1]
DEST = ROOT / "analysis"
QKEYS = [
    "factual_accuracy_pct",
    "finding_f1_pct",
    "required_fact_source_coverage_pct",
    "required_finding_source_coverage_pct",
]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def mean(xs):
    return statistics.mean(xs) if xs else None


def median(xs):
    return statistics.median(xs) if xs else None


def show(x, digits=1):
    return "unknown" if x is None else f"{x:.{digits}f}"


def strict(g):
    return bool(
        g.get("completed")
        and all(g.get(k) == 100 for k in QKEYS)
        and g.get("finding_precision_pct") == 100
        and g.get("finding_recall_pct") == 100
        and g.get("case_id_correct")
        and g.get("critical_decision_correct")
        and not any(
            g.get(k)
            for k in [
                "invalid_source_ids",
                "duplicate_facts",
                "duplicate_findings",
                "unexpected_facts",
                "finding_false_positives",
            ]
        )
    )


def summarize(rows):
    cc = [r for r in rows if r["completed"]]
    costs = [r["cost_usd"] for r in rows if r["cost_usd"] is not None]
    return dict(
        attempts=len(rows),
        completed=len(cc),
        failed=sum(r["status"] == "failed" for r in rows),
        native_activated=sum(r["activation"] for r in rows),
        children=sum(r["children"] for r in rows),
        children_range=[
            min([r["children"] for r in rows], default=0),
            max([r["children"] for r in rows], default=0),
        ],
        max_depth=max([r["depth"] for r in rows], default=0),
        strict_success=sum(r["strict_success"] for r in rows),
        critical_decision_success=sum(
            r["critical_decision_correct"] is True for r in rows
        ),
        null_value_correct_pct=mean(
            [
                r["null_value_correct_pct"]
                for r in cc
                if r["null_value_correct_pct"] is not None
            ]
        ),
        quality_means={k: mean([r[k] for r in cc if r[k] is not None]) for k in QKEYS},
        mean_report_seconds=mean(
            [r["report_seconds"] for r in cc if r["report_seconds"] is not None]
        ),
        median_report_seconds=median(
            [r["report_seconds"] for r in cc if r["report_seconds"] is not None]
        ),
        cost_coverage=len(costs),
        known_cost_usd=sum(costs),
        mean_cost_usd=mean(costs),
        usd_per_1000_known_cost_attempts=mean(costs) * 1000 if costs else None,
        tool_errors=sum(r["tool_errors"] for r in rows),
        function_calls=sum(r["function_calls"] for r in rows),
    )


def main():
    DEST.mkdir(exist_ok=True)
    manifest = json.loads((ROOT / "STUDY_MANIFEST.json").read_text())
    rows = []
    hashes = {}
    for p in sorted((ROOT / "results/study").glob("*-attempt-01.json")):
        d = json.loads(p.read_text())
        gpath = p.with_suffix(".grading.json")
        g = json.loads(gpath.read_text()) if gpath.exists() else {}
        m = d.get("metrics", {})
        ev = d.get("evidence", {})
        repeat = int(p.name.split("-pair-")[1].split("-")[0])
        hashes[str(p.relative_to(ROOT))] = sha(p)
        if gpath.exists():
            hashes[str(gpath.relative_to(ROOT))] = sha(gpath)
        rows.append(
            dict(
                case=d["case_id"],
                domain=d["case_id"].split("-")[0],
                profile=d["case_id"].split("-")[1],
                repeat=repeat,
                mode=d["mode"],
                run_id=d["run_id"],
                status=d["status"],
                completed=bool(m.get("completed_report")),
                activation=bool(ev.get("activation_observed")),
                children=ev.get("native_children", 0),
                depth=ev.get("max_depth", 0),
                strict_success=strict(g),
                critical_decision_correct=g.get("critical_decision_correct"),
                null_value_correct_pct=g.get("null_value_correct_pct"),
                report_seconds=(
                    m.get("time_to_report_ms") / 1000
                    if m.get("time_to_report_ms") is not None
                    else None
                ),
                duration_seconds=m.get("total_duration_ms", 0) / 1000,
                cost_usd=(
                    m.get("estimated_model_usd") if m.get("usage_complete") else None
                ),
                observed_known_cost_usd=m.get("observed_estimated_model_usd", 0),
                usage_complete=m.get("usage_complete", False),
                function_calls=m.get("function_calls", 0),
                tool_errors=m.get("tool_error_count", 0),
                error=d.get("error"),
                source=str(p.relative_to(PROJECT)),
                **{k: g.get(k) for k in QKEYS},
                finding_precision_pct=g.get("finding_precision_pct"),
                finding_recall_pct=g.get("finding_recall_pct"),
            )
        )
    bycase = {}
    pairs = []
    for case in manifest["cases"]:
        rr = [r for r in rows if r["case"] == case]
        arms = {
            m: summarize([r for r in rr if r["mode"] == m])
            for m in ["native", "single"]
        }
        pp = []
        for repeat in range(1, 6):
            n = next(
                (r for r in rr if r["repeat"] == repeat and r["mode"] == "native"), None
            )
            s = next(
                (r for r in rr if r["repeat"] == repeat and r["mode"] == "single"), None
            )
            if not n or not s:
                continue
            both = n["completed"] and s["completed"]
            cost_pair = (
                both and n["cost_usd"] is not None and s["cost_usd"] not in (None, 0)
            )
            p = dict(
                case=case,
                repeat=repeat,
                both_completed=both,
                native_run_id=n["run_id"],
                single_run_id=s["run_id"],
                report_time_ratio=(
                    n["report_seconds"] / s["report_seconds"]
                    if both
                    and n["report_seconds"] is not None
                    and s["report_seconds"] not in (None, 0)
                    else None
                ),
                cost_ratio=n["cost_usd"] / s["cost_usd"] if cost_pair else None,
                native_strict_success=n["strict_success"],
                single_strict_success=s["strict_success"],
                native_activation=n["activation"],
            )
            pp.append(p)
            pairs.append(p)
        time_ratios = [
            p["report_time_ratio"] for p in pp if p["report_time_ratio"] is not None
        ]
        cost_ratios = [p["cost_ratio"] for p in pp if p["cost_ratio"] is not None]
        n, s = arms["native"], arms["single"]
        valid = (
            len(pp) == 5
            and sum(p["both_completed"] for p in pp) == 5
            and n["native_activated"] == 5
        )
        quality_no_worse = bool(
            valid
            and all(n["quality_means"][k] >= s["quality_means"][k] for k in QKEYS)
            and n["critical_decision_success"] >= s["critical_decision_success"]
            and n["strict_success"] >= s["strict_success"]
        )
        benefit = bool(
            quality_no_worse
            and (
                (median(time_ratios) is not None and median(time_ratios) <= 0.85)
                or (median(cost_ratios) is not None and median(cost_ratios) <= 0.85)
            )
        )
        bycase[case] = dict(
            arms=arms,
            paired_completed=sum(p["both_completed"] for p in pp),
            time_pair_count=len(time_ratios),
            cost_pair_count=len(cost_ratios),
            median_paired_time_ratio=median(time_ratios),
            median_paired_cost_ratio=median(cost_ratios),
            quality_no_worse=quality_no_worse,
            exploratory_benefit_signal=benefit,
        )
    all_terminal = len(rows) == 120 and all(
        r["status"] in ["completed", "failed"] for r in rows
    )
    overall = {
        m: summarize([r for r in rows if r["mode"] == m]) for m in ["native", "single"]
    }
    balanced = {
        m: dict(
            quality_means={
                k: mean(
                    [
                        v["arms"][m]["quality_means"][k]
                        for v in bycase.values()
                        if v["arms"][m]["quality_means"][k] is not None
                    ]
                )
                for k in QKEYS
            },
            mean_report_seconds=mean(
                [
                    v["arms"][m]["mean_report_seconds"]
                    for v in bycase.values()
                    if v["arms"][m]["mean_report_seconds"] is not None
                ]
            ),
            mean_cost_usd=mean(
                [
                    v["arms"][m]["mean_cost_usd"]
                    for v in bycase.values()
                    if v["arms"][m]["mean_cost_usd"] is not None
                ]
            ),
        )
        for m in ["native", "single"]
    }
    freeze_errors = [
        name for name, h in manifest["source_sha256"].items() if sha(ROOT / name) != h
    ]
    summary = dict(
        study="12 fresh cases, five paired repetitions",
        terminal=all_terminal,
        planned_attempts=120,
        observed_attempts=len(rows),
        manifest_sha256=sha(ROOT / "STUDY_MANIFEST.json"),
        freeze_verified=not freeze_errors,
        freeze_errors=freeze_errors,
        by_case=bycase,
        all_attempts=overall,
        equal_case_weighted=balanced,
        pairs=pairs,
        runs=rows,
        known_returned_usage_estimate_usd=sum(
            r["observed_known_cost_usd"] for r in rows
        ),
        usage_unknown_attempts=sum(not r["usage_complete"] for r in rows),
        benefit_cases=[c for c, v in bycase.items() if v["exploratory_benefit_signal"]],
        cost_note="Unique returned response usage only, no independent per-agent invoice reconciliation. USD/1K is normalized cost per assessment, not per function call or 1,000 executed tasks.",
        quality_note="Exact facts and finding keys plus required source-ID coverage; no semantic citation entailment or prose-quality judge.",
        analysis_note="Five repeats within each frozen demo task; equal case weighting. Paired ratios use both-completed slots. Failures remain in all-attempt denominators.",
        source_sha256=hashes,
    )
    (DEST / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    with (DEST / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
        w.writeheader()
        w.writerows(rows)
    text = [
        "# Twelve-case WebSocket study results\n\n",
        f"Status: {'FINAL' if all_terminal else 'IN PROGRESS'}. {len(rows)} / 120 attempt records. Frozen source verification: {not freeze_errors}.\n\n",
        "Each case has five native/single pairs. Completion and strict success use all attempts; quality and report timing use completed reports. Native enabled permits zero children. Required-source coverage checks IDs and explanations, not semantic entailment.\n\n",
        "| Arm | Completed / attempts | Strict success | Critical decision | Fact % | Finding F1 % | Fact-source % | Finding-source % | Equal-case mean report s | Equal-case USD / 1K known-cost assessments |\n",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n",
    ]
    for m in ["single", "native"]:
        a = overall[m]
        e = balanced[m]
        q = e["quality_means"]
        text.append(
            f"| {m} | {a['completed']}/{a['attempts']} | {a['strict_success']}/{a['attempts']} | {a['critical_decision_success']}/{a['attempts']} | "
            + " | ".join(show(q[k]) for k in QKEYS)
            + f" | {show(e['mean_report_seconds'])} | {show(e['mean_cost_usd']*1000 if e['mean_cost_usd'] is not None else None,2)} |\n"
        )
    text += [
        "\n## Every case\n\n",
        "Pairs in the ratios have two completed reports. A ratio below 1 favors native; missing measurements stay unknown. Time is the retained valid completed root report from assessment start.\n\n",
        "| Case | Complete S/N | Facts S/N % | F1 S/N % | Fact source S/N % | Strict S/N | Native active | Median paired time N/S (n) | Median paired cost N/S (n) | Frozen benefit signal |\n",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |\n",
    ]
    for c, v in bycase.items():
        s, n = v["arms"]["single"], v["arms"]["native"]
        text.append(
            f"| {c} | {s['completed']}/{n['completed']} | {show(s['quality_means'][QKEYS[0]])}/{show(n['quality_means'][QKEYS[0]])} | {show(s['quality_means'][QKEYS[1]])}/{show(n['quality_means'][QKEYS[1]])} | {show(s['quality_means'][QKEYS[2]])}/{show(n['quality_means'][QKEYS[2]])} | {s['strict_success']}/{n['strict_success']} | {n['native_activated']}/{n['attempts']} | {show(v['median_paired_time_ratio'],3)} ({v['time_pair_count']}) | {show(v['median_paired_cost_ratio'],3)} ({v['cost_pair_count']}) | {v['exploratory_benefit_signal']} |\n"
        )
    text += [
        f"\nKnown returned-token estimate: **${summary['known_returned_usage_estimate_usd']:.6f}**, {summary['usage_unknown_attempts']} attempts with incomplete usage. This is not an invoice or verified per-agent billing total.\n\n",
        "The frozen exploratory benefit rule requires five completed pairs and actual child work in all five native runs; mean fact/F1/source scores and critical-decision/strict-success counts must be no worse, with at least a 15% median paired reduction in report time or estimated token cost. Five pairs do not establish statistical significance or production reliability.\n\n",
        "All outputs, errors, exact requests and raw event journals are preserved. No automatic retries or replacements. Source hashes, all run rows, paired ratios and per-case summaries are in [comparison.json](comparison.json); all runs are in [runs.csv](runs.csv). These numbers describe the current local run.\n",
    ]
    (DEST / "RESULTS.md").write_text("".join(text))
    print(
        json.dumps(
            {
                k: summary[k]
                for k in [
                    "terminal",
                    "observed_attempts",
                    "freeze_verified",
                    "known_returned_usage_estimate_usd",
                    "usage_unknown_attempts",
                    "benefit_cases",
                ]
            }
        )
    )


if __name__ == "__main__":
    main()
