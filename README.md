# CitationEntailmentAttestor

**Source-claim verification using GenLayer consensus.**

CitationEntailmentAttestor determines whether a set of public sources actually supports, contradicts, or fails to establish a precise claim. It makes "show your sources" enforceable on-chain.

## What It Does

Research agents, DAO proposals, and grant applications often cite sources that look convincing but do not actually support the claims. This contract independently retrieves each source and returns:

- `SUPPORTED` — at least one credible source confirms the exact claim
- `CONTRADICTED` — at least one source contradicts the claim
- `INSUFFICIENT_EVIDENCE` — no source conclusively establishes the claim

## Why GenLayer

CitationEntailmentAttestor uses GenLayer's nondeterministic consensus. Multiple validators independently retrieve and evaluate each source. The LLM extracts per-source findings; the final verdict is composed deterministically — never by the LLM.

## Architecture

### State Model

Each attestation stores:
- Attestation ID, creator, exact claim text
- Source URL list (1–5 HTTPS URLs)
- Freshness requirement (max age in days)
- Status: `PENDING` → `RUNNING` → `COMPLETED`

Each report stores:
- Overall result: `SUPPORTED`, `CONTRADICTED`, or `INSUFFICIENT_EVIDENCE`
- Supporting/contradicting/insufficient source counts
- Per-source findings (URL, evidence excerpt, result, reason)

### Deterministic Verdict Rules

| Condition | Result |
|---|---|
| At least one source contradicts the exact claim | `CONTRADICTED` |
| At least one supports, none contradict, threshold met | `SUPPORTED` |
| Anything missing, stale, ambiguous, malformed, unavailable | `INSUFFICIENT_EVIDENCE` |

The LLM chooses per-source findings only. The overall verdict is pure code.

### Fail-Closed Invariants

- Source fetch failure → `INSUFFICIENT` for that source
- Page too short → `INSUFFICIENT`
- Malformed LLM output → `INSUFFICIENT`
- Non-boolean fields → `INSUFFICIENT`
- Invalid enum values → `INSUFFICIENT`
- Consensus failure → `INSUFFICIENT`
- Overall: `SUPPORTED` only when at least one source explicitly supports

## Installation

```bash
pip install -r requirements.txt
```

## Testing

```bash
# Direct tests (local GenLayer VM)
python -m pytest tests/direct/ -v

# Lint
PYTHONIOENCODING=utf-8 genvm-lint contracts/citation_entailment_attestor.py
```

## Known Limitations

- Evaluates whether supplied sources support a claim; does not establish objective truth.
- Does not assess whether a source is authoritative or credible — it accepts any public HTTPS URL.
- Structured JSON output format constrains the LLM but does not fully prevent hostile source content from influencing the assessment.
- Maximum 5 sources per attestation.
- Freshness is checked via LLM-extracted publication dates, not cryptographic timestamps.
- A steward reviewing submissions: this is a source-attestation primitive, not a fact oracle.

## Dependencies

- genlayer-py v0.18
- genlayer-test v0.29
- genvm-linter 0.11.0
- pytest >=7.0.0
