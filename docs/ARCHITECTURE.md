# CitationEntailmentAttestor Architecture

## Overview

CitationEntailmentAttestor is a GenLayer Intelligent Contract that determines whether public sources support, contradict, or fail to establish a precise claim. It uses GenLayer's consensus model for independent source evaluation and deterministic verdict composition.

## Storage Layout

```
attestations: TreeMap[str, str]  # attestation_id -> JSON(Attestation)
reports: TreeMap[str, str]       # report_id -> JSON(Report)
attestation_counter: u256        # auto-increment attestation IDs
report_counter: u256             # auto-increment report IDs
```

## Data Schema

### Attestation
- `id`: Unique identifier (a-1, a-2, ...)
- `creator`: Address that created the attestation
- `claim`: Exact claim text to verify
- `source_urls`: Comma-separated HTTPS URLs (1-5)
- `max_age_days`: Freshness requirement
- `status`: PENDING | RUNNING | COMPLETED
- `latest_report_id`: Most recent report

### Report
- `id`: Unique identifier (rp-1, rp-2, ...)
- `attestation_id`: Parent attestation
- `claim`: Original claim text
- `overall_result`: SUPPORTED | CONTRADICTED | INSUFFICIENT_EVIDENCE
- `supporting_count`: Number of supporting sources
- `contradicting_count`: Number of contradicting sources
- `insufficient_count`: Number of inconclusive sources
- `reason`: Human-readable summary
- `source_results`: List of per-source findings

### Per-Source Result
- `source_url`: The URL evaluated
- `source_result`: SUPPORTS | CONTRADICTS | INSUFFICIENT
- `evidence_excerpt`: Direct quote or summary from source
- `addresses_claim`: Whether source discusses the exact claim
- `publication_date`: Date if available
- `reason`: Assessment explanation

## State Transitions

```
Attestation:
  create_attestation() -> PENDING
  run_attestation()    -> RUNNING -> COMPLETED

Report (per run_attestation call):
  SUPPORTED         (at least one source supports, none contradict)
  CONTRADICTED      (at least one source contradicts)
  INSUFFICIENT_EVIDENCE (all sources inconclusive, malformed, or missing)
```

## Consensus Model

1. Attestation data copied to memory before non-deterministic block.
2. For each source URL:
   a. Leader fetches the URL via `gl.nondet.web.render`.
   b. Leader calls `gl.nondet.exec_prompt` to extract structured JSON.
   c. Validator independently re-fetches and re-evaluates.
   d. `gl.vm.run_nondet_unsafe` compares the structured verdict.
   e. Validators must agree on both `source_result` and `addresses_claim`.
3. Overall verdict composed deterministically from per-source results.

## Deterministic Verdict Composition

Per-source LLM output:
- `source_result`: SUPPORTS | CONTRADICTS | INSUFFICIENT
- `addresses_claim`: boolean
- `evidence_excerpt`: string

Contract logic:
- Any source CONTRADICTS → overall CONTRADICTED
- At least one SUPPORTS, none CONTRADICTS → overall SUPPORTED
- Otherwise → overall INSUFFICIENT_EVIDENCE

## Authorization

- `create_attestation`: Any address (creates a new claim verification)
- `run_attestation`: Any address (permissionless, deterministic result)
- `get_attestation`, `get_report`: Any address (read-only)

## Freshness Enforcement

- `max_age_days` is stored per attestation.
- The LLM extracts `days_since_publication` alongside the source assessment.
- The leader function enforces freshness: if `days_since_publication >= max_age_days`, the source is forced to `INSUFFICIENT`.
- Stale sources cannot contribute to a SUPPORTED or CONTRADICTED verdict.
- Unknown dates (`days_since_publication = -1`) do not cause rejection.

## Evidence Validation

- SUPPORTS or CONTRADICTS requires `addresses_claim = true` and a non-empty `evidence_excerpt`.
- Empty `reason` is rejected.
- These checks prevent the contract from accepting unsupported positive/negative verdicts.

## Limitations

- Evaluates supplied sources only; does not establish objective truth.
- Does not assess whether a source is authoritative or credible.
- Structured JSON output constrains the LLM but does not fully prevent hostile content influence.
- Maximum 5 sources per attestation.
- Freshness relies on LLM-extracted dates, not cryptographic timestamps.
- Cannot verify sources behind authentication.
- Assumes HTTPS URLs are publicly accessible.

## Threat Model

- Prompt injection via source content: mitigated by structured output and deterministic verdict composition.
- Malicious sources: cannot produce SUPPORTED without explicit consensus on a supporting finding.
- Stale sources: freshness enforcement prevents outdated evidence from supporting claims.
- LLM hallucination: validator independently evaluates; consensus requires agreement on status and addresses_claim.
- Stale sources: freshness requirement is enforced via max_age_days.
