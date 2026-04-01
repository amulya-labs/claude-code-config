# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
"""
gemini_review.py -- Phase 2 inline code review via Gemini.

Reads environment variables, optionally creates/reuses a Gemini Context Cache
for the repo codebase (Pro model only), counts tokens to guard against oversized
requests, then posts the diff to the selected model and writes a JSON array of
findings to OUTPUT_FILE.

Environment variables:
  GEMINI_API_KEY   Required. Gemini API key.
  DIFF_FOCUSED     Required. Path to the focused diff file, or raw diff content.
  SELECTED_MODEL   Default: gemini-2.5-flash. The Gemini model to use.
  REPO             Required when USE_CACHE=1. Org/repo slug (e.g. owner/repo).
  USE_CACHE        Default: 0. Set to 1 to enable context caching (Pro only).
  OUTPUT_FILE      Default: /tmp/inline-comments.json. Where to write results.
  METRICS_FILE     Default: /tmp/review-metrics-phase2.json. Token usage output.
  CACHE_MANIFEST_PATH  Default: .github/gemini-cache-manifest.yml. Cache target config.
  MODE             Default: light. Review mode ("light", "deep", or "pro").
                   Controls thinking budget: light disables thinking, deep/pro enable it.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
DIFF_FOCUSED_INPUT = os.environ.get("DIFF_FOCUSED", "")
SELECTED_MODEL = os.environ.get("SELECTED_MODEL", "gemini-2.5-flash")
REPO = os.environ.get("REPO", "")
USE_CACHE = os.environ.get("USE_CACHE", "0").strip() == "1"
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "/tmp/inline-comments.json")
METRICS_FILE = os.environ.get("METRICS_FILE", "/tmp/review-metrics-phase2.json")
CACHE_MANIFEST_PATH = os.environ.get("CACHE_MANIFEST_PATH", ".github/gemini-cache-manifest.yml")

# Maximum tokens before we bail out with an empty result
TOKEN_LIMIT = 1_000_000

# Minimum tokens needed to justify creating a context cache
CACHE_MIN_TOKENS = 4_096

# Cache TTL (only applies when USE_CACHE=1).
# WARNING: Cached content storage is billed per token-hour. With a 12h TTL and
# moderate PR volume, this can easily cost $40-50/month on Gemini 2.5 Pro.
# Caching is currently disabled in the workflow (USE_CACHE=0). Only re-enable
# if the per-request savings outweigh the storage costs for your usage pattern.
CACHE_TTL_SECONDS = 12 * 3600

# Retry configuration for transient API errors (429, 5xx)
RETRY_MAX_ATTEMPTS = 3
RETRY_BASE_DELAY_SECONDS = 2  # doubles each attempt: 2s, 4s, 8s

# HTTP status codes that warrant a retry
RETRYABLE_EXCEPTION_SUBSTRINGS = ("429", "500", "502", "503", "504", "quota", "rate")

# Thinking model token budgets: max_output_tokens covers BOTH thinking and
# text output for Gemini 2.5 models. We set 16384 total with 8192 for
# thinking, leaving 8192+ for the JSON response (~10 findings).
REVIEW_MAX_OUTPUT_TOKENS = 16_384
REVIEW_THINKING_BUDGET = 8_192

# File extensions / name patterns to skip when building the cache corpus.
# Covers lock files, build artifacts, compiled assets, AND common secret/credential files.
SKIP_PATTERNS = re.compile(
    # Lock files
    r"package-lock\.json$|yarn\.lock$|pnpm-lock\.yaml$|Cargo\.lock$"
    r"|Gemfile\.lock$|poetry\.lock$|composer\.lock$"
    # Minified / data files
    r"|\.min\.(js|css)$|\.csv$|\.tsv$|\.pb$|\.bin$"
    # Tooling dirs
    r"|node_modules/|\.git/|__pycache__/|\.pyc$"
    # Secret / credential files
    r"|\.env$|\.env\."
    r"|\.pem$|\.key$|\.p12$|\.pfx$|\.crt$|\.cer$"
    r"|\.tfvars$|\.tfstate$|\.tfstate\.backup$"
    r"|credential|secret|\.vault$"
    r"|id_rsa|id_ed25519|id_ecdsa|id_dsa"
)

# Binary-like extensions to skip entirely
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg",
    ".pdf", ".zip", ".tar", ".gz", ".whl", ".exe", ".so", ".dylib",
}

# Default glob patterns when no cache manifest is found
DEFAULT_CACHE_PATTERNS = ["*.md", "docs/**/*.md"]


# ---------------------------------------------------------------------------
# Inline review prompt (matches the existing workflow prompt)
# ---------------------------------------------------------------------------

INLINE_PROMPT_TEMPLATE = """You are a Senior Staff Software Engineer performing a rigorous code review.
Objective: Identify bugs, security vulnerabilities, and performance bottlenecks in the provided diff.

Review Criteria:
- Logic and Correctness: Are there edge cases missed? Off-by-one errors?
- Security: Look for hardcoded secrets, injection risks, or unsafe dependencies.
- Maintainability: Is the code self-documenting? Are there better patterns?
- Actionable: Every critique MUST include a code suggestion if a fix is possible.

Constraint: Do not comment on stylistic preferences (tabs vs spaces, etc.) unless it violates a clear pattern in the existing code. Focus only on bugs, security, and significant correctness issues.

Git Diff to Review:
{diff}

Output Format: Return ONLY a valid JSON array. No markdown, no explanation, just the JSON.
Each object must follow this schema:
{{
  "file": "string (relative path to the file)",
  "line": number (the absolute line number in the new version of the file where the issue appears; use the line numbers shown after '+' in the diff hunk headers),
  "severity": "string (Critical | High | Medium | Low)",
  "comment": "string (your technical explanation of the issue)",
  "suggestion": "string (the corrected code, or empty string if no suggestion)"
}}

If you find no significant issues, return an empty JSON array: []
Cap your response at 10 items. Prioritize Critical and High severity findings.
Return ONLY the JSON array, nothing else."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def die(msg: str) -> None:
    log(f"ERROR: {msg}")
    sys.exit(1)


def load_diff() -> str:
    """Load the diff from file path or inline content."""
    if not DIFF_FOCUSED_INPUT:
        die("DIFF_FOCUSED env var is not set")

    # If it looks like a file path and the file exists, read it
    candidate = Path(DIFF_FOCUSED_INPUT)
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8", errors="replace")

    # Otherwise treat it as inline content
    return DIFF_FOCUSED_INPUT


def truncate_diff(diff: str, max_chars: int = 500_000) -> str:
    """
    Truncate diff at max_chars, adding a notice if truncated.

    Note: the 500k char limit is intentionally conservative relative to the
    1M-token TOKEN_LIMIT. At roughly 3-4 chars per token, 500k chars maps to
    approximately 125k-167k tokens for the diff alone, leaving ample headroom
    for the prompt template and any cached corpus content.
    """
    if len(diff) <= max_chars:
        return diff
    truncated = diff[:max_chars]
    truncated += f"\n[DIFF TRUNCATED: full diff is {len(diff)} chars; only first {max_chars} shown]\n"
    return truncated


def parse_cache_manifest(manifest_path: str) -> list:
    """
    Parse a simple YAML cache manifest, returning include glob patterns.
    Returns an empty list if the manifest doesn't exist or can't be parsed.

    Handles the format:
        include:
          - "pattern1"
          - "pattern2"
    """
    try:
        content = Path(manifest_path).read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []

    patterns = []
    in_include = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("include:"):
            in_include = True
            continue
        if in_include:
            if stripped.startswith("- "):
                pattern = stripped[2:].strip().strip('"').strip("'")
                if pattern:
                    patterns.append(pattern)
            elif stripped and not stripped.startswith("#"):
                break  # End of include list
    return patterns


def _validate_glob_pattern(pattern: str) -> bool:
    """
    Validate that a glob pattern is safe (no path traversal or absolute paths).
    Returns True if the pattern is safe to use, False otherwise.
    """
    if os.path.isabs(pattern):
        log(f"WARNING: Rejecting absolute glob pattern: {pattern}")
        return False
    if ".." in pattern:
        log(f"WARNING: Rejecting glob pattern with path traversal: {pattern}")
        return False
    return True


def build_cache_corpus() -> str:
    """
    Collect text source files into a corpus for context caching.
    Uses a manifest file if available, otherwise falls back to defaults.
    Skips binaries, lock files, and secret/credential files.
    Validates patterns and resolved paths to prevent directory traversal.
    """
    cwd = Path(".").resolve()

    # Try to load manifest patterns
    manifest_patterns = parse_cache_manifest(CACHE_MANIFEST_PATH)

    if manifest_patterns:
        log(f"Using cache manifest from {CACHE_MANIFEST_PATH} ({len(manifest_patterns)} patterns)")
        patterns = [p for p in manifest_patterns if _validate_glob_pattern(p)]
    else:
        log(f"No cache manifest at {CACHE_MANIFEST_PATH}; using defaults: {DEFAULT_CACHE_PATTERNS}")
        patterns = DEFAULT_CACHE_PATTERNS

    # Collect files matching patterns (deduplicate across patterns)
    seen: set = set()
    parts = []
    for pattern in patterns:
        for path in sorted(Path(".").glob(pattern)):
            if not path.is_file():
                continue
            # Ensure resolved path is under the repo root (prevents symlink escapes)
            resolved = path.resolve()
            if not str(resolved).startswith(str(cwd)):
                log(f"WARNING: Skipping file outside repo root: {path} -> {resolved}")
                continue
            rel = str(path)
            if rel in seen:
                continue
            seen.add(rel)
            if SKIP_PATTERNS.search(rel):
                continue
            if path.suffix.lower() in BINARY_EXTENSIONS:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            parts.append(f"=== {rel} ===\n{content}\n")

    log(f"Cache corpus: {len(parts)} files included")
    return "\n".join(parts)


def repo_slug(repo: str) -> str:
    """Convert 'owner/repo' to a cache display name safe slug."""
    return re.sub(r"[^a-z0-9-]", "-", repo.lower())


def write_output(data: list) -> None:
    """Write the JSON array to OUTPUT_FILE."""
    output_path = Path(OUTPUT_FILE)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    log(f"Wrote {len(data)} finding(s) to {OUTPUT_FILE}")


def write_metrics(usage: dict) -> None:
    """Write token usage metrics to METRICS_FILE."""
    metrics_path = Path(METRICS_FILE)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(usage, indent=2), encoding="utf-8")
    log(f"Wrote metrics to {METRICS_FILE}")


def parse_json_response(raw_text: str) -> list:
    """
    Strip markdown code fences and parse as a JSON array.
    Raises ValueError on empty or unparseable responses.
    Returns an empty list only when the model legitimately returned [].
    """
    if not raw_text or not raw_text.strip():
        raise ValueError("Gemini returned an empty response")

    clean = raw_text.strip()
    # Strip an opening ```json or ``` fence (only one of these can match)
    clean = re.sub(r"^```(?:json)?\s*", "", clean)
    # Strip a closing ``` fence
    clean = re.sub(r"\s*```$", "", clean)
    clean = clean.strip()

    try:
        parsed = json.loads(clean)
        if isinstance(parsed, list):
            return parsed
        raise ValueError(f"Response JSON is not an array: {type(parsed).__name__}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Failed to parse JSON response: {exc}") from exc


def extract_response_text(response) -> str:
    """
    Extract the non-thought text from a Gemini response.

    Gemini 2.5 models have thinking enabled by default, which means the
    response may contain both thought parts and text parts.  response.text
    can return None or raise when only thought parts are present.
    This helper tries the fast path first, then iterates parts explicitly.
    """
    # Fast path: response.text works in most cases
    try:
        if response.text:
            return response.text
    except Exception as exc:
        log(f"WARNING: response.text raised {type(exc).__name__}: {exc}")

    # Fallback: iterate parts and skip thought parts
    try:
        candidate = response.candidates[0]
        finish_reason = getattr(candidate, "finish_reason", None)
        content = candidate.content
        if content is None:
            log(f"WARNING: response content is None (finish_reason={finish_reason})")
            return ""
        parts = content.parts
        if parts is None:
            log(f"WARNING: response content.parts is None (finish_reason={finish_reason})")
            return ""
        text_parts = [p.text for p in parts if not getattr(p, "thought", False) and p.text]
        if text_parts:
            return "\n".join(text_parts)
        # Log what we got for debugging
        part_summary = [
            f"thought={getattr(p, 'thought', '?')}, text={bool(p.text)}"
            for p in parts
        ]
        log(f"WARNING: No non-thought text parts found. Parts: {part_summary}")
    except (IndexError, AttributeError, TypeError) as exc:
        log(f"WARNING: Could not iterate response parts: {exc}")

    return ""


def _extract_usage_metadata(response) -> dict:
    """Extract token usage metadata from a Gemini response."""
    try:
        usage = response.usage_metadata
        if usage is None:
            return {}
        return {
            "prompt_token_count": getattr(usage, "prompt_token_count", 0) or 0,
            "candidates_token_count": getattr(usage, "candidates_token_count", 0) or 0,
            "cached_content_token_count": getattr(usage, "cached_content_token_count", 0) or 0,
            "total_token_count": getattr(usage, "total_token_count", 0) or 0,
        }
    except Exception as exc:
        log(f"WARNING: Could not extract usage metadata: {exc}")
        return {}


def _is_retryable_error(exc: Exception) -> bool:
    """Return True if the exception looks like a transient quota or server error."""
    msg = str(exc).lower()
    return any(substr in msg for substr in RETRYABLE_EXCEPTION_SUBSTRINGS)


def _call_with_retry(fn, description: str):
    """
    Call fn() with exponential-backoff retry on transient errors (429, 5xx).
    Raises the last exception if all attempts are exhausted.
    """
    delay = RETRY_BASE_DELAY_SECONDS
    last_exc = None
    for attempt in range(1, RETRY_MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            if attempt < RETRY_MAX_ATTEMPTS and _is_retryable_error(exc):
                log(
                    f"WARNING: {description} attempt {attempt}/{RETRY_MAX_ATTEMPTS} "
                    f"failed with transient error: {exc}; retrying in {delay}s..."
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise last_exc  # unreachable but satisfies type checkers


# ---------------------------------------------------------------------------
# Context cache management (Pro model only)
# ---------------------------------------------------------------------------

def find_existing_cache(client, display_name: str):
    """
    List all caches and return the first one with matching display_name
    whose expiry is in the future. Returns None if not found.
    """
    try:
        for cache in client.caches.list():
            if getattr(cache, "display_name", None) != display_name:
                continue
            expire_time = getattr(cache, "expire_time", None)
            if expire_time is None:
                return cache
            # expire_time may be a datetime object (aware or naive) or an ISO string
            if isinstance(expire_time, datetime):
                exp_dt = expire_time
            else:
                exp_dt = datetime.fromisoformat(str(expire_time).replace("Z", "+00:00"))
            # Ensure exp_dt is timezone-aware before comparing with UTC now
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if exp_dt > datetime.now(timezone.utc):
                log(f"Found valid existing cache: {cache.name} (expires {exp_dt})")
                return cache
        return None
    except Exception as exc:
        log(f"WARNING: Failed to list caches: {exc}")
        return None


def create_cache(client, model: str, corpus: str, display_name: str):
    """
    Create a new context cache with the repo corpus.
    Returns the cache object, or None if creation fails or corpus too small.
    """
    from google.genai import types

    # Count tokens in the corpus first
    try:
        count_resp = client.models.count_tokens(model=model, contents=corpus)
        corpus_tokens = count_resp.total_tokens
        log(f"Corpus token count: {corpus_tokens}")
    except Exception as exc:
        log(f"WARNING: Token counting for corpus failed: {exc}")
        corpus_tokens = 0

    if corpus_tokens < CACHE_MIN_TOKENS:
        log(
            f"Corpus too small for caching ({corpus_tokens} tokens < {CACHE_MIN_TOKENS} minimum); "
            "skipping cache creation."
        )
        return None

    ttl_str = f"{CACHE_TTL_SECONDS}s"
    log(f"Creating context cache '{display_name}' with TTL={ttl_str}...")
    try:
        cache = client.caches.create(
            model=model,
            config=types.CreateCachedContentConfig(
                contents=[
                    types.Content(
                        role="user",
                        parts=[types.Part.from_text(text=corpus)],
                    )
                ],
                display_name=display_name,
                ttl=ttl_str,
            ),
        )
        log(f"Created cache: {cache.name}")
        return cache
    except Exception as exc:
        log(f"WARNING: Cache creation failed: {exc}; falling back to direct API call")
        return None


# ---------------------------------------------------------------------------
# Main review logic
# ---------------------------------------------------------------------------

def _thinking_config_for_model(model: str):
    """
    Return an appropriate ThinkingConfig based on review mode.
    Light mode disables thinking for speed/cost. Deep and pro modes enable
    thinking so the model can reason through complex diffs before responding.
    """
    from google.genai import types

    mode = os.environ.get("MODE", "light")
    if mode in ("deep", "pro"):
        return types.ThinkingConfig(thinking_budget=REVIEW_THINKING_BUDGET)
    # Light: disable thinking to keep reviews fast and cheap
    return types.ThinkingConfig(thinking_budget=0)


def run_review_direct(client, model: str, prompt: str) -> tuple:
    """
    Send the prompt directly to the model (no caching).
    Retries on transient 429/5xx errors with exponential backoff.
    Returns (findings_list, usage_metadata_dict).
    """
    from google.genai import types

    log(f"Running inline review via {model} (direct, no cache)...")

    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                thinking_config=_thinking_config_for_model(model),
            ),
        )

    response = _call_with_retry(_call, f"generate_content ({model})")
    usage = _extract_usage_metadata(response)
    raw = extract_response_text(response)
    if not raw.strip():
        log("WARNING: Model returned no text output (thinking-only response); treating as zero findings")
        return [], usage
    return parse_json_response(raw), usage


def run_review_with_cache(client, model: str, cache_name: str, diff: str) -> tuple:
    """
    Send the prompt with a context cache reference.
    Retries on transient errors; falls back to direct call on cache errors.
    Returns (findings_list, usage_metadata_dict).
    """
    from google.genai import types

    prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)
    log(f"Running inline review via {model} (with cache {cache_name})...")

    def _call():
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=REVIEW_MAX_OUTPUT_TOKENS,
                cached_content=cache_name,
                thinking_config=_thinking_config_for_model(model),
            ),
        )

    try:
        response = _call_with_retry(_call, f"generate_content with cache ({model})")
        usage = _extract_usage_metadata(response)
        raw = extract_response_text(response)
        if not raw.strip():
            log("WARNING: Model returned no text output (thinking-only response); treating as zero findings")
            return [], usage
        return parse_json_response(raw), usage
    except ValueError:
        raise  # parse failures should not be silenced
    except Exception as exc:
        log(f"WARNING: Gemini API call with cache failed: {exc}; retrying without cache")
        prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)
        return run_review_direct(client, model, prompt)


def main() -> None:
    if not GEMINI_API_KEY:
        die("GEMINI_API_KEY is not set")

    # Load and truncate the diff
    diff_raw = load_diff()
    diff = truncate_diff(diff_raw)

    if not diff.strip():
        log("WARNING: Diff is empty; writing empty findings")
        write_output([])
        write_metrics({})
        return

    # Build the full prompt for token counting and direct calls
    prompt = INLINE_PROMPT_TEMPLATE.format(diff=diff)

    # Import the SDK
    try:
        from google import genai
    except ImportError:
        die("google-genai is not installed. Run: pip install google-genai")

    client = genai.Client(api_key=GEMINI_API_KEY)

    # Count tokens before sending
    log(f"Counting tokens for model '{SELECTED_MODEL}'...")
    try:
        count_resp = client.models.count_tokens(model=SELECTED_MODEL, contents=prompt)
        total_tokens = count_resp.total_tokens
        log(f"Estimated token count: {total_tokens}")
    except Exception as exc:
        log(f"WARNING: Token counting failed: {exc}; proceeding without count")
        total_tokens = 0

    if total_tokens > TOKEN_LIMIT:
        log(
            f"WARNING: Token count ({total_tokens}) exceeds limit ({TOKEN_LIMIT}); "
            "skipping inline review to avoid quota exhaustion."
        )
        write_output([])
        write_metrics({})
        return

    # Determine whether to use caching.
    # Only "pro" tier models support context caching; do NOT match on version
    # strings like "1.5" which would incorrectly classify gemini-1.5-flash as Pro.
    is_pro_model = "pro" in SELECTED_MODEL.lower()

    usage = {}
    try:
        if USE_CACHE and is_pro_model and REPO:
            display_name = f"cache-{repo_slug(REPO)}"

            # Try to reuse an existing valid cache
            existing = find_existing_cache(client, display_name)

            if existing:
                findings, usage = run_review_with_cache(client, SELECTED_MODEL, existing.name, diff)
            else:
                # Build corpus and try to create a new cache
                log("Building repo corpus for context cache...")
                corpus = build_cache_corpus()
                cache = create_cache(client, SELECTED_MODEL, corpus, display_name)

                if cache:
                    findings, usage = run_review_with_cache(client, SELECTED_MODEL, cache.name, diff)
                else:
                    # Cache unavailable — fall back to direct call
                    findings, usage = run_review_direct(client, SELECTED_MODEL, prompt)
        else:
            # Flash model or caching disabled: direct call
            findings, usage = run_review_direct(client, SELECTED_MODEL, prompt)
    except Exception as exc:
        die(f"Gemini review failed: {exc}")

    write_output(findings)
    write_metrics(usage)


if __name__ == "__main__":
    main()
