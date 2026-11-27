"""The shared workflows' own ``gh api`` calls retry a transient failure, and only that.

One 502 from GitHub used to turn a job red, and for a step that writes a status it could
leave a wrong status standing. Every step that calls ``gh api`` now starts with one small
function, ``gh_api`` (tests/workflow_retry.py pins its text), which tries again on HTTP
429, a 5xx or a lost connection and returns any other failure at once. The function is
run here against a fake ``gh``, and so are the steps whose failure must still fail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_retry import GH_API

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
FILES = sorted(WORKFLOWS.glob("*.yml"))
OK = (0, "", "")
BAD_GATEWAY = (1, "gh: Server Error (HTTP 502)", '{"message":"Server Error"}')
UNAVAILABLE = (1, "gh: HTTP 503", "<html>no</html>")
LIMITED = (1, "gh: API rate limit exceeded (HTTP 429)", "")
NOT_FOUND = (1, "gh: Not Found (HTTP 404)", '{"message":"Not Found"}')
FORBIDDEN = (1, "gh: Resource not accessible by integration (HTTP 403)", "")
INVALID = (1, "gh: Validation Failed (HTTP 422)", "")
REFUSED = (
    1,
    'Post "https://api.github.com/repos/o/r/statuses/abc": dial tcp: connection refused',
    "",
)
NO_HOST = (1, "error connecting to api.github.com", "")
Reply = tuple[int, str, str]


def _run(
    tmp_path: Path, script: str, replies: list[Reply], **env: str
) -> tuple[int, str, str, list[str], list[str]]:
    """Run ``script`` as a workflow step does, with a fake ``gh`` that gives ``replies`` in turn.

    Return the exit code, stdout, stderr, the arguments of each ``gh`` call and the
    seconds each ``sleep`` was asked for (the fake ``sleep`` returns at once).
    """
    bin_dir, plan = tmp_path / "bin", tmp_path / "plan"
    bin_dir.mkdir(exist_ok=True)
    plan.mkdir(exist_ok=True)
    for old in plan.iterdir():
        old.unlink()
    for number, (code, err, out) in enumerate(replies, start=1):
        (plan / f"{number}.code").write_text(str(code), encoding="utf-8")
        (plan / f"{number}.err").write_text(err, encoding="utf-8")
        (plan / f"{number}.out").write_text(out, encoding="utf-8")
    asked, slept = tmp_path / "asked", tmp_path / "slept"
    asked.write_text("", encoding="utf-8")
    slept.write_text("", encoding="utf-8")
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        f"for arg in \"$@\"; do printf '[%s]' \"$arg\" >> '{asked}'; done; echo >> '{asked}'\n"
        f"n=$(wc -l < '{asked}' | tr -d ' ')\n"
        f"[ -f '{plan}'/$n.code ] || {{ echo 'gh: no reply planned' >&2; exit 9; }}\n"
        f'out=$(cat \'{plan}\'/$n.out); [ -z "$out" ] || echo "$out"\n'
        f'err=$(cat \'{plan}\'/$n.err); [ -z "$err" ] || echo "$err" >&2\n'
        f"exit $(cat '{plan}'/$n.code)\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text(f"#!/bin/sh\necho \"$1\" >> '{slept}'\n", encoding="utf-8")
    sleep.chmod(0o755)
    done = subprocess.run(
        ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "GITHUB_REPOSITORY": "o/r", **env},
        capture_output=True,
        text=True,
        check=False,
    )
    calls = asked.read_text(encoding="utf-8").splitlines()
    return done.returncode, done.stdout, done.stderr, calls, slept.read_text().split()


CALL = GH_API + 'gh_api "repos/o/r/x y" --jq .a\n'
ASKED = "[api][repos/o/r/x y][--jq][.a]"


def test_a_call_that_succeeds_is_made_once_and_its_output_passed_on(tmp_path: Path) -> None:
    assert _run(tmp_path, CALL, [(0, "", "7")]) == (0, "7\n", "", [ASKED], [])
    assert _run(tmp_path, CALL, [OK]) == (0, "", "", [ASKED], [])


@pytest.mark.parametrize(
    "transient",
    [BAD_GATEWAY, UNAVAILABLE, LIMITED, REFUSED, NO_HOST],
    ids=["502", "503", "429", "connection-refused", "no-host"],
)
def test_a_transient_failure_is_tried_again_and_only_the_good_answer_is_output(
    tmp_path: Path, transient: Reply
) -> None:
    """The failed try's body never reaches stdout, where a caller would read it as the answer."""
    code, out, _, calls, slept = _run(tmp_path, CALL, [transient, (0, "", "7")])
    assert (code, out, calls, slept) == (0, "7\n", [ASKED, ASKED], ["2"])


def test_three_transient_failures_fail_after_waits_of_two_and_four_seconds(tmp_path: Path) -> None:
    code, out, err, calls, slept = _run(tmp_path, CALL, [UNAVAILABLE] * 4)
    assert (code, out, calls, slept) == (1, "", [ASKED] * 3, ["2", "4"])
    assert "HTTP 503" in err


@pytest.mark.parametrize(
    "final",
    [NOT_FOUND, FORBIDDEN, INVALID, (1, "", ""), (1, "jq: error: bad filter", "")],
    ids=["404", "403", "422", "silent", "jq"],
)
def test_any_other_failure_is_returned_at_once(tmp_path: Path, final: Reply) -> None:
    """A 4xx is an answer, not an outage: asking again would not change it."""
    code, out, err, calls, slept = _run(tmp_path, CALL, [final, OK, OK])
    assert (code, out, calls, slept) == (1, "", [ASKED], [])
    assert final[1] in err


def test_a_transient_failure_then_a_final_one_stops_there(tmp_path: Path) -> None:
    code, _, err, calls, slept = _run(tmp_path, CALL, [BAD_GATEWAY, FORBIDDEN, OK])
    assert (code, len(calls), slept) == (1, 2, ["2"]) and "HTTP 403" in err


def _steps(path: Path) -> list[dict[str, Any]]:
    """Return every step of every job in the workflow at ``path``."""
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    return [step for job in jobs.values() for step in job.get("steps", [])]


def _step(workflow: str, job: str, name: str) -> str:
    """Return the ``run`` text of the step called ``name``."""
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"]
    found = [str(s["run"]) for s in jobs[job]["steps"] if s.get("name") == name]
    assert len(found) == 1, (workflow, job, name)
    return found[0]


@pytest.mark.parametrize("path", FILES, ids=[p.name for p in FILES])
def test_every_step_that_calls_gh_api_starts_with_the_same_function(path: Path) -> None:
    """The one bare ``gh api`` in a step is the function's own; every call goes through it."""
    for step in _steps(path):
        run = str(step.get("run", ""))
        if "gh api" not in run and "gh_api" not in run:
            continue
        assert run.startswith(GH_API), f"{path.name}: {step.get('name')}: no retry function"
        rest = run[len(GH_API) :]
        assert "gh api" not in rest, f"{path.name}: {step.get('name')}: a bare gh api call"
        assert "gh_api " in rest, f"{path.name}: {step.get('name')}: the function is unused"
        assert "gh_api()" not in rest, f"{path.name}: {step.get('name')}: defined twice"


def test_the_shared_workflows_call_gh_api_in_the_ten_known_steps() -> None:
    """A new call is a new line here, so its retry and its failure are thought about."""
    found = {
        (path.name, str(step.get("name")))
        for path in FILES
        for step in _steps(path)
        if "gh_api " in str(step.get("run", ""))
    }
    guard = "Check a status for this pull request may be written on its head"
    assert found == {
        ("gate.yml", "Find the head commit"),
        ("gate.yml", "Report a run that could not check"),
        ("labels.yml", "Check the pull request number and find its head"),
        ("labels.yml", "Report a run that posted no verdict"),
        ("review.yml", guard),
        ("review.yml", "Say the review waits for a maintainer"),
        ("review.yml", "Mark the review pending on the head commit"),
        ("review.yml", "Report the review's outcome on the head commit"),
        ("unapprove.yml", "Remove the approval label"),
    }
    review = [s for s in _steps(WORKFLOWS / "review.yml") if s.get("name") == guard]
    assert len(review) == 2  # the gate job's guard and the review job's: ten steps in all


def test_the_function_holds_no_expression_and_is_short() -> None:
    assert "${{" not in GH_API and len(GH_API.splitlines()) <= 20


FORK = {"HEAD_REPO": "stranger/r", "HEAD_SHA": "a" * 40}
GUARD_STEP = (
    "review.yml",
    "review",
    "Check a status for this pull request may be written on its head",
)


def test_the_status_guard_reads_again_after_a_transient_failure(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    code, _, _, calls, _ = _run(
        tmp_path, _step(*GUARD_STEP), [BAD_GATEWAY, (0, "", "0")], GITHUB_OUTPUT=str(output), **FORK
    )
    assert (code, len(calls), output.read_text()) == (0, 2, "due=true\n")


@pytest.mark.parametrize("replies", [[UNAVAILABLE] * 3, [FORBIDDEN], [BAD_GATEWAY, NOT_FOUND]])
def test_the_status_guard_still_fails_closed_when_the_read_fails(
    tmp_path: Path, replies: list[Reply]
) -> None:
    """Could not check is neither due nor not due: no output, a failed step."""
    output = tmp_path / "output"
    output.write_text("", encoding="utf-8")
    code, _, _, calls, _ = _run(
        tmp_path, _step(*GUARD_STEP), replies, GITHUB_OUTPUT=str(output), **FORK
    )
    assert code != 0 and output.read_text() == "" and len(calls) == len(replies)


LABEL = {"PR_NUMBER": "7", "APPROVAL_LABEL": "ai-review-approved"}


@pytest.mark.parametrize(
    ("replies", "expected", "calls"),
    [
        ([OK], 0, 1),
        ([NOT_FOUND], 0, 1),
        ([BAD_GATEWAY, OK], 0, 2),
        # The first try removed the label and its answer was lost: gone is the state wanted.
        ([REFUSED, NOT_FOUND], 0, 2),
        ([FORBIDDEN], 1, 1),
        ([BAD_GATEWAY, FORBIDDEN], 1, 2),
        ([UNAVAILABLE] * 3, 1, 3),
    ],
)
def test_withdrawing_approval_retries_an_outage_and_still_fails_on_any_final_failure(
    tmp_path: Path, replies: list[Reply], expected: int, calls: int
) -> None:
    """A 404 is success; a 403, or an outage that outlasts the tries, fails the job."""
    script = _step("unapprove.yml", "unapprove", "Remove the approval label")
    code, _, _, asked, _ = _run(tmp_path, script, replies, **LABEL)
    assert (code, len(asked)) == (expected, calls)
    assert set(asked) == {"[api][-X][DELETE][repos/o/r/issues/7/labels/ai-review-approved]"}


@pytest.mark.parametrize(
    ("workflow", "job", "name"),
    [
        ("review.yml", "gate", "Say the review waits for a maintainer"),
        ("review.yml", "review", "Mark the review pending on the head commit"),
        ("review.yml", "review", "Report the review's outcome on the head commit"),
        ("labels.yml", "labels", "Report a run that posted no verdict"),
        ("gate.yml", "gate", "Report a run that could not check"),
    ],
)
def test_a_status_post_is_sent_again_after_an_outage_and_fails_when_it_never_lands(
    tmp_path: Path, workflow: str, job: str, name: str
) -> None:
    """The same post, whole, each time; a status that was never written is a red step."""
    env = {
        "HEAD_SHA": "a" * 40,
        "SHA": "a" * 40,
        "PROVIDER": "gemini",
        "APPROVAL_LABEL": "ok",
        "RUN_URL": "https://example.invalid/run",
        "CONTEXT": "Merge gate",
        "OUTCOME": "success",
        "DUE": "true",
        "DESCRIPTION": "",
    }
    # ${PROVIDER^} needs bash 4, which the runner has and an older local bash may not.
    script = _step(workflow, job, name).replace("${PROVIDER^}", "Gemini")
    code, _, _, calls, slept = _run(tmp_path, script, [BAD_GATEWAY, OK], **env)
    assert (code, len(calls), slept) == (0, 2, ["2"]) and calls[0] == calls[1]
    assert calls[0].startswith(f"[api][repos/o/r/statuses/{'a' * 40}][-f][state=")
    assert "[target_url=https://example.invalid/run]" in calls[0]
    assert _run(tmp_path, script, [UNAVAILABLE] * 3, **env)[0] != 0
    code, _, _, calls, slept = _run(tmp_path, script, [INVALID, OK], **env)
    assert code != 0 and len(calls) == 1 and slept == []
