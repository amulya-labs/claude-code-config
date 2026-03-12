#!/bin/bash
# Shared logic for Gemini code review workflow (internal + OSS jobs).
# Both gemini-review-internal and gemini-review-oss call this script
# after setting required environment variables.
#
# Source: https://github.com/amulya-labs/ai-dev-foundry
# License: MIT (https://opensource.org/licenses/MIT)
#
# Required env vars:
#   GH_TOKEN           GitHub token for API calls
#   GEMINI_API_KEY     Gemini API key
#   PR_NUMBER          Pull request number
#   REPO               Repository (owner/repo)
#   RUN_URL            URL to the workflow run
#   SELECTED_MODEL     Gemini model name (e.g. gemini-2.5-flash)
#   MODE               "light" or "deep"
#   USE_CACHE          "0" or "1"
#   WORKFLOW_VERSION   Workflow version (usually commit SHA)
#   INLINE_ATTRIBUTION Attribution footer for inline comments
#   COMMENT_BODY       Original trigger comment body
#
# Optional env vars:
#   SCRIPT_SOURCE          "local" (default) or "remote"
#   REVIEW_SCRIPT_PATH     Path to gemini_review.py (auto-resolved)
#   CACHE_MANIFEST_PATH    Path to cache manifest YAML

set -euo pipefail

# shellcheck disable=SC2154  # all uppercase vars are env vars set by the calling workflow

# Validate required env vars
: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GEMINI_API_KEY:?GEMINI_API_KEY is required}"
: "${PR_NUMBER:?PR_NUMBER is required}"
: "${REPO:?REPO is required}"
: "${RUN_URL:?RUN_URL is required}"
: "${SELECTED_MODEL:?SELECTED_MODEL is required}"
: "${MODE:?MODE is required}"
: "${USE_CACHE:?USE_CACHE is required}"
: "${WORKFLOW_VERSION:?WORKFLOW_VERSION is required}"
: "${INLINE_ATTRIBUTION:?INLINE_ATTRIBUTION is required}"
: "${COMMENT_BODY:?COMMENT_BODY is required}"

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

# Call Gemini API with exponential-backoff retry on 429/5xx.
# $1 = URL, $2 = path to JSON payload file (avoids ARG_MAX with large diffs)
gemini_api_call() {
  local url="$1" payload_file="$2"
  local attempt delay http_code curl_exit
  delay=2
  for attempt in 1 2 3; do
    http_code=""
    curl_exit=0
    # Temporarily disable errexit so curl network failures don't abort the script
    set +e
    http_code=$(curl -s \
      -o /tmp/gemini-resp.json \
      -w '%{http_code}' \
      "$url" -H 'Content-Type: application/json' -d "@$payload_file" 2>/dev/null)
    curl_exit=$?
    set -e

    if [ "$curl_exit" -ne 0 ]; then
      echo "WARNING: curl failed with exit code $curl_exit (attempt $attempt/3); retrying in ${delay}s..." >&2
      if [ "$attempt" -lt 3 ]; then
        sleep "$delay"
        delay=$((delay * 2))
        continue
      else
        echo "ERROR: Gemini API failed after 3 attempts (curl exit $curl_exit)" >&2
        [ -f /tmp/gemini-resp.json ] && cat /tmp/gemini-resp.json >&2
        return 1
      fi
    fi

    case "$http_code" in
      200)
        [ -f /tmp/gemini-resp.json ] && cat /tmp/gemini-resp.json
        return 0
        ;;
      429|500|502|503|504)
        echo "WARNING: Gemini API HTTP $http_code (attempt $attempt/3); retrying in ${delay}s..." >&2
        sleep "$delay"
        delay=$((delay * 2))
        ;;
      *)
        echo "ERROR: Gemini API HTTP $http_code (non-retryable)" >&2
        [ -f /tmp/gemini-resp.json ] && cat /tmp/gemini-resp.json >&2
        return 1
        ;;
    esac
  done
  echo "ERROR: Gemini API failed after 3 attempts (last HTTP $http_code)" >&2
  [ -f /tmp/gemini-resp.json ] && cat /tmp/gemini-resp.json >&2
  return 1
}

# Phase-status tracking: record what happened in each phase so the summary
# comment can distinguish "no findings" from "did not run".
echo '{"phase1_summary":"pending","phase2_inline":"pending"}' > /tmp/review-status.json
update_status() { # $1=key, $2=value
  local tmp
  tmp=$(jq --arg k "$1" --arg v "$2" '.[$k] = $v' /tmp/review-status.json)
  echo "$tmp" > /tmp/review-status.json
}

FLASH_MODEL="gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Post review-started notice (non-fatal)
# ---------------------------------------------------------------------------

if [[ "${COMMENT_BODY}" == /gemini-deep-review* ]]; then
  REVIEW_LABEL="Deep"
  TRIGGER_CMD="/gemini-deep-review"
elif [[ "${COMMENT_BODY}" == /gemini-light-review* ]]; then
  REVIEW_LABEL="Light"
  TRIGGER_CMD="/gemini-light-review"
else
  REVIEW_LABEL="Light"
  TRIGGER_CMD="/gemini-review"
fi

# shellcheck disable=SC2016  # %s placeholders are for printf, not shell expansion
printf '💎 **Gemini %s Review** in progress… [View run](%s)\n> Triggered by `%s`.' \
  "$REVIEW_LABEL" "$RUN_URL" "$TRIGGER_CMD" > /tmp/review-started.md

gh pr comment "$PR_NUMBER" \
  --repo "$REPO" \
  --body-file /tmp/review-started.md \
  2>/dev/null || echo "WARNING: Failed to post review-started notice" >&2

# ---------------------------------------------------------------------------
# Fetch PR metadata
# ---------------------------------------------------------------------------

gh pr view "$PR_NUMBER" \
  --repo "$REPO" \
  --json title,body,additions,deletions,changedFiles,headRefOid \
  > /tmp/pr-meta.json

PR_TITLE=$(jq -r '.title' /tmp/pr-meta.json)
PR_BODY=$(jq -r '.body // ""' /tmp/pr-meta.json)
HEAD_SHA=$(jq -r '.headRefOid' /tmp/pr-meta.json)
FILES=$(jq '.changedFiles' /tmp/pr-meta.json)
LINES=$(jq '.additions + .deletions' /tmp/pr-meta.json)

# ---------------------------------------------------------------------------
# Determine review strategy (based on PR size only, not mode)
# ---------------------------------------------------------------------------

if [ "$FILES" -gt 50 ] || [ "$LINES" -gt 3000 ]; then
  REVIEW_STRATEGY="summary"
else
  REVIEW_STRATEGY="detailed"
fi

# ---------------------------------------------------------------------------
# Fetch and filter diff
# ---------------------------------------------------------------------------

gh pr diff "$PR_NUMBER" --repo "$REPO" > /tmp/pr-full.diff
DIFF_CHARS=$(wc -c < /tmp/pr-full.diff)
if [ "$DIFF_CHARS" -gt 500000 ]; then
  head -c 500000 /tmp/pr-full.diff > /tmp/pr.diff
  printf '\n[DIFF TRUNCATED: full diff is %s chars; only first 500000 shown]\n' "$DIFF_CHARS" >> /tmp/pr.diff
else
  cp /tmp/pr-full.diff /tmp/pr.diff
fi

# Filter non-code files (lock files, compiled assets, data files)
python3 -c '
import sys, re
SKIP = re.compile(r"package-lock\.json|yarn\.lock|pnpm-lock\.yaml|Cargo\.lock|Gemfile\.lock|poetry\.lock|composer\.lock|\.min\.(js|css)|\.csv$|\.tsv$")
skip = False
for line in sys.stdin:
    if line.startswith("diff --git"):
        skip = bool(SKIP.search(line))
    if not skip:
        sys.stdout.write(line)
' < /tmp/pr.diff > /tmp/pr-focused.diff

# ---------------------------------------------------------------------------
# Phase 1: Summary via Gemini Flash
# ---------------------------------------------------------------------------

PHASE1_START=$(date +%s)

if [ "${MODE}" = "light" ]; then
  printf 'You are performing a QUICK code review of this pull request.\n\nPR Title: %s\n%s files changed, %s lines (additions + deletions)\n\nGit Diff:\n' \
    "$PR_TITLE" "$FILES" "$LINES" > /tmp/summary-prompt.txt
  cat /tmp/pr.diff >> /tmp/summary-prompt.txt
  printf '\nQuick scan — focus only on:\n1. Obvious bugs or logic errors\n2. Security issues (injection, auth, exposed secrets)\n3. Breaking changes or missing error handling\n\nFormat: two short paragraphs (no headers, no bullets, no bold labels).\nFirst: what the PR does in one sentence, then any critical issues found.\nSecond: overall verdict — looks good / needs changes / flag for deep review.\nKeep it under 100 words. If purely mechanical (renames, deps, formatting), just confirm it looks fine.' \
    >> /tmp/summary-prompt.txt
else
  printf 'You are an experienced engineering lead reviewing a pull request for your team.\nWrite a concise narrative summary of these changes in exactly three paragraphs — no headers, no bullet points, no bold labels.\n\nPR Title: %s\nPR Description: %s\n\nGit Diff:\n' \
    "$PR_TITLE" "$PR_BODY" > /tmp/summary-prompt.txt
  cat /tmp/pr.diff >> /tmp/summary-prompt.txt
  printf '\nFirst paragraph: Describe the overall intent and motivation for the change in plain language.\nSecond paragraph: Explain the key technical decisions — what logic was added, restructured, or removed, and why it matters.\nThird paragraph: Give your honest read on the nature of this change (e.g. a targeted bug fix, a careful refactor, a broad feature addition) and flag any areas that deserve closer attention during review.\n\nWrite as a thoughtful colleague, not as a template-filling bot. Keep it under 200 words total.\nDo not include any markdown headers or code fences in your response.' \
    >> /tmp/summary-prompt.txt
fi

jq -n --rawfile prompt /tmp/summary-prompt.txt '{
  contents: [{parts: [{text: $prompt}]}],
  generationConfig: {temperature: 0.3, maxOutputTokens: 512}
}' > /tmp/gemini-flash-payload.json

FLASH_RESPONSE=$(gemini_api_call \
  "https://generativelanguage.googleapis.com/v1beta/models/${FLASH_MODEL}:generateContent?key=${GEMINI_API_KEY}" \
  "/tmp/gemini-flash-payload.json") || true

SUMMARY_TEXT=$(echo "$FLASH_RESPONSE" | jq -r '.candidates[0].content.parts[0].text // empty' 2>/dev/null)
if [ -z "$SUMMARY_TEXT" ]; then
  FLASH_ERROR=$(echo "$FLASH_RESPONSE" | jq -r '.error | "[\(.code)] \(.message)" // "unknown error"' 2>/dev/null)
  echo "ERROR: Gemini Flash API call failed: $FLASH_ERROR" >&2
  update_status phase1_summary "failed:$FLASH_ERROR"
  exit 1
fi
update_status phase1_summary "success"

PHASE1_END=$(date +%s)
PHASE1_LATENCY=$((PHASE1_END - PHASE1_START))

# ---------------------------------------------------------------------------
# Phase 2: Inline review via Gemini (focused diff — code files only)
# Phase 2 errors are non-fatal: a failure here should not prevent posting
# the Phase 1 summary. The status is recorded and surfaced in the comment.
# ---------------------------------------------------------------------------

PHASE2_START=$(date +%s)

# Resolve the review script path
REVIEW_SCRIPT_PATH="${REVIEW_SCRIPT_PATH:-.github/workflows/scripts/gemini_review.py}"

if [ "${SCRIPT_SOURCE:-local}" = "remote" ]; then
  REVIEW_SCRIPT_PATH="/tmp/gemini_review.py"
  if ! curl -fsSL \
    -H "Authorization: Bearer ${GH_TOKEN}" \
    -H "Accept: application/vnd.github.raw+json" \
    "https://api.github.com/repos/${REPO}/contents/.github/workflows/scripts/gemini_review.py" \
    -o "$REVIEW_SCRIPT_PATH"; then
    echo "WARNING: curl failed to fetch gemini_review.py; skipping inline review." >&2
    echo "[]" > /tmp/inline-comments.json
    update_status phase2_inline "skipped:curl_fetch_failed"
    REVIEW_SCRIPT_PATH=""
  elif [ ! -s "$REVIEW_SCRIPT_PATH" ]; then
    echo "WARNING: gemini_review.py was fetched but is empty; skipping inline review." >&2
    echo "[]" > /tmp/inline-comments.json
    update_status phase2_inline "skipped:script_empty"
    REVIEW_SCRIPT_PATH=""
  fi
fi

if [ "${REVIEW_STRATEGY}" = "summary" ]; then
  echo "PR too large for inline review (${FILES} files, ${LINES} lines); skipping." >&2
  echo "[]" > /tmp/inline-comments.json
  update_status phase2_inline "skipped:summary_only"
elif [ -z "${REVIEW_SCRIPT_PATH}" ]; then
  : # Script not available (remote fetch failed); already handled above
else
  if DIFF_FOCUSED="/tmp/pr-focused.diff" \
    SELECTED_MODEL="${SELECTED_MODEL}" \
    USE_CACHE="${USE_CACHE}" \
    OUTPUT_FILE="/tmp/inline-comments.json" \
    METRICS_FILE="/tmp/review-metrics-phase2.json" \
    CACHE_MANIFEST_PATH="${CACHE_MANIFEST_PATH:-.github/gemini-cache-manifest.yml}" \
    python3 "$REVIEW_SCRIPT_PATH"; then
    # Note: status is set to "success" after inline comments are posted (below)
    :
  else
    echo "ERROR: Phase 2 (inline review) failed with exit code $?" >&2
    update_status phase2_inline "failed:script_error"
    echo "[]" > /tmp/inline-comments.json
  fi
fi

PHASE2_END=$(date +%s)
PHASE2_LATENCY=$((PHASE2_END - PHASE2_START))

COUNT=$(jq 'length' /tmp/inline-comments.json)

# ---------------------------------------------------------------------------
# Build summary comment
# ---------------------------------------------------------------------------

if [ "${MODE}" = "deep" ]; then
  RETRIGGER="/gemini-deep-review"
else
  RETRIGGER="/gemini-review"
fi

{
  echo "## Gemini Code Review"
  echo ""
  echo "_Automated review by [Gemini](https://gemini.google.com/) via [ai-dev-foundry](https://github.com/amulya-labs/ai-dev-foundry) (${WORKFLOW_VERSION:0:7}). Re-trigger with \`$RETRIGGER\`._"
  echo ""
  echo "$SUMMARY_TEXT"
  echo ""

  PHASE2=$(jq -r '.phase2_inline' /tmp/review-status.json)
  if [ "$COUNT" -gt 0 ]; then
    # Compute severity breakdown for the findings header
    CRITICAL_COUNT=$(jq '[.[] | select(.severity == "Critical")] | length' /tmp/inline-comments.json)
    HIGH_COUNT=$(jq '[.[] | select(.severity == "High")] | length' /tmp/inline-comments.json)
    MEDIUM_COUNT=$(jq '[.[] | select(.severity == "Medium")] | length' /tmp/inline-comments.json)
    LOW_COUNT=$(jq '[.[] | select(.severity == "Low")] | length' /tmp/inline-comments.json)

    echo "---"
    echo ""
    echo "### Findings ($COUNT)"
    echo ""

    BREAKDOWN=""
    [ "$CRITICAL_COUNT" -gt 0 ] && BREAKDOWN="${BREAKDOWN}${CRITICAL_COUNT} Critical, "
    [ "$HIGH_COUNT" -gt 0 ] && BREAKDOWN="${BREAKDOWN}${HIGH_COUNT} High, "
    [ "$MEDIUM_COUNT" -gt 0 ] && BREAKDOWN="${BREAKDOWN}${MEDIUM_COUNT} Medium, "
    [ "$LOW_COUNT" -gt 0 ] && BREAKDOWN="${BREAKDOWN}${LOW_COUNT} Low, "
    if [ -n "$BREAKDOWN" ]; then
      echo "_${BREAKDOWN%, }_"
      echo ""
    fi

    echo "| Severity | File | Issue |"
    echo "|----------|------|-------|"
    jq -r '.[] | "| \(.severity) | `\(.file)` | \(.comment | gsub("\n"; " ") | .[0:120]) |"' \
      /tmp/inline-comments.json
    echo ""
    echo "_Inline comments are posted directly on the diff._"
  elif [ "${MODE}" = "light" ]; then
    echo ""
    echo "_No significant issues found. Run \`/gemini-deep-review\` for a deeper analysis._"
  elif [[ "$PHASE2" == skipped:* ]]; then
    echo "---"
    echo ""
    SKIP_REASON="${PHASE2#skipped:}"
    if [ "$SKIP_REASON" = "summary_only" ]; then
      echo "_Inline review skipped: PR is large (${FILES} files, ${LINES} lines). Summary only._"
    elif [ "$SKIP_REASON" = "curl_fetch_failed" ]; then
      echo "> **Warning:** Inline review could not run — the review script failed to download. Summary review above is still valid."
    elif [ "$SKIP_REASON" = "script_empty" ]; then
      echo "> **Warning:** Inline review could not run — the review script was empty. Summary review above is still valid."
    else
      echo "> **Warning:** Inline review was skipped ($SKIP_REASON). Summary review above is still valid."
    fi
  elif [[ "$PHASE2" == failed:* ]]; then
    echo "---"
    echo ""
    echo "> **Warning:** Inline review failed (${PHASE2#failed:}). Summary review above is still valid."
  else
    echo "---"
    echo ""
    echo "No significant issues found by inline review."
  fi

  # Feedback prompt
  echo ""
  echo "---"
  echo "_Was this review helpful? React with :+1: or :-1:_"
} > /tmp/review-body.md

# ---------------------------------------------------------------------------
# Upsert summary comment (edit existing or create new)
# ---------------------------------------------------------------------------

EXISTING_ID=$(gh api "repos/$REPO/issues/$PR_NUMBER/comments" \
  --jq '[.[] | select(.user.login == "github-actions[bot]") | select(.body | startswith("## Gemini Code Review"))] | last | .id // empty' \
  2>/dev/null || true)

if [ -n "$EXISTING_ID" ]; then
  SUMMARY_COMMENT_ID="$EXISTING_ID"
  jq -n --rawfile body /tmp/review-body.md '{"body": $body}' \
  | gh api \
    --method PATCH \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/issues/comments/$SUMMARY_COMMENT_ID" \
    --input -
else
  SUMMARY_COMMENT_ID=$(gh api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/issues/$PR_NUMBER/comments" \
    -f body="$(cat /tmp/review-body.md)" \
    --jq '.id' 2>/dev/null) || true
fi

# ---------------------------------------------------------------------------
# Post inline review comments (if any)
# ---------------------------------------------------------------------------

INLINE_POSTED=0
INLINE_FAILED=0
echo '[]' > /tmp/inline-failures.json

if [ "$COUNT" -gt 0 ]; then
  echo "Posting $COUNT inline comment(s) via GitHub Reviews API..."

  COMMENTS=$(jq --arg footer "$INLINE_ATTRIBUTION" '[.[] | {
    path: .file,
    line: .line,
    side: "RIGHT",
    body: (
      "**[\(.severity)]** \(.comment)" +
      (if (.suggestion != null and .suggestion != "") then
        "\n\n```suggestion\n\(.suggestion)\n```"
      else "" end) +
      $footer
    )
  }]' /tmp/inline-comments.json)

  # Tier 1: try batch POST (happy path — single API call)
  BATCH_OK=true
  if ! jq -n \
    --arg commit_id "$HEAD_SHA" \
    --arg body "" \
    --arg event "COMMENT" \
    --argjson comments "$COMMENTS" \
    '{commit_id: $commit_id, body: $body, event: $event, comments: $comments}' \
  | gh api \
    --method POST \
    -H "Accept: application/vnd.github+json" \
    "/repos/$REPO/pulls/$PR_NUMBER/reviews" \
    --input - 2>/tmp/inline-batch-err.txt; then
    BATCH_OK=false
    echo "WARNING: Batch inline POST failed; falling back to individual comments..." >&2
    cat /tmp/inline-batch-err.txt >&2
  fi

  if [ "$BATCH_OK" = true ]; then
    INLINE_POSTED=$COUNT
    update_status phase2_inline "success"
  else
    # Tier 2: post comments individually via single-comment endpoint
    TOTAL=$(echo "$COMMENTS" | jq 'length')
    for i in $(seq 0 $((TOTAL - 1))); do
      COMMENT_JSON=$(echo "$COMMENTS" | jq --arg sha "$HEAD_SHA" --argjson idx "$i" '{
        commit_id: $sha,
        path: .[$idx].path,
        line: .[$idx].line,
        side: .[$idx].side,
        body: .[$idx].body
      }')

      set +e
      ERR=$(echo "$COMMENT_JSON" | gh api \
        --method POST \
        -H "Accept: application/vnd.github+json" \
        "/repos/$REPO/pulls/$PR_NUMBER/comments" \
        --input - 2>&1 >/dev/null)
      RC=$?
      set -e

      if [ "$RC" -eq 0 ]; then
        INLINE_POSTED=$((INLINE_POSTED + 1))
      else
        INLINE_FAILED=$((INLINE_FAILED + 1))
        FILE=$(echo "$COMMENTS" | jq -r ".[$i].path")
        LINE=$(echo "$COMMENTS" | jq -r ".[$i].line")
        echo "WARNING: Failed to post comment on $FILE:$LINE — $ERR" >&2
        # Collect failed comment details for the summary appendix
        jq -r ".[$i]" /tmp/inline-comments.json \
          | jq '{file: .file, line: .line, severity: .severity, comment: .comment}' \
          >> /tmp/inline-failure-item.json
      fi

      # 1-second sleep between POSTs to avoid secondary rate limits
      if [ "$i" -lt $((TOTAL - 1)) ]; then
        sleep 1
      fi
    done

    # Assemble failures array
    if [ "$INLINE_FAILED" -gt 0 ] && [ -f /tmp/inline-failure-item.json ]; then
      jq -s '.' /tmp/inline-failure-item.json > /tmp/inline-failures.json
    fi

    if [ "$INLINE_FAILED" -eq 0 ]; then
      update_status phase2_inline "success"
    elif [ "$INLINE_POSTED" -gt 0 ]; then
      update_status phase2_inline "partial:${INLINE_POSTED}_of_${COUNT}_posted"
    else
      update_status phase2_inline "failed:inline_post"
    fi
  fi

  # Tier 3: PATCH summary comment with failure details if any comments failed
  if [ "$INLINE_FAILED" -gt 0 ] && [ -n "${SUMMARY_COMMENT_ID:-}" ]; then
    {
      echo ""
      echo "---"
      echo ""
      echo "> **Note:** $INLINE_FAILED of $COUNT inline comment(s) could not be posted (invalid line references). Findings are in the table above."
      echo ""
      echo "<details>"
      echo "<summary>Failed inline comments</summary>"
      echo ""
      jq -r '.[] | "- **[\(.severity)]** `\(.file):\(.line)` — \(.comment | gsub("\n"; " ") | .[0:200])"' \
        /tmp/inline-failures.json
      echo ""
      echo "</details>"
    } > /tmp/inline-failure-appendix.md

    # Read current summary, append failure details, PATCH
    CURRENT_BODY=$(gh api "/repos/$REPO/issues/comments/$SUMMARY_COMMENT_ID" --jq '.body' 2>/dev/null || true)
    if [ -n "$CURRENT_BODY" ]; then
      {
        echo "$CURRENT_BODY"
        cat /tmp/inline-failure-appendix.md
      } > /tmp/review-body-patched.md

      jq -n --rawfile body /tmp/review-body-patched.md '{"body": $body}' \
      | gh api \
        --method PATCH \
        -H "Accept: application/vnd.github+json" \
        "/repos/$REPO/issues/comments/$SUMMARY_COMMENT_ID" \
        --input - >/dev/null 2>&1 || echo "WARNING: Failed to append failure details to summary comment" >&2
    fi
  fi
else
  # No findings to post; Phase 2 script succeeded if we reached here
  PHASE2_CUR=$(jq -r '.phase2_inline' /tmp/review-status.json)
  if [ "$PHASE2_CUR" = "pending" ]; then
    update_status phase2_inline "success"
  fi
fi

# ---------------------------------------------------------------------------
# Apply labels
# ---------------------------------------------------------------------------

CRITICAL=$(jq '[.[] | select(.severity == "Critical")] | length' /tmp/inline-comments.json)
HIGH=$(jq '[.[] | select(.severity == "High")] | length' /tmp/inline-comments.json)

gh label create "ai-reviewed" --color "0075ca" --description "Reviewed by AI" \
  --repo "$REPO" 2>/dev/null || true
gh pr edit "$PR_NUMBER" --repo "$REPO" --add-label "ai-reviewed" 2>/dev/null || true

if [ "$CRITICAL" -gt 0 ]; then
  gh label create "severity:critical" --color "d73a4a" --description "Critical severity finding" \
    --repo "$REPO" 2>/dev/null || true
  gh pr edit "$PR_NUMBER" --repo "$REPO" --add-label "severity:critical" 2>/dev/null || true
elif [ "$HIGH" -gt 0 ]; then
  gh label create "severity:high" --color "e4e669" --description "High severity finding" \
    --repo "$REPO" 2>/dev/null || true
  gh pr edit "$PR_NUMBER" --repo "$REPO" --add-label "severity:high" 2>/dev/null || true
fi

# ---------------------------------------------------------------------------
# Write rollout metrics
# ---------------------------------------------------------------------------

PHASE2_METRICS="{}"
if [ -f /tmp/review-metrics-phase2.json ]; then
  PHASE2_METRICS=$(cat /tmp/review-metrics-phase2.json)
fi

MEDIUM=$(jq '[.[] | select(.severity == "Medium")] | length' /tmp/inline-comments.json)
LOW=$(jq '[.[] | select(.severity == "Low")] | length' /tmp/inline-comments.json)

jq -n \
  --arg model "$SELECTED_MODEL" \
  --arg mode "$MODE" \
  --argjson p1_latency "$PHASE1_LATENCY" \
  --argjson p2_latency "$PHASE2_LATENCY" \
  --argjson phase2_tokens "$PHASE2_METRICS" \
  --argjson finding_count "$COUNT" \
  --argjson inline_posted "$INLINE_POSTED" \
  --argjson inline_failed "$INLINE_FAILED" \
  --argjson critical "$CRITICAL" \
  --argjson high "$HIGH" \
  --argjson medium "$MEDIUM" \
  --argjson low "$LOW" \
  '{
    model: $model,
    mode: $mode,
    phase1_latency_seconds: $p1_latency,
    phase2_latency_seconds: $p2_latency,
    phase2_tokens: $phase2_tokens,
    finding_count: $finding_count,
    inline_comments_posted: $inline_posted,
    inline_comments_failed: $inline_failed,
    severity_breakdown: {Critical: $critical, High: $high, Medium: $medium, Low: $low}
  }' > /tmp/review-metrics.json

# ---------------------------------------------------------------------------
# Propagate Phase 2 failure
# All phases above (summary comment, inline comments, labels) have completed.
# Now fail the step so the "Post failure notice" fires and the job shows red.
# ---------------------------------------------------------------------------

PHASE2_STATUS=$(jq -r '.phase2_inline' /tmp/review-status.json)
if [[ "$PHASE2_STATUS" == failed:* ]]; then
  echo "::error::Phase 2 inline review failed ($PHASE2_STATUS). Summary was still posted."
  exit 1
elif [[ "$PHASE2_STATUS" == partial:* ]]; then
  echo "::warning::Phase 2 inline review partially succeeded ($PHASE2_STATUS). Some comments could not be posted."
fi
