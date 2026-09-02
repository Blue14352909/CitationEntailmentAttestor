# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
CitationEntailmentAttestor — Source-Claim Verification

A GenLayer Intelligent Contract that determines whether a set of public
sources actually supports, contradicts, or fails to establish a precise
claim.

Use case: Research agents produce citations that look convincing but do
not actually support their claims. This contract makes "show your sources"
enforceable.

Example:
    contract = CitationEntailmentAttestor()
    aid = contract.create_attestation(
        "Project X raised $5m in May 2026",
        "https://techcrunch.com/example,https://blog.example.com/round",
        "365"
    )
    report_id = contract.run_attestation(aid)
    report = contract.get_report(report_id)
    # report["overall_result"] == "SUPPORTED" | "CONTRADICTED" | "INSUFFICIENT_EVIDENCE"

Deterministic final-verdict rules:
    At least one credible source contradicts -> CONTRADICTED
    At least one supports, none contradict, threshold met -> SUPPORTED
    Anything missing, stale, ambiguous, malformed, unavailable -> INSUFFICIENT_EVIDENCE

The LLM extracts structured per-source findings; deterministic contract
logic composes the final verdict.
"""
import json
import re
from dataclasses import dataclass
from genlayer import *


# ---------------------------------------------------------------------------
# Status constants
# ---------------------------------------------------------------------------
E_PASS = "PASS"
E_FAIL = "FAIL"
E_FETCH_FAILED = "FETCH_FAILED"
E_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

SUPPORTED = "SUPPORTED"
CONTRADICTED = "CONTRADICTED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

SOURCE_SUPPORTS = "SUPPORTS"
SOURCE_CONTRADICTS = "CONTRADICTS"
SOURCE_INSUFFICIENT = "INSUFFICIENT"

VALID_OVERALL = {SUPPORTED, CONTRADICTED, INSUFFICIENT_EVIDENCE}
VALID_SOURCE = {SOURCE_SUPPORTS, SOURCE_CONTRADICTS, SOURCE_INSUFFICIENT}

MAX_SOURCES = 5


# ---------------------------------------------------------------------------
# Storage types
# ---------------------------------------------------------------------------
@allow_storage
@dataclass
class Attestation:
    id: str
    creator: Address
    claim: str
    source_urls: str
    max_age_days: u256
    status: str
    latest_report_id: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sanitize_content(raw: str, max_len: int = 4000) -> str:
    """Sanitize page content."""
    if not raw:
        return ""
    cleaned = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.DOTALL)
    cleaned = re.sub(r"<style[^>]*>.*?</style>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:max_len]


def _parse_json_response(raw) -> dict:
    """Parse LLM JSON output."""
    if isinstance(raw, dict):
        return raw
    text = str(raw)
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1:
        raise gl.vm.UserError("No JSON object found in response")
    text = text[first : last + 1]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise gl.vm.UserError(f"Invalid JSON from evaluator: {e}")


def _validate_url(url: str) -> bool:
    """HTTPS URL input validation."""
    if not url or not url.strip():
        return False
    url = url.strip()
    blocked = [
        r"^http://", r"localhost", r"127\.\d+\.\d+\.\d+",
        r"10\.\d+\.\d+\.\d+", r"172\.(1[6-9]|2\d|3[01])\.",
        r"192\.168\.", r"\[::1\]", r"0\.0\.0\.0",
        r"^file://", r"^javascript:", r"^data:",
    ]
    for pattern in blocked:
        if re.search(pattern, url, re.IGNORECASE):
            return False
    return url.startswith("https://")


def _fetch_page_text(url: str) -> str:
    """Fetch browser-rendered text first, fall back to static HTML."""
    try:
        raw = gl.nondet.web.render(url, mode="text")
        content = _sanitize_content(str(raw))
        if content:
            return content
    except Exception:
        pass
    try:
        response = gl.nondet.web.get(url)
        body = response.body
        text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
        content = _sanitize_content(text)
        if content:
            return content
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# LLM prompt for per-source extraction
# ---------------------------------------------------------------------------
_SOURCE_EXTRACTION_PROMPT = """\
You are a precise source analyst. Evaluate whether this source supports or contradicts the claim.

CLAIM:
{claim}

SOURCE URL:
{url}

PAGE CONTENT:
{page_content}

FRESHNESS REQUIREMENT:
This source must have been published or updated within {max_age_days} days to be considered credible.

Your task:
1. Determine whether this source addresses the exact claim.
2. If it does, determine whether it supports or contradicts the claim.
3. Extract a concise evidence excerpt directly from the source.
4. Identify the publication date if available and compute how many days ago it was published.

Return a JSON object with these exact fields:
{{
  "source_result": "SUPPORTS" or "CONTRADICTS" or "INSUFFICIENT",
  "evidence_excerpt": "direct quote or concise summary from the source",
  "addresses_claim": true or false,
  "publication_date": "YYYY-MM-DD or empty if unavailable",
  "days_since_publication": 0,
  "reason": "one sentence explaining your assessment"
}}

Rules:
- A source that does not discuss the exact claim must be INSUFFICIENT.
- A source that discusses a similar but different claim must be INSUFFICIENT.
- Do not treat page titles or search snippets as support.
- Only set SUPPORTS or CONTRADICTS when the evidence is explicit.
- If the source is unreachable, too short, or has no relevant content, set source_result to INSUFFICIENT.
- If you cannot determine the publication date, set days_since_publication to -1.

It is mandatory that you respond only using the JSON format above, \
nothing else. Your output must be only JSON without any formatting \
prefix or suffix.
"""


def _validate_source_fields(parsed: dict) -> str | None:
    """Validate extracted source fields. Returns None if valid, error string otherwise."""
    if not isinstance(parsed, dict):
        return "Response is not a dict"

    source_result = parsed.get("source_result", "")
    if source_result not in VALID_SOURCE:
        return f"Invalid source_result: {source_result}"

    addresses_claim = parsed.get("addresses_claim")
    if not isinstance(addresses_claim, bool):
        return "addresses_claim must be a boolean"

    evidence_excerpt = parsed.get("evidence_excerpt", "")
    if not isinstance(evidence_excerpt, str):
        return "evidence_excerpt must be a string"

    reason = parsed.get("reason", "")
    if not isinstance(reason, str) or not reason.strip():
        return "reason must be a non-empty string"

    # If source claims to support or contradict, it must address the claim
    # and must provide a non-empty evidence excerpt
    if source_result in (SOURCE_SUPPORTS, SOURCE_CONTRADICTS):
        if not addresses_claim:
            return f"source_result is {source_result} but addresses_claim is false"
        if not evidence_excerpt.strip():
            return f"source_result is {source_result} but evidence_excerpt is empty"

    return None


def _is_source_stale(parsed: dict, max_age_days: int) -> bool:
    """Check if source is older than max_age_days. Returns True if stale.

    Fail-closed: if the age cannot be validly established, treat as stale.
    A source whose age is unknown cannot prove freshness and must not
    contribute to SUPPORTED or CONTRADICTED.
    """
    days_raw = parsed.get("days_since_publication")
    if days_raw is None:
        return True  # Age unknown -> cannot prove freshness -> stale
    if isinstance(days_raw, bool):
        return True  # Boolean is not a valid age -> stale
    if isinstance(days_raw, str):
        return True  # String is not a valid numeric age -> stale
    if isinstance(days_raw, float):
        return True  # Fractional is not a valid integer age -> stale
    if not isinstance(days_raw, int):
        return True  # Non-numeric type -> stale
    if days_raw < 0:
        return True  # Negative -> age unknown -> stale
    return days_raw >= max_age_days


def _deterministic_overall_verdict(source_results: list) -> dict:
    """Compose overall verdict from per-source findings. Deterministic, no LLM.

    Rules:
        At least one CONTRADICTS -> CONTRADICTED
        At least one SUPPORTS, none CONTRADICTS -> SUPPORTED
        Otherwise -> INSUFFICIENT_EVIDENCE
    """
    contradict_count = 0
    support_count = 0
    insufficient_count = 0

    for sr in source_results:
        result = sr.get("source_result", SOURCE_INSUFFICIENT)
        if result == SOURCE_CONTRADICTS:
            contradict_count += 1
        elif result == SOURCE_SUPPORTS:
            support_count += 1
        else:
            insufficient_count += 1

    # Any contradiction -> CONTRADICTED (highest priority)
    if contradict_count > 0:
        return {
            "overall_result": CONTRADICTED,
            "supporting_count": support_count,
            "contradicting_count": contradict_count,
            "insufficient_count": insufficient_count,
            "reason": f"{contradict_count} source(s) contradict the claim",
        }

    # At least one supports and none contradict -> SUPPORTED
    if support_count > 0 and contradict_count == 0:
        return {
            "overall_result": SUPPORTED,
            "supporting_count": support_count,
            "contradicting_count": 0,
            "insufficient_count": insufficient_count,
            "reason": f"{support_count} source(s) support the claim",
        }

    # Nothing conclusive
    return {
        "overall_result": INSUFFICIENT_EVIDENCE,
        "supporting_count": 0,
        "contradicting_count": 0,
        "insufficient_count": insufficient_count,
        "reason": "No source conclusively supports or contradicts the claim",
    }


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------
class CitationEntailmentAttestor(gl.Contract):
    """
    Determines whether public sources support, contradict, or fail to
    establish a precise claim.

    Uses GenLayer consensus for independent source retrieval and evaluation.
    Final verdict is deterministic: the LLM extracts per-source findings;
    contract logic composes the overall result.

    Hard invariants:
        CONTRADICTED if any source contradicts
        SUPPORTED only if at least one source supports and none contradict
        INSUFFICIENT for all other cases (missing, stale, malformed, etc.)
    """

    attestations: TreeMap[str, str]
    reports: TreeMap[str, str]
    attestation_counter: u256
    report_counter: u256

    def __init__(self):
        self.attestation_counter = u256(0)
        self.report_counter = u256(0)

    @gl.public.write
    def create_attestation(
        self,
        claim: str,
        source_urls: str,
        max_age_days: str,
    ) -> str:
        """
        Create a new claim attestation request.

        Args:
            claim: The exact claim to verify
            source_urls: Comma-separated HTTPS URLs (1-5 sources)
            max_age_days: Maximum age of sources in days (as string)

        Returns:
            Attestation ID string (e.g. "a-1")
        """
        if not claim or not claim.strip():
            raise gl.vm.UserError("Claim required")
        if not source_urls or not source_urls.strip():
            raise gl.vm.UserError("Source URLs required")

        urls = [u.strip() for u in source_urls.split(",") if u.strip()]
        if len(urls) == 0:
            raise gl.vm.UserError("No valid URLs provided")
        if len(urls) > MAX_SOURCES:
            raise gl.vm.UserError(f"Maximum {MAX_SOURCES} source URLs")
        if len(urls) != len(set(urls)):
            raise gl.vm.UserError("Duplicate URLs not allowed")

        for url in urls:
            if not _validate_url(url):
                raise gl.vm.UserError(f"Invalid URL: {url}")

        # Parse max_age_days
        days = 365
        if max_age_days and max_age_days.strip():
            try:
                days = int(max_age_days.strip())
            except ValueError:
                raise gl.vm.UserError(f"Invalid max_age_days: {max_age_days}")
        if days <= 0:
            raise gl.vm.UserError(f"max_age_days must be positive, got {days}")

        self.attestation_counter = self.attestation_counter + 1
        aid = f"a-{self.attestation_counter}"

        att = {
            "id": aid,
            "creator": str(gl.message.sender_address),
            "claim": claim.strip(),
            "source_urls": ", ".join(urls),
            "max_age_days": days,
            "status": "PENDING",
            "latest_report_id": "",
        }
        self.attestations[aid] = json.dumps(att)
        return aid

    @gl.public.write
    def run_attestation(self, attestation_id: str) -> str:
        """
        Execute the attestation pipeline.

        Fetches each source, extracts claim-relevant facts via LLM with
        consensus, then composes the final verdict deterministically.

        Args:
            attestation_id: The attestation to run

        Returns:
            Report ID string
        """
        if attestation_id not in self.attestations:
            raise gl.vm.UserError("Attestation not found")

        att = json.loads(self.attestations[attestation_id])
        if att["status"] != "PENDING":
            raise gl.vm.UserError(f"Attestation is {att['status']}, not PENDING")

        att["status"] = "RUNNING"
        self.attestations[attestation_id] = json.dumps(att)

        # Copy storage to memory before non-deterministic work
        mem_claim = att["claim"]
        mem_urls = [u.strip() for u in att["source_urls"].split(",") if u.strip()]
        mem_days = att["max_age_days"]

        source_results = []

        for url in mem_urls:
            def leader_fn(u=url) -> dict:
                content = _fetch_page_text(u)
                if not content or len(content.strip()) < 10:
                    return {
                        "source_url": u,
                        "source_result": SOURCE_INSUFFICIENT,
                        "evidence_excerpt": "",
                        "addresses_claim": False,
                        "publication_date": "",
                        "reason": "Page fetch failed or content too short",
                    }

                prompt = _SOURCE_EXTRACTION_PROMPT.format(
                    claim=mem_claim,
                    url=u,
                    page_content=content,
                    max_age_days=mem_days,
                )
                raw_result = gl.nondet.exec_prompt(prompt, response_format="json")
                parsed = _parse_json_response(raw_result)

                validation_error = _validate_source_fields(parsed)
                if validation_error:
                    return {
                        "source_url": u,
                        "source_result": SOURCE_INSUFFICIENT,
                        "evidence_excerpt": "",
                        "addresses_claim": False,
                        "publication_date": "",
                        "reason": f"Malformed output: {validation_error}",
                    }

                # Enforce freshness: stale sources cannot support or contradict
                if _is_source_stale(parsed, mem_days):
                    return {
                        "source_url": u,
                        "source_result": SOURCE_INSUFFICIENT,
                        "evidence_excerpt": parsed.get("evidence_excerpt", ""),
                        "addresses_claim": parsed.get("addresses_claim", False),
                        "publication_date": parsed.get("publication_date", ""),
                        "reason": f"Source is stale: {parsed.get('days_since_publication', '?')} days old, max {mem_days}",
                    }

                return {
                    "source_url": u,
                    "source_result": parsed["source_result"],
                    "evidence_excerpt": parsed.get("evidence_excerpt", ""),
                    "addresses_claim": parsed["addresses_claim"],
                    "publication_date": parsed.get("publication_date", ""),
                    "reason": parsed.get("reason", ""),
                }

            def validator_fn(leader_result, u=url) -> bool:
                if not isinstance(leader_result, gl.vm.Return):
                    return False
                leader_data = leader_result.calldata
                if not isinstance(leader_data, dict):
                    return False
                leader_status = leader_data.get("source_result", "")
                if leader_status not in VALID_SOURCE:
                    return False
                try:
                    validator_data = leader_fn()
                except Exception:
                    return False
                if not isinstance(validator_data, dict):
                    return False
                validator_status = validator_data.get("source_result", "")
                if validator_status not in VALID_SOURCE:
                    return False
                # Must agree on status AND addresses_claim
                if leader_status != validator_status:
                    return False
                leader_addresses = leader_data.get("addresses_claim", False)
                validator_addresses = validator_data.get("addresses_claim", False)
                if leader_addresses != validator_addresses:
                    return False
                return True

            try:
                result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)
                source_results.append(result)
            except Exception:
                # Consensus failure -> INSUFFICIENT for this source
                source_results.append({
                    "source_url": url,
                    "source_result": SOURCE_INSUFFICIENT,
                    "evidence_excerpt": "",
                    "addresses_claim": False,
                    "publication_date": "",
                    "reason": "Consensus not reached",
                })

        # Deterministic overall verdict composition
        overall = _deterministic_overall_verdict(source_results)

        # Store report
        self.report_counter = self.report_counter + 1
        report_id = f"rp-{self.report_counter}"

        report = {
            "id": report_id,
            "attestation_id": attestation_id,
            "claim": mem_claim,
            "overall_result": overall["overall_result"],
            "supporting_count": overall["supporting_count"],
            "contradicting_count": overall["contradicting_count"],
            "insufficient_count": overall["insufficient_count"],
            "reason": overall["reason"],
            "source_results": source_results,
        }
        self.reports[report_id] = json.dumps(report)

        att["status"] = "COMPLETED"
        att["latest_report_id"] = report_id
        self.attestations[attestation_id] = json.dumps(att)

        return report_id

    @gl.public.view
    def get_attestation(self, attestation_id: str) -> dict:
        """Retrieve attestation details."""
        if attestation_id not in self.attestations:
            raise gl.vm.UserError("Attestation not found")
        return json.loads(self.attestations[attestation_id])

    @gl.public.view
    def get_report(self, report_id: str) -> dict:
        """Retrieve a specific report."""
        if report_id not in self.reports:
            raise gl.vm.UserError("Report not found")
        return json.loads(self.reports[report_id])
