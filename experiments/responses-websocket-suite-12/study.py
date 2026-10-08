"""Frozen 12-case, 60-pair WebSocket study. No SDK or automatic retries."""

import csv, hashlib, json, random, sys, time
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ws_runner as runner
from grade import grade

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
ALLOWANCE = 25.0
RESERVATION_PER_PAIR = 2.0
MAX_PAIRS_IN_FLIGHT = 2


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def save(p, value):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(value, indent=2) + "\n")


def schedule():
    pairs = [
        {
            "case": p.name,
            "repeat": repeat,
            "order": ["native", "single"] if (repeat + i) % 2 else ["single", "native"],
        }
        for i, p in enumerate(sorted((ROOT / "cases").iterdir()))
        for repeat in range(1, 6)
    ]
    random.Random(20261007).shuffle(pairs)
    for i, pair in enumerate(pairs):
        pair["schedule_slot"] = i + 1
    return pairs


def freeze():
    inputs = {}
    for case in sorted(p.name for p in (ROOT / "cases").iterdir()):
        runner.configure(case)
        inputs[case] = {
            m: runner.request_body(m == "native", runner.initial_input())
            for m in ["native", "single"]
        }
    files = (
        list(ROOT.glob("*.py"))
        + [ROOT / "README.md", ROOT / "DESIGN.md"]
        + list((ROOT / "cases").rglob("*.json"))
    )
    value = {
        "kind": "pre-run twelve demo tasks freeze",
        "created_at": runner.now(),
        "model": runner.MODEL,
        "api": "Responses API",
        "transport": "WebSocket",
        "endpoint": runner.ENDPOINT,
        "beta_header": runner.BETA,
        "reasoning": "medium",
        "assessments": 120,
        "pairs": 60,
        "repetitions_per_case": 5,
        "cases": list(inputs),
        "schedule": schedule(),
        "initial_requests": inputs,
        "max_pairs_in_flight": MAX_PAIRS_IN_FLIGHT,
        "execution_policy": "At most two pairs in flight; arms within each pair are sequential in frozen alternating order. One assessment per worker process and one fresh socket per assessment. No reconnect, model retries or replacement trials.",
        "returned_token_observation_allowance_usd": ALLOWANCE,
        "pending_pair_reservation_usd": RESERVATION_PER_PAIR,
        "budget_note": "Admission and returned-usage observation limits, not provider invoice caps. Unknown attempt usage reserves at least USD1. Only returned unique-response usage priced; no per-agent invoice reconciliation.",
        "timeout_seconds": runner.TIMEOUT,
        "max_responses_per_assessment": runner.MAX_RESPONSES,
        "max_output_tokens": 32768,
        "per_attempt_observed_cost_stop_usd": runner.OBSERVED_COST_STOP,
        "resource_ceilings": {
            "depth_below_root": 3,
            "total_agents_including_root": 12,
            "api_max_concurrent_subagents": 3,
            "depth_count": "instruction ceilings plus observed-violation stop; not API hard creation limits",
        },
        "delegation_policy": "Native feature available; use where useful. No mandated delegation, count, split or depth. Zero-child native runs remain in the native-enabled condition.",
        "quality_contract": {
            "facts": "Exact values and types; integer arithmetic reference, null preserved, no bool-number coercion",
            "findings": "Exact expected keys, precision/recall/F1, duplicates and unsupported IDs separate",
            "citations": "Per-fact and per-finding required-source-ID coverage and nonempty derivation; no semantic entailment claim",
            "decision": "Exact critical all-case decision, reported separately",
            "task_success": "Completed report, all facts/findings correct, complete structural citation coverage, no invalid IDs/duplicate/extra facts or findings, correct case and critical decision",
        },
        "reference_validation": "Offline deterministic source replay, independently implemented integer/Decimal arithmetic cross-checks and DFS/permutation schedule checks. Author-built synthetic labels; no external human review or paid model judge.",
        "analysis_contract": "Per-case five-repeat summaries and paired ratios; equal case weighting for overall summaries. Failed attempts retained in all-attempt completion, missing usage stays unknown. Completed-only and successful-both paired views labelled; no winner-only exclusions.",
        "benefit_rule": "Exploratory case signal requires all five pairs complete, all native slots with proven child work, native factual accuracy/finding F1/fact and finding source coverage means no worse, critical-decision success and strict task success counts no worse; median paired native/single time OR cost ratio <=0.85. No significance or general workload claim from this threshold.",
        "baseline_scope": "This study compares only the twelve Responses API WebSocket demo tasks distributed in this repository.",
        "source_sha256": {str(p.relative_to(ROOT)): digest(p) for p in sorted(files)},
        "official_sources": [
            "https://developers.openai.com/api/docs/guides/responses-multi-agent",
            "https://developers.openai.com/api/docs/guides/websocket-mode",
            "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
        ],
    }
    path = ROOT / "STUDY_MANIFEST.json"
    if path.exists():
        old = json.loads(path.read_text())
        value["created_at"] = old["created_at"]
        assert value == old, "Frozen study changed"
    else:
        save(path, value)
    return value


def path_for(case, repeat, mode):
    return ROOT / "results/study" / f"{case}-pair-{repeat:02d}-{mode}-attempt-01.json"


def all_records():
    rows = []
    for p in sorted((ROOT / "results/study").glob("*.json")):
        if ".grading." in p.name:
            continue
        try:
            d = json.loads(p.read_text())
        except ValueError:
            d = {
                "status": "admitted",
                "metrics": {},
                "admission_note": "Initial exclusive record write in progress",
            }
        d["artifact"] = str(p.relative_to(ROOT))
        rows.append(d)
    return rows


def budget():
    known = consumed = 0.0
    for d in all_records():
        m = d.get("metrics", {})
        cost = m.get("estimated_model_usd")
        partial = m.get("observed_estimated_model_usd", 0)
        known += partial
        consumed += (
            cost if cost is not None and m.get("usage_complete") else max(1.0, partial)
        )
    return known, consumed


def execute_pair(pair):
    runner.configure(pair["case"])
    for mode in pair["order"]:
        p = path_for(pair["case"], pair["repeat"], mode)
        if p.exists():
            d = json.loads(p.read_text())
            if d["status"] == "admitted":
                raise RuntimeError(
                    "Unresolved admission; never retry automatically: " + p.name
                )
            continue
        print(
            json.dumps(
                {
                    "starting": p.stem,
                    "case": pair["case"],
                    "repeat": pair["repeat"],
                    "mode": mode,
                    "schedule_slot": pair["schedule_slot"],
                }
            ),
            flush=True,
        )
        d = runner.run(mode == "native", p.stem)
        save(p.with_suffix(".grading.json"), grade(d))
    return {
        "case": pair["case"],
        "repeat": pair["repeat"],
        "schedule_slot": pair["schedule_slot"],
    }


def run():
    manifest = freeze()
    (ROOT / "results/study").mkdir(parents=True, exist_ok=True)
    pairs = manifest["schedule"]
    todo = []
    for pair in pairs:
        existing = [path_for(pair["case"], pair["repeat"], m) for m in pair["order"]]
        if all(
            p.exists()
            and json.loads(p.read_text())["status"] in ("completed", "failed")
            for p in existing
        ):
            continue
        if any(
            p.exists() and json.loads(p.read_text())["status"] == "admitted"
            for p in existing
        ):
            raise RuntimeError("Unresolved admission; stop without retry")
        todo.append(pair)
    pending = {}
    cursor = 0
    stop = None
    with ProcessPoolExecutor(max_workers=MAX_PAIRS_IN_FLIGHT) as pool:
        while cursor < len(todo) or pending:
            while (
                cursor < len(todo)
                and len(pending) < MAX_PAIRS_IN_FLIGHT
                and stop is None
            ):
                known, consumed = budget()
                # Existing records for in-flight pairs already consume reservations; this
                # additional pending reservation is conservative, never subtracting usage.
                if (
                    consumed
                    + len(pending) * RESERVATION_PER_PAIR
                    + RESERVATION_PER_PAIR
                    > ALLOWANCE
                ):
                    stop = "Returned-usage admission allowance reached"
                    break
                pair = todo[cursor]
                cursor += 1
                future = pool.submit(execute_pair, pair)
                pending[future] = pair
            if not pending:
                break
            done, _ = wait(pending, timeout=30, return_when=FIRST_COMPLETED)
            for future in done:
                pair = pending.pop(future)
                try:
                    result = future.result()
                except Exception as exc:
                    stop = "Orchestration error: " + str(exc)
                    save(
                        ROOT / "results/ORCHESTRATION_ERROR.json",
                        {"pair": pair, "error": str(exc), "at": runner.now()},
                    )
                    continue
                known, consumed = budget()
                rows = all_records()
                progress = {
                    "pairs_finished": sum(
                        all(
                            path_for(p["case"], p["repeat"], m).exists()
                            and json.loads(
                                path_for(p["case"], p["repeat"], m).read_text()
                            )["status"]
                            in ("completed", "failed")
                            for m in p["order"]
                        )
                        for p in pairs
                    ),
                    "actual_admissions": len(rows),
                    "reports_completed": sum(d["status"] == "completed" for d in rows),
                    "known_usd": known,
                    "budget_consumed_or_reserved_usd": consumed,
                    "last_pair": result,
                    "at": runner.now(),
                }
                save(ROOT / "results/progress.json", progress)
                print(json.dumps(progress), flush=True)
        known, consumed = budget()
        rows = all_records()
        save(
            ROOT / "results/execution.json",
            {
                "terminal": len(rows) == 120
                and all(d["status"] in ("completed", "failed") for d in rows),
                "actual_admissions": len(rows),
                "known_returned_token_estimate_usd": known,
                "budget_consumed_or_reserved_usd": consumed,
                "stop": stop,
                "at": runner.now(),
            },
        )
        print(
            json.dumps(
                {"execution_finished": len(rows), "stop": stop, "known_usd": known}
            ),
            flush=True,
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--freeze"]:
        freeze()
        print("12 cases, scoring, requests and 60-pair schedule frozen; no API calls.")
    elif sys.argv[1:] == ["--run"]:
        run()
    else:
        raise SystemExit("Choose --freeze or explicitly authorized --run.")
