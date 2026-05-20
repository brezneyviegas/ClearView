"""Online policy tuner (routing-accuracy Layer 3).

Reads the two Layer-2/3 corpora — shadow-judge verdicts and thumbs feedback —
and proposes concrete, mechanical edits to policy.yaml:

  1. RULE TIER BUMP — a named rule whose served responses get down-voted past a
     threshold (enough samples) is escalated one tier (cheap→mid→frontier).
  2. CONFIDENCE FLOOR BUMP — when shadow verdicts show the classifier
     systematically under-routes (alternative tier keeps winning), raise
     classifier.confidence_floor a step so borderline prompts escalate.

Apply is guarded:
  - backs up policy.yaml to policy.yaml.bak.<ts> BEFORE writing,
  - records the apply (+ proposals) in the tuner_log table,
  - revert() restores the latest backup and marks the log row reverted.

NOTE: applying re-serialises policy.yaml via yaml.safe_dump, which drops
comments. The pre-apply backup retains the original verbatim, and revert
restores it fully — so comments are never lost, only moved aside.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

from . import telemetry
from .config import Policy

log = logging.getLogger("clearview.tuner")

_TIER_ORDER = ["cheap", "mid", "frontier"]


def _next_tier(tier: str) -> str | None:
    try:
        i = _TIER_ORDER.index(tier)
    except ValueError:
        return None
    return _TIER_ORDER[i + 1] if i + 1 < len(_TIER_ORDER) else None


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Proposal:
    kind: str            # "rule_tier_bump" | "confidence_floor_bump"
    target: str          # rule name or "classifier.confidence_floor"
    current: object
    proposed: object
    reason: str


def analyze(policy: Policy, *, window_minutes: int = 7 * 24 * 60) -> list[Proposal]:
    """Produce tuning proposals from the corpora. Pure read — never mutates."""
    min_feedback = _i("CLEARVIEW_TUNE_MIN_FEEDBACK", 10)
    downvote_pct = _f("CLEARVIEW_TUNE_DOWNVOTE_PCT", 50.0)
    min_pairs = _i("CLEARVIEW_TUNE_MIN_PAIRS", 20)
    under_route_pct = _f("CLEARVIEW_TUNE_UNDER_ROUTE_PCT", 60.0)

    proposals: list[Proposal] = []
    rule_by_name = {r.get("name"): r for r in policy.rules}

    # --- Signal 1: feedback down-votes per rule -> bump that rule's tier ---
    fb = telemetry.feedback_summary(window_minutes=window_minutes)
    for row in fb.get("by_rule", []):
        reason = row.get("route_reason") or ""
        n = int(row.get("n") or 0)
        down = int(row.get("down") or 0)
        if n < min_feedback:
            continue
        if (down / n * 100.0) < downvote_pct:
            continue
        if not reason.startswith("rule:"):
            continue
        name = reason.split("rule:", 1)[1]
        rule = rule_by_name.get(name)
        if not rule:
            continue
        cur_tier = rule.get("then")
        nxt = _next_tier(cur_tier) if isinstance(cur_tier, str) else None
        if not nxt:
            continue
        proposals.append(Proposal(
            kind="rule_tier_bump", target=name, current=cur_tier, proposed=nxt,
            reason=(f"{down}/{n} down-votes ({round(down/n*100,1)}%) on rule "
                    f"'{name}' (≥{downvote_pct}% over ≥{min_feedback} samples)"),
        ))

    # --- Signal 2: shadow under-route -> raise classifier confidence floor ---
    pairs = telemetry.verdict_by_pair(window_minutes=window_minutes)
    total_judged = sum(int(p["judged"]) for p in pairs)
    total_wins = sum(int(p["shadow_wins"]) for p in pairs)
    if total_judged >= min_pairs and total_wins / total_judged * 100.0 >= under_route_pct:
        cur = float(policy.classifier.confidence_floor)
        step = _f("CLEARVIEW_TUNE_FLOOR_STEP", 0.05)
        proposed = round(min(0.95, cur + step), 4)
        if proposed > cur:
            proposals.append(Proposal(
                kind="confidence_floor_bump", target="classifier.confidence_floor",
                current=cur, proposed=proposed,
                reason=(f"{total_wins}/{total_judged} shadow wins "
                        f"({round(total_wins/total_judged*100,1)}%) — classifier "
                        f"under-routes (≥{under_route_pct}% over ≥{min_pairs} pairs)"),
            ))

    return proposals


def _policy_path() -> Path:
    return Path(os.environ.get("CLEARVIEW_POLICY_PATH", "./policy.yaml"))


def apply(proposals: list[Proposal], *, policy_path: str | None = None) -> dict:
    """Back up policy.yaml, apply proposals, log the change. Returns a summary
    including the backup path and the tuner_log id (for revert)."""
    if not proposals:
        return {"applied": 0, "backup_path": None, "tune_id": None, "proposals": []}

    path = Path(policy_path) if policy_path else _policy_path()
    data = yaml.safe_load(path.read_text())

    backup_path = f"{path}.bak.{int(time.time())}"
    shutil.copy2(path, backup_path)

    rules = data.get("rules", [])
    rules_by_name = {r.get("name"): r for r in rules}
    applied: list[dict] = []
    for p in proposals:
        if p.kind == "rule_tier_bump":
            rule = rules_by_name.get(p.target)
            if rule and rule.get("then") == p.current:
                rule["then"] = p.proposed
                applied.append(asdict(p))
        elif p.kind == "confidence_floor_bump":
            data.setdefault("classifier", {})["confidence_floor"] = p.proposed
            applied.append(asdict(p))

    if not applied:
        os.remove(backup_path)
        return {"applied": 0, "backup_path": None, "tune_id": None, "proposals": []}

    path.write_text(yaml.safe_dump(data, sort_keys=False))
    tune_id = telemetry.record_tune(backup_path=backup_path,
                                    proposals_json=json.dumps(applied))
    log.info("tuner applied %d proposal(s); backup=%s tune_id=%s",
             len(applied), backup_path, tune_id)
    return {"applied": len(applied), "backup_path": backup_path,
            "tune_id": tune_id, "proposals": applied}


def revert(*, policy_path: str | None = None) -> dict:
    """Restore the most recent un-reverted backup; mark its log row reverted."""
    entry = telemetry.latest_tune(include_reverted=False)
    if not entry or not entry.get("backup_path"):
        return {"reverted": False, "reason": "no applied tune to revert"}
    backup = Path(entry["backup_path"])
    if not backup.exists():
        return {"reverted": False, "reason": f"backup missing: {backup}"}
    path = Path(policy_path) if policy_path else _policy_path()
    shutil.copy2(backup, path)
    telemetry.mark_tune_reverted(int(entry["id"]))
    log.info("tuner reverted tune_id=%s from %s", entry["id"], backup)
    return {"reverted": True, "tune_id": entry["id"], "restored_from": str(backup)}
