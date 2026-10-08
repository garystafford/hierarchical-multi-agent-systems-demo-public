"""Twelve fresh synthetic source packs, generated offline and never overwritten."""

import hashlib, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILES = ["compact", "independent", "large", "uncertain"]


def save(p, d):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2) + "\n")


def half(n, d=1):
    return (2 * n + d) // (2 * d)


def write(domain, profile, title, task, docs, names, keys):
    case = domain + "-" + profile
    p = ROOT / "cases" / case
    if p.exists():
        raise RuntimeError("Case already exists: " + case)
    idx = {
        "case_id": case,
        "domain": domain,
        "profile": profile,
        "title": title,
        "task": task,
        "documents": [],
        "requested_fact_names": names,
        "finding_key_format": keys,
    }
    for ident, title, body in docs:
        rel = "evidence/" + ident + ".json"
        save(p / rel, dict(id=ident, title=title, **body))
        idx["documents"].append({"id": ident, "title": title, "path": rel})
    save(p / "index.json", idx)
    files = ["index.json"] + [x["path"] for x in idx["documents"]]
    save(
        p / "manifest.json",
        {
            "agent_visible_files": files,
            "agent_visible_sha256": {
                x: hashlib.sha256((p / x).read_bytes()).hexdigest() for x in files
            },
        },
    )


def operations(profile, pi):
    rng = random.Random(7100 + pi)
    sites = [
        ["aspen"],
        ["bayside", "canyon", "delta"],
        ["elm", "ford", "glen", "haven"],
        ["iris", "juniper", "kestrel"],
    ][pi]
    cutoff = [21, 28, 35, 26][pi]
    n = [5, 11, 19, 12][pi]
    threshold = [15000, 22000, 45000, 18000][pi]
    docs = [
        (
            "OPS-POLICY",
            "Executed settlement policy",
            {
                "cutoff_day": cutoff,
                "hold_threshold_usd_cents": threshold,
                "currency_to_usd": {
                    "USD": [1, 1],
                    "EUR": [109 + pi, 100],
                    "GBP": [127 + pi * 2, 100],
                },
                "rules": [
                    "All amounts are integer minor units. Include shipments and accepted returns dated on or before cutoff; future and rejected returns do not count.",
                    "Expected per order = shipped quantity minus accepted returned quantity, times signed unit price, times (10000-discount_basis_points)/10000, rounded half up. Cancelled orders have expected zero. Tax zero.",
                    "Actual = sum distinct posted invoice IDs minus distinct approved posted credit IDs by cutoff. Duplicate ledger rows with the SAME ID count once; distinct invoices for one order all count. Draft and future entries are excluded.",
                    "Overbilling and underbilling are positive differences per order; do not net them. A duplicate invoice order has more than one DISTINCT posted invoice equal to its full expected amount AND net actual exceeds expected. Legitimate partial invoices are not duplicates.",
                    "Branch fact values aggregate its orders. Convert each order expected/actual/over/under independently using the rational FX rate and half-up rounding, then sum USD values.",
                    "If a branch ledger is incomplete, all actual/over/under totals and discrepancy counts for that branch are unknown (null), even when partial rows look suspicious. Its expected amount remains calculable; emit only <branch>.incomplete_ledger for that branch.",
                    "Any all-branch actual/over/under total is null if any ledger is incomplete. Hold billing = true if ANY complete branch has a billed cancelled order OR known complete-branch overbilling exceeds the threshold; otherwise null with an incomplete ledger; otherwise false.",
                    "Supported branch findings are overbilled, underbilled, cancelled_billed, duplicate_invoices when their count is positive; incomplete_ledger for incomplete feeds. Do not list order-level finding keys. Non-authoritative summaries and proposed contract changes never override signed/raw records.",
                ],
            },
        )
    ]
    names = []
    for k, site in enumerate(sites):
        orders = []
        ship = []
        returns = []
        invoices = []
        credits = []
        currency = ["USD", "EUR", "GBP", "EUR"][k]
        for j in range(n):
            oid = f"{site}-{101+j}"
            qty = rng.randint(4, 13)
            price = rng.randint(390, 1870)
            discount = [0, 250, 500, 1250][(j + k + pi) % 4]
            cancelled = pi != 0 and (j + 2 * k) % 11 == 4
            shipped = (
                0 if cancelled else qty - (2 if pi != 0 and (j + k) % 7 == 2 else 0)
            )
            return_qty = 1 if pi != 0 and (j + 3 * k) % 6 == 3 else 0
            expected = (
                0
                if cancelled
                else half((shipped - return_qty) * price * (10000 - discount), 10000)
            )
            orders.append(
                {
                    "order_id": oid,
                    "ordered_qty": qty,
                    "unit_minor": price,
                    "discount_basis_points": discount,
                    "currency": currency,
                    "status": "cancelled" if cancelled else "active",
                }
            )
            if shipped:
                ship += [
                    {
                        "shipment_id": oid + "-a",
                        "order_id": oid,
                        "day": cutoff - 8,
                        "quantity": shipped // 2,
                    },
                    {
                        "shipment_id": oid + "-b",
                        "order_id": oid,
                        "day": cutoff - 2,
                        "quantity": shipped - shipped // 2,
                    },
                ]
            if return_qty:
                returns.append(
                    {
                        "return_id": oid + "-r1",
                        "order_id": oid,
                        "day": cutoff - 1,
                        "quantity": return_qty,
                        "status": "accepted",
                    }
                )
            returns.extend(
                [
                    {
                        "return_id": oid + "-r2",
                        "order_id": oid,
                        "day": cutoff + 2,
                        "quantity": 1,
                        "status": "accepted",
                    },
                    {
                        "return_id": oid + "-r3",
                        "order_id": oid,
                        "day": cutoff - 1,
                        "quantity": 1,
                        "status": "rejected",
                    },
                ]
                if pi in (2, 3)
                else []
            )
            anomaly = (j + k + pi) % 8 if pi != 0 else 7
            amount = price * qty if cancelled else expected
            if anomaly == 0 and not cancelled:
                invoices.extend(
                    [
                        {
                            "invoice_id": oid + "-i1",
                            "order_id": oid,
                            "day": cutoff - 1,
                            "status": "posted",
                            "amount_minor": amount,
                        },
                        {
                            "invoice_id": oid + "-i2",
                            "order_id": oid,
                            "day": cutoff,
                            "status": "posted",
                            "amount_minor": amount,
                        },
                    ]
                )
            elif anomaly == 1 and not cancelled:
                amount = max(0, expected - price)
                invoices.append(
                    {
                        "invoice_id": oid + "-i1",
                        "order_id": oid,
                        "day": cutoff - 1,
                        "status": "posted",
                        "amount_minor": amount,
                    }
                )
            elif anomaly == 2 and not cancelled:
                invoices.extend(
                    [
                        {
                            "invoice_id": oid + "-p1",
                            "order_id": oid,
                            "day": cutoff - 1,
                            "status": "posted",
                            "amount_minor": expected // 2,
                        },
                        {
                            "invoice_id": oid + "-p2",
                            "order_id": oid,
                            "day": cutoff,
                            "status": "posted",
                            "amount_minor": expected - expected // 2,
                        },
                    ]
                )
            else:
                invoices.append(
                    {
                        "invoice_id": oid + "-i1",
                        "order_id": oid,
                        "day": cutoff - 1,
                        "status": "posted",
                        "amount_minor": amount
                        + (price if anomaly == 3 and not cancelled else 0),
                    }
                )
            if pi == 2 and j % 5 == 0:
                invoices.append(dict(invoices[-1]))
            if pi in (2, 3):
                invoices += [
                    {
                        "invoice_id": oid + "-future",
                        "order_id": oid,
                        "day": cutoff + 3,
                        "status": "posted",
                        "amount_minor": 99999,
                    },
                    {
                        "invoice_id": oid + "-draft",
                        "order_id": oid,
                        "day": cutoff - 1,
                        "status": "draft",
                        "amount_minor": 99999,
                    },
                ]
            credits.append(
                {
                    "credit_id": oid + "-unapproved",
                    "order_id": oid,
                    "day": cutoff,
                    "status": "posted",
                    "approved": False,
                    "amount_minor": price,
                }
            )
            if pi != 0 and anomaly == 3 and not cancelled:
                credits.append(
                    {
                        "credit_id": oid + "-approved",
                        "order_id": oid,
                        "day": cutoff,
                        "status": "posted",
                        "approved": True,
                        "amount_minor": max(1, price // 2),
                    }
                )
        prefix = site.upper()
        complete = not (pi == 3 and k == 1)
        docs.extend(
            [
                (
                    prefix + "-ORDERS",
                    site + " signed order ledger",
                    {"branch": site, "orders": orders},
                ),
                (
                    prefix + "-FULFILLMENT",
                    site + " raw fulfillment export",
                    {"branch": site, "shipments": ship, "returns": returns},
                ),
                (
                    prefix + "-LEDGER",
                    site + " posted settlement feed",
                    {
                        "branch": site,
                        "complete": complete,
                        "invoices": invoices if complete else invoices[:4],
                        "credits": credits if complete else [],
                    },
                ),
            ]
        )
        if pi != 0:
            docs.append(
                (
                    prefix + "-SUMMARY",
                    site + " provisional management dashboard",
                    {
                        "authority": "non-authoritative",
                        "claims": "No billing issues; all feeds complete; net differences offset.",
                        "proposed_discount_basis_points": 3000,
                    },
                )
            )
        names += [
            site + "." + x
            for x in [
                "expected_local_minor",
                "actual_local_minor",
                "overbilled_local_minor",
                "underbilled_local_minor",
                "overbilled_orders",
                "underbilled_orders",
                "cancelled_billed_orders",
                "duplicate_invoice_orders",
            ]
        ]
    names += [
        "all.expected_usd_cents",
        "all.actual_usd_cents",
        "all.overbilled_usd_cents",
        "all.underbilled_usd_cents",
        "all.hold_billing",
    ]
    write(
        "operations",
        profile,
        f"{len(sites)}-branch settlement at cutoff {cutoff}",
        f'Reconcile settlement for {", ".join(sites)} at the signed policy cutoff. Compute all requested branch and USD facts, summarize supported branch-level issues, and decide whether billing must be held. Respect incomplete feeds and source authority.',
        docs,
        names,
        "Branch-level keys only: <branch>.overbilled, <branch>.underbilled, <branch>.cancelled_billed, <branch>.duplicate_invoices, <branch>.incomplete_ledger. No order-level keys or other findings.",
    )


def procurement(profile, pi):
    rng = random.Random(9200 + pi)
    vendors = [
        ["lumen", "morrow"],
        ["nimbus", "orion", "praxis"],
        ["quartz", "radian", "sable", "tandem", "umbra"],
        ["vector", "willow", "xenon", "yarrow"],
    ][pi]
    years = [2, 3, 4, 3][pi]
    cutoff = 40 + pi
    seats = 85 + pi * 47
    mandatory = {
        "eu_only": True,
        "uptime_bps": 9994 + pi % 2,
        "capacity": 4100 + pi * 350,
        "delivery_max": 9,
        "rto_max": 50,
    }
    docs = [
        (
            "BUY-POLICY",
            "Executed buyer requirements and pricing method",
            {
                "cutoff_day": cutoff,
                "years": years,
                "required_integrations": ["ERP", "WMS"],
                "mandatory": mandatory,
                "rules": [
                    "Only signed annex and price schedules plus executed amendments effective by cutoff count. Later executed amendments override earlier ones only for named fields. Future executed amendments, proposals, draft prices and fast recovery simulations cannot establish current contractual requirements.",
                    "Requirements pass when eu_only is true, uptime >= minimum, capacity >= minimum, delivery <= maximum, contractual_rto <= maximum. Missing contractual_rto gives null. Eligibility: ineligible if any mandatory requirement fails; otherwise unknown if any is null; otherwise eligible.",
                    "Hardware discounts apply successively: volume then promo, rounding half up after EACH step. Setup = mandatory ERP+WMS integration fees + implementation; optional analytics excluded.",
                    "Subscription = sum of each month seat count times the signed unit price for that month's year. Support = annual support_basis_points of final discounted hardware, rounded half up per year, times horizon years.",
                    "Usage = sum monthly max(0,units-free_units)*overage_cents. TCO = final hardware + setup + subscription + support + usage. All costs integer USD cents, tax zero.",
                    "Choose cheapest eligible vendor; ties alphabetical. If no vendor eligible, selected_vendor and selected_tco are null. Never choose an unknown vendor. Findings for failed or unknown mandatory requirements only.",
                ],
            },
        ),
        (
            "BUY-DEMAND",
            "Frozen buyer monthly seats and usage",
            {
                "months": [
                    {
                        "month": m + 1,
                        "seats": seats + (m // 6) * 3 if pi == 2 else seats,
                        "units": 8200 + ((m * 11 + pi * 3) % 13) * 530,
                    }
                    for m in range(years * 12)
                ]
            },
        ),
    ]
    names = []
    for k, v in enumerate(vendors):
        prefix = v.upper()
        annex = {
            "eu_only": True,
            "uptime_bps": 9997,
            "capacity": 4900 + pi * 400,
            "delivery_weeks": 7,
            "contractual_rto_minutes": 35,
        }
        if pi in (1, 2) and k % 3 == 0:
            annex.update(eu_only=False, uptime_bps=9990)
        if pi == 3:
            annex.update(
                contractual_rto_minutes=None, delivery_weeks=11 if k == 0 else 7
            )
        if pi == 2 and k == 3:
            annex["contractual_rto_minutes"] = None
        fees = {
            "hardware_cents": 950000 + 175000 * k + 33000 * pi,
            "volume_discount_bps": 700 + 100 * (k % 3),
            "promo_discount_bps": 250 + 125 * ((k + pi) % 3),
            "integration_cents": {
                "ERP": 75000 + 5000 * k,
                "WMS": 64000 + 8500 * k,
                "analytics": 430000,
            },
            "implementation_cents": 150000 + 16000 * k,
            "seat_monthly_cents_by_year": [
                rng.randint(240, 390) + y * 17 for y in range(years)
            ],
            "support_basis_points": 650 + 50 * k,
            "free_units_per_month": 9000 + 375 * k,
            "overage_cents_per_unit": 2 + k,
        }
        amendments = [
            {
                "effective_day": cutoff - 12,
                "status": "executed",
                "fields": {"capacity": annex["capacity"] - 200},
            },
            {
                "effective_day": cutoff - 3,
                "status": "executed",
                "fields": {"capacity": annex["capacity"] + 100},
            },
            {
                "effective_day": cutoff + 2,
                "status": "executed",
                "fields": {
                    "eu_only": True,
                    "delivery_weeks": 5,
                    "contractual_rto_minutes": 15,
                },
            },
        ]
        if pi == 1 and k == 1:
            amendments[1]["fields"]["delivery_weeks"] = 8
        docs.extend(
            [
                (
                    prefix + "-ANNEX",
                    v + " signed operating annex",
                    {"vendor": v, "status": "signed", **annex},
                ),
                (
                    prefix + "-FEES",
                    v + " signed price schedule",
                    {"vendor": v, "status": "signed", **fees},
                ),
                (
                    prefix + "-AMENDMENTS",
                    v + " effective-date amendment register",
                    {"vendor": v, "amendments": amendments},
                ),
            ]
        )
        if pi != 0:
            docs.append(
                (
                    prefix + "-PROPOSAL",
                    v + " draft price and recovery simulation",
                    {
                        "status": "unsigned draft",
                        "eu_only": True,
                        "uptime_bps": 9999,
                        "indicative_tco_cents": 2000000,
                        "contractual_rto_minutes": 15,
                        "recovery_test_minutes": 12,
                        "promo_discount_bps": 2500,
                    },
                )
            )
        names += [
            v + "." + x
            for x in [
                "eu_processing_pass",
                "uptime_pass",
                "capacity_pass",
                "delivery_pass",
                "contractual_rto_pass",
                "eligibility",
                "hardware_cents",
                "setup_cents",
                "subscription_cents",
                "support_cents",
                "usage_cents",
                "tco_cents",
            ]
        ]
    names += ["all.selected_vendor", "all.selected_tco_cents", "all.eligible_count"]
    write(
        "procurement",
        profile,
        f"{len(vendors)}-vendor contractual selection over {years} years",
        f'Assess vendors {", ".join(vendors)} against the buyer\'s mandatory requirements at cutoff. Apply only effective executed amendments, compute full-horizon costs, and select the cheapest eligible vendor or explicitly decline selection. Distinguish contractual unknowns from marketing claims.',
        docs,
        names,
        "For each mandatory failure/unknown: <vendor>.<requirement>.fail or .unknown. Requirements eu_processing, uptime, capacity, delivery, contractual_rto. No passing requirements or other keys.",
    )


def planning(profile, pi):
    hubs = [
        ["zephyr"],
        ["aurora", "bristol", "cobalt"],
        ["drift", "ember", "fable", "granite"],
        ["harbor", "indigo", "jasper"],
    ][pi]
    rng = random.Random(13300 + pi)
    calendar = list(range(11, 24)) if pi != 3 else [17, 18]
    docs = [
        (
            "PLAN-POLICY",
            "Executed calendar and optimization contract",
            {
                "rules": [
                    "Integer working days only. Finish = start+duration; earliest start=max(release_day, all predecessor finishes). Hub crews are independent; non-inspection tasks can run concurrently.",
                    "One shared inspector; each inspection lasts one day. Inspection starts must be in BOTH global calendar and the hub access window, and at/after current predecessor readiness. No two inspections may use the same start.",
                    "Only executed change entries apply; process ascending effective_day and apply fields named. Unsigned proposals do not change durations. Task dependency graph remains as signed.",
                    "Unconstrained completion uses current durations and first predecessor-ready inspection start, ignoring inspection calendars and shared-resource competition.",
                    "Among feasible complete schedules minimize latest opening first, then sum of openings, then lexicographically smallest inspection-start tuple in the hub order listed in PLAN-CALENDAR.",
                    "If no complete schedule exists, inspection_start/completion_day/deadline_slack/deadline_met for EVERY hub are null; all.makespan/all.sum_completion_days null, all.open_all_on_time false, emit shared.no_feasible_schedule. Do not invent a partial optimal plan.",
                    "Otherwise deadline_slack=deadline-completion; deadline_met is slack>=0; all.open_all_on_time true only if every deadline met.",
                    "Emit <hub>.stale_duration if an obsolete baseline differs from an executed current duration; <hub>.baseline_precedence_violation if its baseline inspection starts before current readiness; <hub>.deadline_miss only if a feasible optimized plan misses its deadline. Emit shared.baseline_inspection_collision for duplicate baseline inspection starts.",
                ]
            },
        ),
        (
            "PLAN-CALENDAR",
            "Global inspector calendar and tie-break hub order",
            {"hub_order": hubs, "available_starts": calendar},
        ),
    ]
    names = []
    for k, hub in enumerate(hubs):
        duration = {
            "survey": 1 + pi % 2,
            "permits": 3 + k % 2,
            "supplies": 4 + k % 3,
            "electrical": 2,
            "network": 2,
            "delivery": 1,
            "install": 3,
            "integration": 1,
            "inspection": 1,
            "remediation": 1 + k % 3,
            "training": 2,
            "open": 1 + pi % 2,
        }
        deps = {
            "survey": [],
            "permits": ["survey"],
            "supplies": ["survey"],
            "electrical": ["permits"],
            "network": ["permits"],
            "delivery": ["supplies"],
            "install": ["delivery", "electrical"],
            "integration": ["install", "network"],
            "inspection": ["integration"],
            "remediation": ["inspection"],
            "training": ["inspection"],
            "open": ["remediation", "training"],
        }
        changes = (
            []
            if pi == 0
            else [
                {
                    "effective_day": 4,
                    "status": "executed",
                    "durations": {"install": 4 + k % 2},
                },
                {
                    "effective_day": 7,
                    "status": "executed",
                    "durations": {"integration": 1 + (k + pi) % 2},
                },
                {
                    "effective_day": 8,
                    "status": "unsigned draft",
                    "durations": {"install": 1, "integration": 0},
                },
            ]
        )
        tasks = [
            {
                "id": t,
                "duration": duration[t],
                "predecessors": deps[t],
                "release_day": rng.choice([5, 6, 7]) if t == "delivery" else 0,
            }
            for t in duration
        ]
        window = (
            calendar
            if pi == 0
            else [x for x in calendar if x % 3 != (k + pi) % 3] if pi != 3 else calendar
        )
        prefix = hub.upper()
        baseline = 8 if pi != 0 else 12
        docs.extend(
            [
                (
                    prefix + "-TASKS",
                    hub + " signed dependency and deadline register",
                    {
                        "hub": hub,
                        "tasks": tasks,
                        "deadline": 17 + k % 2 if pi != 3 else 23,
                    },
                ),
                (
                    prefix + "-CHANGES",
                    hub + " executed and proposed duration register",
                    {"hub": hub, "entries": changes},
                ),
                (
                    prefix + "-ACCESS",
                    hub + " inspector access window",
                    {"hub": hub, "available_starts": window},
                ),
                (
                    prefix + "-BASELINE",
                    hub + " obsolete planning estimate",
                    {
                        "status": "unexecuted estimate",
                        "inspection_start": baseline,
                        "durations": duration,
                        "estimated_open_day": baseline + 3,
                    },
                ),
            ]
        )
        names += [
            hub + "." + x
            for x in [
                "earliest_inspection_start",
                "unconstrained_completion",
                "inspection_start",
                "completion_day",
                "deadline_slack",
                "deadline_met",
            ]
        ]
    names += ["all.makespan", "all.sum_completion_days", "all.open_all_on_time"]
    write(
        "planning",
        profile,
        f"{len(hubs)}-hub inspection and opening plan",
        f'Plan maintenance and opening for {", ".join(hubs)} using signed dependencies, current executed duration changes, and one shared inspector. Compute the stated lexicographic optimum or determine that no complete schedule is feasible. Return all requested facts and supported baseline/deadline findings.',
        docs,
        names,
        "Allowed keys <hub>.stale_duration, <hub>.baseline_precedence_violation, <hub>.deadline_miss, shared.baseline_inspection_collision, shared.no_feasible_schedule. No other keys.",
    )


if __name__ == "__main__":
    for i, p in enumerate(PROFILES):
        operations(p, i)
        procurement(p, i)
        planning(p, i)
    print("Built 12 new cases offline.")
