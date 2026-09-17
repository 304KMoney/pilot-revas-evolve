"""
evolve_runner.py
Evolve Edge AI — ARM-B Static Runner
Version: 1.0.0
Frozen: 2026-09-17

PURPOSE
-------
This script implements the Evolve Edge governance evaluation layer for use
in the joint REVAS / Evolve Edge benchmark (ARM-B and ARM-C conditions).

It takes two canonical JSON inputs:
  - control_atom.json  : the governance rule governing the AI output
  - live_state.json    : the evaluation snapshot of the AI output

It produces one JSON output:
  - evolve_output.json : structured Route / Verify / Gate result

NO network calls. NO production credentials. NO external dependencies.
Python standard library only.

Deterministic: same inputs always produce the same output.
Auditable: all decision logic is visible in this file.

USAGE
-----
    python evolve_runner.py --atom control_atom.json --state live_state.json --out evolve_output.json

Or with explicit paths:
    python evolve_runner.py \
        --atom ./pilot-01/control_atom.json \
        --state ./pilot-01/live_state.json \
        --out ./pilot-01/evolve_output.json

CALIBRATION
-----------
Run against the Pilot 1 frozen source at commit de4fe17 to verify
the runner is faithful before using it on benchmark items:

    python evolve_runner.py \
        --atom pilot-01/control_atom.json \
        --state pilot-01/live_state.json \
        --out pilot-01/calibration_output.json

Expected calibration result:
    route.route_decision       = "dual_authorization_required"
    verification.verification_state = "incomplete"
    adoption_gate.gate_decision     = "HOLD"

WHAT THIS RUNNER DOES NOT DO
-----------------------------
- It does not independently verify clinical credentials, log records, or
  hash preimages. Source-reported boolean values are preserved as-is.
- It does not execute adoption, write records, or propagate state.
- adoption_performed is always False.
- gate_decision == "ADOPT" requires all required booleans to be True in
  the source input. The runner does not relax this requirement.
- This is not the production Evolve codebase. It is a spec-faithful
  implementation built for this benchmark.

LICENSE / USAGE RESTRICTION
----------------------------
This file is provided solely for the REVAS / Evolve Edge joint benchmark.
It may not be redistributed, reverse-engineered as a production system,
or used as the basis for a competing product. All Evolve Edge governance
semantics encoded here remain the intellectual property of Strong
Redemption LLC d/b/a Evolve Edge AI.
"""

import json
import sys
import argparse
import hashlib
from datetime import datetime, timezone
from typing import Any


# ── VERSION ──────────────────────────────────────────────────────────────────

RUNNER_VERSION = "1.0.0"
RUNNER_FROZEN_DATE = "2026-09-17"
SPEC_VERSION = "1.0.0"


# ── HELPERS ───────────────────────────────────────────────────────────────────

def load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        sys.exit(f"[evolve_runner] ERROR: File not found: {path}")
    except json.JSONDecodeError as e:
        sys.exit(f"[evolve_runner] ERROR: Invalid JSON in {path}: {e}")


def sha256_of(obj: Any) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ── ROUTE ─────────────────────────────────────────────────────────────────────

def derive_route(atom: dict, state: dict) -> dict:
    """
    Determine the authorization route for this AI output.

    Route is determined by the authority requirements in the control atom
    combined with the authority state in live_state.json.

    Route values (in priority order):
      dual_authorization_required   — dual_authorization_required == True in state
      single_authorization_required — standard single-actor authorization path
      evidence_only                 — no actor authorization required; evidence only
      no_constraint_applicable      — no control atom constraint applies
    """
    constraint = atom.get("constraint", {})
    authority_state = state.get("authority_state", {})
    adoption_gate = constraint.get("adoption_gate", {})

    # No constraint
    if not constraint:
        return {
            "route_decision": "no_constraint_applicable",
            "routing_authority": atom.get("control_id", "UNKNOWN"),
            "source_refs": [],
            "decision_executed": False,
            "reason": "Control atom has no constraint block. No routing rule applies.",
        }

    # Dual authorization required
    if authority_state.get("dual_authorization_required", False):
        source = authority_state.get("dual_authorization_required_source", "not specified")
        return {
            "route_decision": "dual_authorization_required",
            "routing_authority": atom.get("control_id"),
            "source_refs": [
                "live_state.json#/authority_state/dual_authorization_required",
                "live_state.json#/authority_state/dual_authorization_required_source",
                "control_atom.json#/constraint/adoption_gate/condition_for_escalation",
            ],
            "decision_executed": False,
            "reason": (
                f"Source explicitly requires dual authorization "
                f"(dual_authorization_required_source: {source}). "
                "This artifact specifies the route; it does not dispatch a queue operation."
            ),
        }

    # Evidence-only path (no authority requirements defined)
    authority_reqs = constraint.get("authority_requirements", [])
    if not authority_reqs:
        return {
            "route_decision": "evidence_only",
            "routing_authority": atom.get("control_id"),
            "source_refs": ["control_atom.json#/constraint/authority_requirements"],
            "decision_executed": False,
            "reason": "No authority requirements defined. Evidence requirements apply only.",
        }

    # Standard single authorization
    return {
        "route_decision": "single_authorization_required",
        "routing_authority": atom.get("control_id"),
        "source_refs": [
            "control_atom.json#/constraint/authority_requirements",
            "live_state.json#/authority_state",
        ],
        "decision_executed": False,
        "reason": "Standard single-actor authorization path. Dual authorization not required.",
    }


# ── VERIFY ────────────────────────────────────────────────────────────────────

def derive_verification(atom: dict, state: dict) -> dict:
    """
    Evaluate which authority and evidence checks are satisfied.

    Source-reported boolean values are preserved exactly.
    The runner does not independently verify credentials, log records,
    or hash preimages.

    verification_state:
      complete     — all required checks are True
      incomplete   — one or more required checks are False
      not_applicable — no constraint applies
    """
    constraint = atom.get("constraint", {})
    authority_state = state.get("authority_state", {})
    evidence_state = state.get("evidence_state", {})

    if not constraint:
        return {
            "verification_state": "not_applicable",
            "owner": "REVAS verifier role",
            "checks": [],
            "blocking_gaps": [],
            "source_reported_flags_are_preserved": True,
            "independent_verification_performed": False,
            "decision_authority": "Verification does not issue the adoption decision.",
        }

    # Authority checks
    authority_checks = [
        {
            "check": "primary_authority_verified",
            "required": True,
            "reported_value": authority_state.get("primary_authority_verified", False),
            "source_ref": "live_state.json#/authority_state/primary_authority_verified",
        },
    ]

    # Add secondary authority check if dual authorization is required
    if authority_state.get("dual_authorization_required", False):
        authority_checks.append({
            "check": "secondary_authority_verified",
            "required": True,
            "reported_value": authority_state.get("secondary_authority_verified", False),
            "source_ref": "live_state.json#/authority_state/secondary_authority_verified",
        })
        authority_checks.append({
            "check": "required_authority_set_complete",
            "required": True,
            "reported_value": authority_state.get("required_authority_set_complete", False),
            "source_ref": "live_state.json#/authority_state/required_authority_set_complete",
        })

    # Evidence checks — derived from evidence_requirements in atom
    evidence_check_map = {
        "minimum_necessary_logged": "live_state.json#/evidence_state/minimum_necessary_logged",
        "model_version_recorded": "live_state.json#/evidence_state/model_version_recorded",
        "input_hash_recorded": "live_state.json#/evidence_state/input_hash_recorded",
        "output_hash_recorded": "live_state.json#/evidence_state/output_hash_recorded",
        "authorization_event_logged": "live_state.json#/evidence_state/authorization_event_logged",
        "audit_trail_complete": "live_state.json#/evidence_state/audit_trail_complete",
    }

    evidence_checks = []
    for field, ref in evidence_check_map.items():
        if field in evidence_state:
            evidence_checks.append({
                "check": field,
                "required": True,
                "reported_value": evidence_state.get(field, False),
                "source_ref": ref,
            })

    all_checks = authority_checks + evidence_checks
    blocking_gaps = [c["check"] for c in all_checks if c["required"] and not c["reported_value"]]

    verification_state = "complete" if not blocking_gaps else "incomplete"

    return {
        "verification_state": verification_state,
        "owner": "REVAS verifier role",
        "evidence_class": "derived_evaluation_of_source_reported_checks",
        "checks": all_checks,
        "blocking_gaps": blocking_gaps,
        "source_reported_flags_are_preserved": True,
        "independent_verification_performed": False,
        "known_state_boundary": (
            "True/false values are preserved exactly as reported in live_state.json. "
            "Credentials, log records, hash preimages, and approval documents were not "
            "independently supplied or checked."
        ),
        "decision_authority": "Verification does not issue the adoption decision.",
    }


# ── GATE ──────────────────────────────────────────────────────────────────────

def derive_gate(atom: dict, state: dict, verification: dict) -> dict:
    """
    Apply the HOLD / ADOPT / REJECT decision.

    Decision logic follows the hold_reject_precedence rule in the control atom:
      ADOPT  — all required checks are True (verification_state == "complete")
      HOLD   — checks are incomplete AND incompleteness is remediable
                (authorization_window_open == True AND window not expired)
      REJECT — terminal: authority invalid/revoked, evidence permanently
                unresolvable, or authorization window expired

    The gate owns the adoption decision. Verify does not.
    adoption_performed is always False in the static runner.
    """
    constraint = atom.get("constraint", {})
    adoption_gate_spec = constraint.get("adoption_gate", {})
    authority_state = state.get("authority_state", {})
    blocking_gaps = verification.get("blocking_gaps", [])
    verification_state = verification.get("verification_state", "incomplete")

    # No constraint
    if not constraint:
        return {
            "gate_decision": "HOLD",
            "gate_authority": atom.get("control_id", "UNKNOWN"),
            "owner": "Adoption Gate",
            "reason": "No constraint defined. Defaulting to HOLD.",
            "downstream_propagation": False,
            "adopted_state": None,
            "adoption_performed": False,
            "gate_execution_timestamp": None,
        }

    # ADOPT path: all checks satisfied
    if verification_state == "complete" and not blocking_gaps:
        return {
            "gate_decision": "ADOPT",
            "gate_authority": atom.get("control_id"),
            "owner": "Adoption Gate",
            "evidence_class": "derived_from_canonical_snapshot",
            "source_refs": [
                "control_atom.json#/constraint/adoption_gate/condition_for_adoption",
                "live_state.json#/authority_state",
                "live_state.json#/evidence_state",
            ],
            "reason": (
                "All authority requirements and evidence requirements are satisfied. "
                "Adoption is permitted. A TrustLedger receipt is required before "
                "state propagation (not executed by this runner)."
            ),
            "downstream_propagation": False,  # Runner specifies; does not execute
            "adopted_state": None,            # Runner does not produce adopted state
            "adoption_performed": False,
            "gate_execution_timestamp": None,
            "receipt_required_before_propagation": True,
        }

    # REJECT path: terminal conditions
    window_open = authority_state.get("authorization_window_open", True)
    window_expired = authority_state.get("authorization_window_expired", False)

    # Check for terminal authority failure
    primary_verified = authority_state.get("primary_authority_verified", True)
    # Primary authority explicitly False (not merely absent) = terminal
    primary_terminal = (primary_verified is False and
                        "primary_authority_verified" in state.get("authority_state", {}))

    terminal = window_expired or primary_terminal

    if terminal:
        reason_parts = []
        if window_expired:
            reason_parts.append("the authorization window has expired")
        if primary_terminal:
            reason_parts.append("primary authority cannot be confirmed (terminal)")
        return {
            "gate_decision": "REJECT",
            "gate_authority": atom.get("control_id"),
            "owner": "Adoption Gate",
            "evidence_class": "derived_from_canonical_snapshot",
            "source_refs": [
                "control_atom.json#/constraint/adoption_gate/condition_for_rejection",
                "live_state.json#/authority_state",
            ],
            "reason": (
                f"Terminal condition: {'; '.join(reason_parts)}. "
                "Reject and quarantine the candidate state per control atom specification."
            ),
            "downstream_propagation": False,
            "adopted_state": None,
            "adoption_performed": False,
            "gate_execution_timestamp": None,
        }

    # HOLD path: incomplete but remediable
    hold_reject_precedence = adoption_gate_spec.get(
        "hold_reject_precedence",
        "HOLD if incomplete and remediable; REJECT only if terminal"
    )
    return {
        "gate_decision": "HOLD",
        "gate_authority": atom.get("control_id"),
        "owner": "Adoption Gate",
        "evidence_class": "derived_from_canonical_snapshot",
        "source_refs": [
            "control_atom.json#/constraint/adoption_gate/default",
            "control_atom.json#/constraint/adoption_gate/hold_reject_precedence",
            "live_state.json#/authority_state/authorization_window_open",
        ],
        "reason": (
            f"Required checks are incomplete (blocking: {blocking_gaps}) but conditions "
            "remain remediable: the authorization window is open and has not expired. "
            f"Precedence rule: {hold_reject_precedence}"
        ),
        "downstream_propagation": False,
        "candidate_state_preserved_required": True,
        "adopted_state": None,
        "adoption_performed": False,
        "gate_execution_timestamp": None,
    }


# ── MAIN ──────────────────────────────────────────────────────────────────────

def run(atom_path: str, state_path: str, out_path: str) -> None:
    atom = load_json(atom_path)
    state = load_json(state_path)

    route = derive_route(atom, state)
    verification = derive_verification(atom, state)
    gate = derive_gate(atom, state, verification)

    # Input fingerprints for audit trail
    atom_digest = sha256_of(atom)
    state_digest = sha256_of(state)

    output = {
        "_artifact_role": "Evolve Edge ARM-B static runner output",
        "runner_version": RUNNER_VERSION,
        "runner_frozen_date": RUNNER_FROZEN_DATE,
        "spec_version": SPEC_VERSION,
        "generated_at": now_iso(),
        "proof_scope": {
            "artifact_type": "static_runner_output",
            "runtime_pipeline_executed": False,
            "clinical_or_external_effect_executed": False,
            "production_readiness_claimed": False,
            "adoption_performed": False,
            "network_calls_made": False,
            "external_credentials_used": False,
        },
        "input_fingerprints": {
            "control_atom_sha256": atom_digest,
            "live_state_sha256": state_digest,
            "control_id": atom.get("control_id"),
            "control_version": atom.get("version"),
            "object_id": state.get("object_id"),
            "evaluation_timestamp": state.get("evaluation_timestamp"),
        },
        "route": route,
        "verification": verification,
        "adoption_gate": gate,
        "semantic_invariants": [
            "Source-reported boolean values are preserved exactly as supplied.",
            "Verify reports verification state; Gate owns HOLD/ADOPT/REJECT.",
            "HOLD takes precedence over the rejection clause for remediable conditions.",
            "adoption_performed is always False in this runner.",
            "gate_decision == ADOPT requires all required checks to be True.",
            "Candidate generation does not confer adoption authority.",
            "This output is a static specification, not an executed runtime result.",
        ],
    }

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    # Print summary to stdout
    print(f"[evolve_runner v{RUNNER_VERSION}] Run complete.")
    print(f"  Control atom : {atom.get('control_id')} v{atom.get('version')}")
    print(f"  Object ID    : {state.get('object_id')}")
    print(f"  Route        : {route['route_decision']}")
    print(f"  Verification : {verification['verification_state']}")
    if verification.get("blocking_gaps"):
        print(f"  Blocking gaps: {verification['blocking_gaps']}")
    print(f"  Gate         : {gate['gate_decision']}")
    print(f"  Output       : {out_path}")


# ── ENTRYPOINT ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Evolve Edge ARM-B Static Runner v1.0.0"
    )
    parser.add_argument(
        "--atom", required=True,
        help="Path to control_atom.json"
    )
    parser.add_argument(
        "--state", required=True,
        help="Path to live_state.json"
    )
    parser.add_argument(
        "--out", required=True,
        help="Path to write evolve_output.json"
    )
    args = parser.parse_args()
    run(args.atom, args.state, args.out)
