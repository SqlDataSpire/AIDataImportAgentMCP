"""The proposal object on a proposed signature (design §8.18).

{"candidates": [Candidate.to_dict(), ...],
 "review": {state, draft_version_id, path, rationale, preview, notes, history}}
States used: needs_mapping, ready_for_review, accepted, rejected.
"""

from __future__ import annotations

import copy


def new_review() -> dict:
    return {"state": "needs_mapping", "draft_version_id": None, "path": None, "rationale": [],
            "preview": None, "notes": [], "history": []}


def normalize(proposal) -> dict:
    if proposal is None:
        return {"candidates": [], "review": new_review()}
    if isinstance(proposal, list):  # written by M2a
        return {"candidates": copy.deepcopy(proposal), "review": new_review()}
    return {"candidates": copy.deepcopy(proposal.get("candidates", [])),
            "review": {**new_review(), **copy.deepcopy(proposal.get("review", {}))}}


def with_candidates(existing, candidates: list[dict]) -> dict:
    proposal = normalize(existing)
    proposal["candidates"] = copy.deepcopy(list(candidates))
    return proposal
