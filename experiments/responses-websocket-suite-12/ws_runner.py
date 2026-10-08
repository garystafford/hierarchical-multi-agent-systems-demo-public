"""Direct Responses WebSocket protocol; no OpenAI/Agents SDK, no retries."""

import json, sys, time
from pathlib import Path
from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
sys.path.insert(0, str(ROOT))
import protocol as h
from report import Report

ENDPOINT = "wss://api.openai.com/v1/responses"
MODEL = h.MODEL
BETA = h.BETA
TIMEOUT = h.TIMEOUT
MAX_RESPONSES = h.MAX_RESPONSES
OBSERVED_COST_STOP = h.OBSERVED_COST_STOP
RATES = h.RATES
sha = h.sha
save = h.save
now = h.now


def configure(case):
    h.FIXTURE = ROOT / "cases" / case
    index = json.loads((h.FIXTURE / "index.json").read_text())
    h.TASK = (
        index["task"]
        + "\nCase ID: "
        + case
        + ". Return every requested fact using its exact name.\nRequested fact names: "
        + json.dumps(index["requested_fact_names"])
        + "\nFinding keys: "
        + index["finding_key_format"]
    )


def initial_input():
    return h.initial_input()


def request_body(enabled, history, previous=None):
    body = h.request_body(enabled, history)
    body.pop("stream")
    body["type"] = "response.create"
    if previous:
        body["previous_response_id"] = previous
    return body


class Collector:
    """Retain output across responses; execute only developer function calls."""

    def __init__(self, enabled, execute=h.execute_tool):
        self.enabled = enabled
        self.execute = execute
        self.items = {}
        self.seen_calls = set()
        self.pending = 0
        self.inject_sent = 0
        self.inject_acked = 0
        self.inject_failed = 0
        self.response_id = None
        self.terminal = None
        self.response = {}
        self.next_input = []
        self.report = None
        self.report_text = None
        self.report_ms = None
        self.tool_calls = []
        self.tool_errors = []
        self.validation_errors = []

    def begin_response(self):
        assert self.pending == 0
        self.response_id = None
        self.terminal = None
        self.response = {}
        self.next_input = []

    def retain(self, item, elapsed):
        self.items[item["id"]] = item
        text = h.root_text(list(self.items.values()), self.enabled)
        if text and text != self.report_text:
            try:
                report = Report.model_validate_json(text).model_dump()
            except Exception as exc:
                self.validation_errors.append(
                    {"elapsed_ms": elapsed, "type": type(exc).__name__}
                )
            else:
                self.report = report
                self.report_text = text
                self.report_ms = elapsed

    def handle(self, event, elapsed, send):
        kind = event.get("type")
        if kind == "response.created":
            self.response = event["response"]
            self.response_id = self.response["id"]
        elif kind == "response.output_item.done":
            item = event["item"]
            self.retain(item, elapsed)
            if (
                item.get("type") == "function_call"
                and item["call_id"] not in self.seen_calls
            ):
                if not self.response_id:
                    raise RuntimeError("Function call without active response ID")
                called = time.monotonic()
                try:
                    output = self.execute(item)
                except Exception as exc:
                    output = json.dumps(
                        {"error": {"type": type(exc).__name__, "message": str(exc)}}
                    )
                    self.tool_errors.append(
                        {"call_id": item["call_id"], "error": output}
                    )
                result = {
                    "type": "function_call_output",
                    "call_id": item["call_id"],
                    "output": output,
                }
                self.seen_calls.add(item["call_id"])
                if self.enabled:
                    self.pending += 1
                    self.inject_sent += 1
                    send(
                        {
                            "type": "response.inject",
                            "response_id": self.response_id,
                            "input": [result],
                        }
                    )
                else:
                    # Ordinary single-agent responses finish at a function call.
                    # Return outputs with the next response.create on this socket.
                    self.next_input.append(result)
                self.tool_calls.append(
                    {
                        "call_id": item["call_id"],
                        "agent": item.get("agent"),
                        "delivery": "inject" if self.enabled else "continuation",
                        "name": item["name"],
                        "arguments": item["arguments"],
                        "output_sha256": sha(output.encode()),
                        "item_done_elapsed_ms": elapsed,
                        "tool_execution_and_send_ms": round(
                            (time.monotonic() - called) * 1000, 3
                        ),
                    }
                )
        elif kind in ("response.inject.created", "response.inject.failed"):
            if self.pending <= 0:
                raise RuntimeError("Injection acknowledgment without pending injection")
            if event.get("response_id") not in (None, self.response_id):
                raise RuntimeError("Injection response ID mismatch")
            self.pending -= 1
            if kind == "response.inject.created":
                self.inject_acked += 1
            else:
                self.inject_failed += 1
                if event.get("error", {}).get("code") != "response_already_completed":
                    raise RuntimeError(
                        "Injection failed: " + json.dumps(event.get("error"))
                    )
                if not isinstance(event.get("input"), list):
                    raise RuntimeError("Late injection lacked returned input")
                self.next_input.extend(event["input"])
        elif kind in ("response.completed", "response.failed", "response.incomplete"):
            self.terminal = kind
            self.response = event["response"]
            for item in self.response.get("output", []):
                self.retain(item, elapsed)
            if kind != "response.completed":
                raise RuntimeError(
                    "Response terminal: "
                    + kind
                    + ": "
                    + json.dumps(self.response.get("error"))
                )
        elif kind == "error":
            raise RuntimeError(
                "WebSocket API error: " + json.dumps(event.get("error", event))
            )
        # Hosted multi_agent_call actions are API-owned, never locally executed.
        return self.terminal == "response.completed" and self.pending == 0


def run(enabled, slot):
    path = ROOT / "results/study" / f"{slot}.json"
    if path.exists():
        return json.loads(path.read_text())
    key = h.dotenv_values(PROJECT / ".env").get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("Project API key missing; no admission")
    initial = request_body(enabled, initial_input())
    record = {
        "case_id": h.FIXTURE.name,
        "run_id": slot,
        "mode": "native" if enabled else "single",
        "runtime": "responses_api",
        "api": "Responses API",
        "transport": "WebSocket",
        "endpoint": ENDPOINT,
        "beta_header": BETA,
        "started_at": now(),
        "status": "admitted",
        "request_body": initial,
        "request_sha256": sha(json.dumps(initial, sort_keys=True).encode()),
        "runner_hash": sha(Path(__file__).read_bytes()),
        "controls": {
            "timeout_seconds": TIMEOUT,
            "depth_ceiling": 3,
            "total_agent_ceiling": 12,
            "depth_count_enforcement": "instruction ceilings; stop observing/continuing after observed violation, not API hard limits",
            "observed_model_estimate_stop_usd": OBSERVED_COST_STOP,
            "max_responses": MAX_RESPONSES,
            "retry_policy": "no automatic retries or replacement trials",
        },
        "responses": [],
        "requests": [],
        "injections": [],
        "events": [],
        "output_items": [],
        "response_id": None,
        "output": None,
        "error": None,
    }
    with path.open("x") as f:
        f.write(json.dumps(record) + "\n")
    started = time.monotonic()
    collector = Collector(enabled)
    counted = set()

    def elapsed():
        return round((time.monotonic() - started) * 1000, 3)

    def append_response(step):
        response = collector.response
        if not collector.terminal or response.get("id") in counted:
            return
        counted.add(response.get("id"))
        record["response"] = response
        record["responses"].append(
            {"step": step, "terminal_event": collector.terminal, "response": response}
        )

    step = 0
    try:
        with path.with_suffix(".events.jsonl").open("x") as journal:
            with connect(
                ENDPOINT,
                additional_headers={
                    "Authorization": "Bearer " + key,
                    "OpenAI-Beta": BETA,
                },
                open_timeout=min(30, TIMEOUT),
                close_timeout=2,
                max_size=None,
            ) as socket:
                record["handshake_ms"] = elapsed()

                def send(message):
                    entry = {
                        "step": step,
                        "body": message,
                        "sent_at": now(),
                        "elapsed_ms": elapsed(),
                        "sha256": sha(json.dumps(message, sort_keys=True).encode()),
                    }
                    record[
                        (
                            "injections"
                            if message["type"] == "response.inject"
                            else "requests"
                        )
                    ].append(entry)
                    socket.send(json.dumps(message))

                pending = initial_input()
                previous = None
                for step in range(MAX_RESPONSES):
                    collector.begin_response()
                    send(request_body(enabled, pending, previous))
                    save(path, record)
                    while True:
                        remaining = TIMEOUT - (time.monotonic() - started)
                        if remaining <= 0:
                            raise TimeoutError("Assessment deadline exceeded")
                        event = json.loads(socket.recv(timeout=remaining))
                        stamp = elapsed()
                        wrapped = {"step": step, "elapsed_ms": stamp, "event": event}
                        journal.write(json.dumps(wrapped) + "\n")
                        journal.flush()
                        record["events"].append(wrapped)
                        done = collector.handle(event, stamp, send)
                        if event.get("type") == "response.created":
                            record["response_id"] = collector.response_id
                            save(path, record)
                        if event.get("type") == "response.output_item.done":
                            proof = h.evidence(list(collector.items.values()))
                            if (
                                event["item"].get("type") == "multi_agent_call_output"
                                and event["item"].get("action") == "spawn_agent"
                            ):
                                print(
                                    json.dumps(
                                        {
                                            "slot": slot,
                                            "native_children": proof["native_children"],
                                            "depth": proof["max_depth"],
                                        }
                                    ),
                                    flush=True,
                                )
                            if proof["native_children"] > 11 or proof["max_depth"] > 3:
                                raise RuntimeError("Observed resource ceiling violated")
                        if done:
                            break
                    append_response(step)
                    save(path, record)
                    if collector.response.get("model") != MODEL:
                        raise RuntimeError("Returned model differs from frozen model")
                    estimates = [
                        h.price(r["response"].get("usage")) for r in record["responses"]
                    ]
                    if sum(c for c in estimates if c is not None) > OBSERVED_COST_STOP:
                        raise RuntimeError("Observed returned-token stop exceeded")
                    if collector.next_input:
                        # Documented same-socket cached continuation works with store=false.
                        pending = collector.next_input
                        previous = collector.response["id"]
                        continue
                    if collector.report is None:
                        raise RuntimeError("No valid completed root final report")
                    record["output"] = collector.report
                    record["root_output_text"] = collector.report_text
                    record["time_to_report_ms"] = collector.report_ms
                    record["validated_completion_ms"] = elapsed()
                    record["status"] = "completed"
                    break
                else:
                    raise RuntimeError("Continuation bound reached")
    except Exception as exc:
        append_response(step)
        record["error"] = {"type": type(exc).__name__, "message": str(exc)[:3000]}
    finally:
        record["output_items"] = list(collector.items.values())
        proof = h.evidence(record["output_items"])
        record["evidence"] = proof
        record["tool_calls"] = collector.tool_calls
        record["tool_errors"] = collector.tool_errors
        record["root_validation_errors"] = collector.validation_errors
        record["retained_root_report_available_ms"] = collector.report_ms
        uses = [r["response"].get("usage") for r in record["responses"]]
        estimates = [h.price(u) for u in uses]
        covered = (
            bool(uses)
            and len(record["requests"]) == len(uses)
            and all(c is not None for c in estimates)
        )
        complete = record["status"] == "completed" and record["output"] is not None
        if not complete:
            record["status"] = "failed"
        record["metrics"] = {
            "completed_report": complete,
            "time_to_report_ms": record.get("time_to_report_ms"),
            "total_duration_ms": elapsed(),
            "native_children": proof["native_children"],
            "agent_count": 1 + proof["native_children"],
            "max_depth": proof["max_depth"],
            "response_count": len(record["responses"]),
            "admitted_requests": len(record["requests"]),
            "function_calls": len(collector.tool_calls),
            "tool_error_count": len(collector.tool_errors),
            "estimated_model_usd": sum(estimates) if covered else None,
            "observed_estimated_model_usd": sum(c for c in estimates if c is not None),
            "usage_complete": covered,
            "input_tokens": sum(u["input_tokens"] for u in uses if u),
            "output_tokens": sum(u["output_tokens"] for u in uses if u),
            "cached_tokens": sum(
                u.get("input_tokens_details", {}).get("cached_tokens", 0)
                for u in uses
                if u
            ),
            "cache_write_tokens": sum(
                u.get("input_tokens_details", {}).get("cache_write_tokens", 0)
                for u in uses
                if u
            ),
            "injections_sent": collector.inject_sent,
            "injections_acked": collector.inject_acked,
            "injections_failed": collector.inject_failed,
            "pending_injections": collector.pending,
            "failed_spawn_count": len(proof["failed_spawn_call_ids"]),
            "children_without_completed_final": sorted(
                set(proof["spawned_agents"]) - set(proof["completed_child_agents"])
            ),
        }
        record["usage_coverage"] = (
            "Each unique terminal response usage once; no child estimates added. Missing usage stays unknown. Full-tree invoice coverage unverified."
        )
        record["cost_basis"] = {
            "rates_per_million": RATES,
            "source": "https://developers.openai.com/api/docs/models/gpt-6.1-sol",
            "double_count_policy": "Each unique response usage once; no child output token estimates",
            "billing_coverage": "Not invoice verified; native per-agent usage breakdown unavailable",
        }
        record["finished_at"] = now()
        save(path, record)
        print(
            json.dumps(
                {
                    "finished": slot,
                    "completed": complete,
                    "error": record["error"],
                    "children": proof["native_children"],
                    "depth": proof["max_depth"],
                    "seconds": record["metrics"]["total_duration_ms"] / 1000,
                    "token_estimate_usd": record["metrics"]["estimated_model_usd"],
                    "injections_acked": collector.inject_acked,
                    "late_injections": collector.inject_failed,
                }
            ),
            flush=True,
        )
    return record
