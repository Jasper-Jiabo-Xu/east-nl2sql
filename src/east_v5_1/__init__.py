"""EAST V5.1 adaptation layer.

V5.1 adapts the V5 pipeline for the 'already-have-question' scenario:
question (+ manual corrections) → 110 (normalize) → 000 → 140 → 150 → 160 → 170/180 → 260.

Reuses V5 governance (east_v5.governance), artifacts (east_v5.artifacts),
and existing agent implementations. Only entry logic is new/modified.
"""
