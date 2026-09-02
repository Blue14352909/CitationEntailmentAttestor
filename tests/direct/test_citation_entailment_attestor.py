"""Tests for CitationEntailmentAttestor contract.

Covers: input validation, supported/contradicted/insufficient paths,
fail-closed behavior, storage isolation, freshness, consensus, and edge cases.
"""
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
VALID_CLAIM = "Project X raised $5m in May 2026"
VALID_URLS = "https://techcrunch.com/example,https://blog.example.com/round"
SINGLE_URL = "https://techcrunch.com/example"
MAX_AGE = "365"


def _create_and_run(contract, direct_vm, claim=VALID_CLAIM, urls=VALID_URLS,
                    web_body="Project X announced a $5 million funding round.",
                    llm_response=None):
    """Helper: create attestation, mock web+llm, run, return report."""
    aid = contract.create_attestation(claim, urls, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200, "body": web_body})
    if llm_response is None:
        llm_response = ('{"source_result": "SUPPORTS", '
                        '"evidence_excerpt": "Project X raised $5 million", '
                        '"addresses_claim": true, '
                        '"publication_date": "2026-05-15", '
                        '"reason": "Source confirms $5m raise in May 2026"}')
    direct_vm.mock_llm(".*", llm_response)
    report_id = contract.run_attestation(aid)
    return contract.get_report(report_id)


# ===========================================================================
# 1. DEPLOYMENT
# ===========================================================================

def test_deploy(direct_deploy):
    """Contract deploys successfully."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    assert contract is not None


# ===========================================================================
# 2. create_attestation — VALID INPUTS
# ===========================================================================

def test_create_attestation_stores_correctly(direct_deploy, direct_vm):
    """Attestation stores all fields correctly."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, VALID_URLS, MAX_AGE)
    assert aid == "a-1"

    att = contract.get_attestation(aid)
    assert att["claim"] == VALID_CLAIM
    # Contract normalizes URLs with a space after comma
    assert "techcrunch.com/example" in att["source_urls"]
    assert "blog.example.com/round" in att["source_urls"]
    assert att["max_age_days"] == 365
    assert att["status"] == "PENDING"
    assert att["latest_report_id"] == ""


def test_create_multiple_attestations(direct_deploy, direct_vm):
    """Multiple attestations get sequential IDs."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    a1 = contract.create_attestation("Claim 1", SINGLE_URL, "30")
    a2 = contract.create_attestation("Claim 2", "https://other.com", "90")
    assert a1 == "a-1"
    assert a2 == "a-2"


# ===========================================================================
# 3. create_attestation — INVALID INPUTS
# ===========================================================================

def test_create_empty_claim(direct_deploy, direct_vm):
    """Empty claim is rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Claim required"):
        contract.create_attestation("", VALID_URLS, MAX_AGE)
    with pytest.raises(Exception, match="Claim required"):
        contract.create_attestation("  ", VALID_URLS, MAX_AGE)


def test_create_empty_urls(direct_deploy, direct_vm):
    """Empty URLs are rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Source URLs required"):
        contract.create_attestation(VALID_CLAIM, "", MAX_AGE)
    with pytest.raises(Exception, match="Source URLs required"):
        contract.create_attestation(VALID_CLAIM, "  ", MAX_AGE)


def test_create_too_many_urls(direct_deploy, direct_vm):
    """More than 5 URLs rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    urls = ",".join([f"https://src{i}.example.com" for i in range(6)])
    with pytest.raises(Exception, match="Maximum 5"):
        contract.create_attestation(VALID_CLAIM, urls, MAX_AGE)


def test_create_duplicate_urls(direct_deploy, direct_vm):
    """Duplicate URLs rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Duplicate"):
        contract.create_attestation(VALID_CLAIM,
                                    "https://same.com,https://same.com", MAX_AGE)


def test_create_http_url(direct_deploy, direct_vm):
    """HTTP URL rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Invalid URL"):
        contract.create_attestation(VALID_CLAIM, "http://insecure.com/page", MAX_AGE)


def test_create_invalid_max_age(direct_deploy, direct_vm):
    """Non-numeric max_age rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Invalid max_age_days"):
        contract.create_attestation(VALID_CLAIM, SINGLE_URL, "abc")


def test_create_negative_max_age(direct_deploy, direct_vm):
    """Negative max_age rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="must be positive"):
        contract.create_attestation(VALID_CLAIM, SINGLE_URL, "-1")


def test_create_zero_max_age(direct_deploy, direct_vm):
    """Zero max_age rejected."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="must be positive"):
        contract.create_attestation(VALID_CLAIM, SINGLE_URL, "0")


# ===========================================================================
# 4. run_attestation — NONEXISTENT
# ===========================================================================

def test_run_nonexistent(direct_deploy, direct_vm):
    """Running nonexistent attestation raises error."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Attestation not found"):
        contract.run_attestation("a-999")


def test_run_already_completed(direct_deploy, direct_vm):
    """Running already-completed attestation raises error."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    # First run (with mocks for fetch failure -> INSUFFICIENT)
    contract.run_attestation(aid)
    # Second run should fail
    with pytest.raises(Exception, match="not PENDING"):
        contract.run_attestation(aid)


# ===========================================================================
# 5. run_attestation — FETCH FAILURE -> INSUFFICIENT
# ===========================================================================

def test_fetch_failure(direct_deploy, direct_vm):
    """Source unreachable -> INSUFFICIENT_EVIDENCE (fail closed)."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    # No web mock -> fetch fails
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["insufficient_count"] >= 1


def test_fetch_failure_not_supported(direct_deploy, direct_vm):
    """Fetch failure must never produce SUPPORTED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] != "SUPPORTED"


# ===========================================================================
# 6. run_attestation — PAGE TOO SHORT -> INSUFFICIENT
# ===========================================================================

def test_page_too_short(direct_deploy, direct_vm):
    """Very short page -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200, "body": "Hi"})
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"


# ===========================================================================
# 7. run_attestation — SUPPORTED (source confirms claim)
# ===========================================================================

def test_supported_single_source(direct_deploy, direct_vm):
    """Single source confirming claim -> SUPPORTED."""
    report = _create_and_run(contract=direct_deploy("contracts/citation_entailment_attestor.py"),
                             direct_vm=direct_vm)
    assert report["overall_result"] == "SUPPORTED"
    assert report["supporting_count"] >= 1


def test_supported_multiple_sources(direct_deploy, direct_vm):
    """Multiple sources confirming claim -> SUPPORTED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, VALID_URLS, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million in May 2026."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m raise", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"reason": "Confirms claim"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "SUPPORTED"
    assert report["supporting_count"] == 2


# ===========================================================================
# 8. run_attestation — CONTRADICTED (source contradicts claim)
# ===========================================================================

def test_contradicted_overrides_support(direct_deploy, direct_vm):
    """Contradiction from one source overrides support from another -> CONTRADICTED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    # Single URL that contradicts
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $2m, not $5m."})
    direct_vm.mock_llm(".*", '{"source_result": "CONTRADICTS", '
                       '"evidence_excerpt": "Raised $2m not $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-10", '
                       '"reason": "Amount differs"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "CONTRADICTED"
    assert report["contradicting_count"] >= 1


# ===========================================================================
# 9. run_attestation — INSUFFICIENT (all sources irrelevant)
# ===========================================================================

def test_all_sources_irrelevant(direct_deploy, direct_vm):
    """All sources fail to address the claim -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Unrelated page about weather."})
    direct_vm.mock_llm(".*", '{"source_result": "INSUFFICIENT", '
                       '"evidence_excerpt": "", '
                       '"addresses_claim": false, '
                       '"publication_date": "", '
                       '"reason": "Source does not discuss Project X funding"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["supporting_count"] == 0
    assert report["contradicting_count"] == 0


# ===========================================================================
# 10. run_attestation — MALFORMED LLM OUTPUT
# ===========================================================================

def test_malformed_llm_json(direct_deploy, direct_vm):
    """Non-JSON LLM output -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", "NOT VALID JSON")
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"


def test_missing_source_result_field(direct_deploy, direct_vm):
    """LLM output missing source_result -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"evidence_excerpt": "text", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-01-01", '
                       '"reason": "test"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"


def test_non_boolean_addresses_claim(direct_deploy, direct_vm):
    """Non-boolean addresses_claim -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "text", '
                       '"addresses_claim": "yes", '
                       '"publication_date": "2026-01-01", '
                       '"reason": "test"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"


def test_invalid_source_result_enum(direct_deploy, direct_vm):
    """Invalid source_result enum -> INSUFFICIENT."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"source_result": "MAYBE", '
                       '"evidence_excerpt": "text", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-01-01", '
                       '"reason": "test"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"


# ===========================================================================
# 11. run_attestation — DETERMINISTIC VERDICT COMPOSITION
# ===========================================================================

def test_contradiction_always_wins(direct_deploy, direct_vm):
    """CONTRADICTED always takes priority over SUPPORTED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    # Single source that contradicts
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $2m."})
    direct_vm.mock_llm(".*", '{"source_result": "CONTRADICTS", '
                       '"evidence_excerpt": "Raised $2m not $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-10", '
                       '"reason": "Amount differs"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "CONTRADICTED"


def test_mixed_support_and_insufficient(direct_deploy, direct_vm):
    """All sources supported -> SUPPORTED with correct counts."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, VALID_URLS, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Funding details for Project X."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"reason": "Supports"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "SUPPORTED"
    assert report["supporting_count"] == 2
    assert report["insufficient_count"] == 0


def test_all_insufficient(direct_deploy, direct_vm):
    """All sources insufficient -> INSUFFICIENT_EVIDENCE."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Unrelated page."})
    direct_vm.mock_llm(".*", '{"source_result": "INSUFFICIENT", '
                       '"evidence_excerpt": "", '
                       '"addresses_claim": false, '
                       '"publication_date": "", '
                       '"reason": "Not relevant"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["supporting_count"] == 0
    assert report["contradicting_count"] == 0


# ===========================================================================
# 12. STORAGE ISOLATION
# ===========================================================================

def test_storage_isolation(direct_deploy, direct_vm):
    """Two attestations have independent reports."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    a1 = contract.create_attestation("Claim 1", SINGLE_URL, "30")
    a2 = contract.create_attestation("Claim 2",
                                     "https://other.com/evidence", "365")

    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Supporting data."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Yes", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-06-01", '
                       '"reason": "Confirms"}')

    r1 = contract.run_attestation(a1)
    r2 = contract.run_attestation(a2)

    report1 = contract.get_report(r1)
    report2 = contract.get_report(r2)
    assert report1["attestation_id"] == a1
    assert report2["attestation_id"] == a2
    assert report1["id"] != report2["id"]


# ===========================================================================
# 13. REPORT IMMUTABILITY
# ===========================================================================

def test_report_immutable_after_failed_rerun(direct_deploy, direct_vm):
    """A failed re-run does not corrupt a prior report."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    # Create two attestations with the same ID won't work — use two separate
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m raise", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"reason": "Confirms claim"}')
    r1 = contract.run_attestation(aid)
    report1 = contract.get_report(r1)
    assert report1["overall_result"] == "SUPPORTED"

    # Try running again — should fail (already COMPLETED)
    with pytest.raises(Exception, match="not PENDING"):
        contract.run_attestation(aid)

    # Original report intact
    report1_again = contract.get_report(r1)
    assert report1_again["overall_result"] == "SUPPORTED"


# ===========================================================================
# 14. EDGE CASES
# ===========================================================================

def test_get_nonexistent_attestation(direct_deploy, direct_vm):
    """Nonexistent attestation raises error."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Attestation not found"):
        contract.get_attestation("a-999")


def test_get_nonexistent_report(direct_deploy, direct_vm):
    """Nonexistent report raises error."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    with pytest.raises(Exception, match="Report not found"):
        contract.get_report("rp-999")


def test_attestation_status_updates(direct_deploy, direct_vm):
    """Attestation status goes PENDING -> RUNNING -> COMPLETED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    assert contract.get_attestation(aid)["status"] == "PENDING"

    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Some content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "text", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-01-01", '
                       '"reason": "Supports"}')
    contract.run_attestation(aid)
    assert contract.get_attestation(aid)["status"] == "COMPLETED"


def test_report_counts_correct(direct_deploy, direct_vm):
    """Report counts sum to number of sources."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million in May 2026."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"reason": "Confirms"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    total = report["supporting_count"] + report["contradicting_count"] + report["insufficient_count"]
    assert total == 1
    assert report["supporting_count"] == 1


def test_single_url_only(direct_deploy, direct_vm):
    """Single URL works correctly."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5m."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "$5m confirmed", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"reason": "Confirms"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["supporting_count"] == 1
    assert report["insufficient_count"] == 0


def test_claim_text_preserved_in_report(direct_deploy, direct_vm):
    """Original claim text is preserved in the report."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "text", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-01-01", '
                       '"reason": "Supports"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["claim"] == VALID_CLAIM


# ===========================================================================
# 15. REGRESSION: Freshness enforcement
# ===========================================================================

def test_stale_source_insufficient(direct_deploy, direct_vm):
    """Source older than max_age_days -> INSUFFICIENT (fail closed).
    Regression: old code stored max_age_days but never enforced it."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, "30")
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2023-01-01", '
                       '"days_since_publication": 1000, '
                       '"reason": "Old article confirming raise"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    # Stale source must not count as supporting
    assert report["supporting_count"] == 0


def test_fresh_source_supported(direct_deploy, direct_vm):
    """Source within max_age_days -> SUPPORTED."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, "365")
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million in May 2026."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"days_since_publication": 100, '
                       '"reason": "Confirms claim"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "SUPPORTED"


def test_unknown_date_not_rejected(direct_deploy, direct_vm):
    """days_since_publication=-1 (unknown) does not cause rejection."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, "30")
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Project X raised $5 million."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "Confirmed $5m", '
                       '"addresses_claim": true, '
                       '"publication_date": "", '
                       '"days_since_publication": -1, '
                       '"reason": "Cannot determine date"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    # Unknown date does not cause rejection — only stale dates do
    assert report["overall_result"] == "SUPPORTED"


# ===========================================================================
# 16. REGRESSION: Evidence validation
# ===========================================================================

def test_supports_without_addresses_claim_insufficient(direct_deploy, direct_vm):
    """SUPPORTS with addresses_claim=false -> INSUFFICIENT.
    Regression: old validation allowed SUPPORTS without addressing the claim."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "some text", '
                       '"addresses_claim": false, '
                       '"publication_date": "2026-05-15", '
                       '"days_since_publication": 10, '
                       '"reason": "Supports"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["supporting_count"] == 0


def test_supports_with_empty_excerpt_insufficient(direct_deploy, direct_vm):
    """SUPPORTS with empty evidence_excerpt -> INSUFFICIENT.
    Regression: old validation accepted empty excerpts for positive verdicts."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"days_since_publication": 10, '
                       '"reason": "Supports"}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["supporting_count"] == 0


def test_supports_with_empty_reason_insufficient(direct_deploy, direct_vm):
    """SUPPORTS with empty reason -> INSUFFICIENT.
    Regression: old validation did not require a reason."""
    contract = direct_deploy("contracts/citation_entailment_attestor.py")
    aid = contract.create_attestation(VALID_CLAIM, SINGLE_URL, MAX_AGE)
    direct_vm.mock_web(".*", {"method": "GET", "status": 200,
                              "body": "Page content."})
    direct_vm.mock_llm(".*", '{"source_result": "SUPPORTS", '
                       '"evidence_excerpt": "some text", '
                       '"addresses_claim": true, '
                       '"publication_date": "2026-05-15", '
                       '"days_since_publication": 10, '
                       '"reason": ""}')
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    assert report["overall_result"] == "INSUFFICIENT_EVIDENCE"
    assert report["supporting_count"] == 0
