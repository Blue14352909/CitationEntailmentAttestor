# CitationEntailmentAttestor — Submission Document

## Project Name
CitationEntailmentAttestor

## One-liner
GenLayer contract that determines whether public sources support, contradict, or fail to establish a precise claim.

## Description
CitationEntailmentAttestor makes "show your sources" enforceable on-chain. Given a claim and 1–5 HTTPS sources, it independently retrieves each and returns SUPPORTED, CONTRADICTED, or INSUFFICIENT_EVIDENCE. The LLM extracts per-source findings; deterministic contract logic composes the final verdict. Useful for grant reviews, DAO proposals, research agents, and compliance workflows.

## Focus Tags
- Research Verification
- Compliance
- Source Attestation

## User Path / Demo Flow
1. Create an attestation with a claim, source URLs, and freshness requirement
2. Call run_attestation to execute the consensus-backed evaluation
3. Receive a report with overall result and per-source breakdown
4. Review individual source findings (evidence excerpts, dates, results)

## Evidence Links
- **GitHub repository:** [placeholder]
- **Passing CI run:** [placeholder]
- **Final Studio deployment:** [placeholder]
- **Successful attestation result:** [placeholder]

## Test Results
- 46 direct VM tests
- GenVM lint: clean
- CI: Ubuntu/Python 3.12, pinned dependencies

## Known Limitations
- Evaluates supplied sources only; does not establish objective truth
- Maximum 5 sources per attestation
- Freshness is LLM-extracted, not cryptographically verified
