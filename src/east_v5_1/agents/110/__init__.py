"""V5.1 Agent 110 — Question Normalization & Intent Extraction (upgraded entry point).

In V5, Agent 110 performs query binding. In V5.1, Agent 110 is upgraded to be
the pipeline entry point for the 'already-have-question' scenario. It:

1. Parses the input question to extract table names, field names, predicate
   conditions, and requested return fields.
2. Detects common quality issues (customer-type ambiguity, account-detail
   clarity, return-field completeness).
3. Flags ambiguous questions for human correction.
4. Produces a normalized question + answer_contract package for downstream
   consumption by Agent 140 (whose input source is now 110+000 instead of
   120+130).
"""
