"""V5.1 Agent 140 — Query Spec (adapted input source).

In V5, Agent 140 receives input from 120+130. In V5.1, its input source is
changed to 110 (normalized question) + 000 (constraint retrieval). The core
query-spec logic is reused from V5's east_140 extractor, but the input adapter
is modified to consume the V5.1 110 output package.
"""
