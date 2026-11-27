"""The retry function every shared workflow step that calls ``gh api`` starts with.

A reusable workflow called from another repository cannot share a script file with its
caller, so the function is repeated in each step. Its text is pinned here once;
tests/test_workflow_retries.py runs it and checks every step carries exactly this text.
"""

from __future__ import annotations

#: The function, word for word, as it reads at the top of a step's ``run``.
GH_API = """\
gh_api() {  # gh api; tried again after 2 s, then 4 s, on HTTP 429, a 5xx or a lost connection
  local try out err
  err="$(mktemp)"
  for try in 1 2 3; do
    if out="$(gh api "$@" 2>"$err")"; then
      if [ -n "$out" ]; then printf '%s\\n' "$out"; fi
      return 0
    fi
    case "$(<"$err")" in
      *"HTTP 429"* | *"HTTP 5"[0-9][0-9]* | *"error connecting to"* | *' "https://'*'": '*) ;;
      *) break ;;
    esac
    if [ "$try" -lt 3 ]; then sleep "$((try * 2))"; fi
  done
  { printf '%s\\n' "$out"; cat "$err"; } >&2
  return 1
}
"""

#: The one bare ``gh api`` call a step may make: creating the settings issue. A create is
#: not safe to send again blindly, because GitHub may have made the issue and lost the
#: reply. That step looks for the issue again before each new try (settings-check.yml).
ISSUE_CREATE = 'gh api "$issues" -f title="$TITLE" -f "labels[]=${LABEL}" \\\n'
