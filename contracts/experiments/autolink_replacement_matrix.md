## EAS-125 AutoLink replacement-candidate matrix

AutoLink at `26c723158445d5c831290315c9a93ba76eb5bd0e` is not runnable under the
frozen contract: `run/main.sh` has SHA-256
`6b1c9e67b22c147c5e3242d3c58599139e95af74d1323518351abb68a55b7e10`
and clears both `OPENAI_API_KEY` and `OPENAI_BASE_URL`. The adapter must not
patch that source or change AutoLink's search/link/generation semantics.

| Candidate | Official code / exact observed ref | License evidence | Panel / method route | EAST S0 compatibility and adapter work | Decision |
|---|---|---|---|---|---|
| CHESS | `https://github.com/ShayanTalaei/CHESS.git` @ `3d6e835f858d26885d21d4bc0215aeecf855efbe` | README reports Apache-2.0; file-level hash audit still required | BIRD multi-agent SQL synthesis | Requires BIRD input conversion and a temporary provider configuration; no Gold/evidence fields may be supplied | Candidate A: source audit required before adoption |
| DAIL-SQL | `https://github.com/BeachWang/DAIL-SQL.git` @ `2061f68112222083134a0c9e2877961ff315ff44` | `LICENSE.txt` exists; exact license conclusion still requires file audit | Spider few-shot Text-to-SQL | Requires retrieval-example isolation and OpenAI-compatible provider mapping; source-public self-run required | Candidate B: license/provider audit required before adoption |

This is a replacement **candidate** matrix, not a baseline substitution or
performance claim. It contains no S0, credentials, model response, or upstream
source copy. Sol must choose any replacement before implementation starts.
