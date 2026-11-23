"""Read a reviewer's JSON reply, and find the concerns its summary names but posts nowhere.

A concern only in the summary is a hidden finding (gtm_toolkit PR #229: "No findings"
under a summary naming three concerns), so it is listed as not posted.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from devloop.review.model import SEVERITIES, Finding, NotPosted, Provider, Review, ReviewError

_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or the to with without after not no".split()
)


def normalise_title(title: str) -> str:
    """Return a title compared across runs: lower case, single spaces, no zero-width spaces."""
    return " ".join(title.replace("\u200b", "").lower().split())


def _words(title: str) -> set[str]:
    """Return the content words of a title, crudely stemmed (``updating`` and ``updated`` agree)."""
    words = set()
    for word in re.findall(r"[a-z0-9_.]+", normalise_title(title)):
        word = word.strip(".")
        if not word or word in _STOPWORDS:
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(word) > len(suffix) + 2 and word.endswith(suffix):
                word = word[: -len(suffix)]
                break
        words.add(word)
    return words


def title_similarity(one: str, other: str) -> float:
    """Word overlap of two titles (Jaccard), 0 to 1."""
    a, b = _words(one), _words(other)
    return len(a & b) / len(a | b) if a and b else 0.0


def parse_review(provider: Provider, text: str) -> Review:
    """Parse and check a reviewer's JSON reply.

    Raises:
        ReviewError: the reply is not the expected JSON. The check fails rather
            than pass on a review it cannot read.

    """
    name = provider.name
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReviewError(f"{name}'s reply is not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise ReviewError(f"{name}'s reply is not a JSON object")
    summary, raw = data.get("summary"), data.get("findings")
    if not isinstance(summary, str) or not isinstance(raw, list):
        raise ReviewError(f"{name}'s reply needs a string 'summary' and a list 'findings'")
    findings: list[Finding] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReviewError(f"finding {index} is not an object")
        severity, path, line = item.get("severity"), item.get("path"), item.get("line")
        title, body = item.get("title"), item.get("body")
        if severity not in SEVERITIES:
            raise ReviewError(f"finding {index} has severity {severity!r}, not one of {SEVERITIES}")
        if not isinstance(path, str) or not path:
            raise ReviewError(f"finding {index} has no path")
        if isinstance(line, bool) or not isinstance(line, int):
            raise ReviewError(f"finding {index} has no integer line")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ReviewError(f"finding {index} needs a string title and body")
        dimension: str | None = None
        if provider.dimensions is not None:
            tag = item.get("dimension")
            if not isinstance(tag, str) or tag not in provider.dimensions:
                raise ReviewError(
                    f"finding {index} has dimension {tag!r}, not one of "
                    f"{tuple(provider.dimensions)}"
                )
            dimension = tag
        findings.append(Finding(severity, path.removeprefix("./"), line, title, body, dimension))
    order = {name: rank for rank, name in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: order[f.severity])
    not_posted = _parse_not_posted(name, data.get("not_posted", []))
    reason = SUMMARY_UNMATCHED_REASON if findings or not_posted else SUMMARY_ONLY_REASON
    not_posted += [
        NotPosted(sentence, reason) for sentence in summary_concerns(summary, findings, not_posted)
    ]
    return Review(summary=summary, findings=tuple(findings), not_posted=tuple(not_posted))


def _parse_not_posted(name: str, raw: object) -> list[NotPosted]:
    """Return the reply's ``not_posted`` concerns.

    The schema requires the list; a reply without it (a reviewer that ignores
    the schema, or an older recorded reply) has none, and its summary is then
    read for concerns (:func:`summary_concerns`).

    Raises:
        ReviewError: ``not_posted`` is not a list of objects with a string title and reason.

    """
    if not isinstance(raw, list):
        raise ReviewError(f"{name}'s reply has a 'not_posted' that is not a list")
    concerns: list[NotPosted] = []
    for index, item in enumerate(raw):
        title = item.get("title") if isinstance(item, dict) else None
        reason = item.get("reason") if isinstance(item, dict) else None
        if not isinstance(title, str) or not isinstance(reason, str):
            raise ReviewError(f"not_posted item {index} needs a string title and reason")
        if not title.strip() or not reason.strip():
            raise ReviewError(f"not_posted item {index} needs a string title and reason")
        concerns.append(NotPosted(title, reason))
    return concerns


#: Words that mark a summary sentence as naming a concern, not just describing the change.
_CONCERN_WORDS = re.compile(
    r"\b(concerns?|issues?|problems?|potential(?:ly)?|could|might|may|should|bugs?|"
    r"regress\w*|gaps?|incorrect(?:ly)?|wrong(?:ly)?|unsafe|breaks?|breaking|risky|"
    r"incomplete|inconsistent|missing|lacks?|unclear|misleading|"
    # Plain failure statements ("The sync crashes when the CRM returns 429."):
    r"crash\w*|fail\w*|los(?:e|es|ing|t|s)|leak\w*|corrupt\w*|overwrit\w*|"
    r"drop(?:s|ped|ping)?|hangs?|hung|deadlock\w*|races?|stale|duplicat\w*|"
    r"vulnerab\w*|injection|exposes?)\b",
    re.IGNORECASE,
)
#: The nouns a reviewer finds nothing of ("No bugs or security issues were found").
_NONE_NOUN = r"(?:concerns?|issues?|problems?|bugs?|risks?|findings?|defects?|regressions?|gaps?)"
_NONE_NOUN_WORD = re.compile(_NONE_NOUN, re.IGNORECASE)
_FOUND_VERB = r"(?:found|identified|raised|detected|seen|spotted|noted)"
#: Where a nothing-found sentence may say it looked ("in the reviewed files").
_LOOKED_TAIL = r"(?P<tail>\s+(?:in|from|across|within)\s+[^.;:!?]*)?"
#: Whole sentences that say the review found nothing; :func:`says_nothing_found`
#: also checks the kinds and the tail against allow-lists. A sentence that only
#: starts this way ("No issues were found, but ...") is read like any other.
_NOTHING_FOUND_SENTENCES = (
    re.compile(
        rf"(?:there\s+(?:were|was|are|is)\s+)?no\s+(?P<kinds>[^.;:!?]*?)\b{_NONE_NOUN}"
        rf"(?:\s+(?:were|was|are|is|have\s+been|has\s+been))?\s+{_FOUND_VERB}"
        rf"{_LOOKED_TAIL}\s*[.!]?",
        re.IGNORECASE,
    ),
    re.compile(rf"no\s+{_NONE_NOUN}\s*[.!]?", re.IGNORECASE),
    re.compile(
        r"(?:i|we|the\s+review|this\s+review)\s+(?:found|identified|saw|see|spotted|detected)"
        rf"\s+no\s+(?P<kinds>[^.;:!?]*?)\b{_NONE_NOUN}{_LOOKED_TAIL}\s*[.!]?",
        re.IGNORECASE,
    ),
)
#: The longest sentence :func:`says_nothing_found` reads; a longer one is read
#: for concern words like any other, and the templates stay linear.
_NOTHING_FOUND_MAX_CHARS = 400
#: The kinds of concern a nothing-found sentence may name: review areas, never
#: a hedge ("obvious", "high-severity", "merge-blocking" leave room for the
#: concerns not named) or a thing a change can lack ("tests", "retry"). Each
#: word of a label, each part of a hyphenated one included, must be one of these.
_KIND_WORDS = frozenset(
    "security privacy performance reliability correctness compliance revenue revops gtm "
    "integration integrations enterprise operability operator experience facing governance "
    "contract contracts production edge case data crm ux accessibility licensing legal "
    "statistical ml model modelling modeling scoring forecasting territory concurrency".split()
)
#: The words a nothing-found sentence may use for where it looked: the change
#: and its parts ("in the workflow configuration, script logic, or test suite").
_PLACE_WORDS = frozenset(
    "reviewed changed change changes diff code review pr pull request files file workflow "
    "workflows configuration config script scripts logic test tests suite module modules "
    "package packages docs documentation schema ci job jobs step steps".split()
)
#: Labels are split at commas, "and" and "or"; every part must still be an
#: allowed label, so "and" joins labels, never clauses ("No bugs and no tests").
_KIND_LABELS = re.compile(r"(,\s*(?:(?:and|or)\s+)?|\s+(?:and|or)\s+)", re.IGNORECASE)
#: The only joint allowed after a label with a :data:`_NONE_NOUN` word: "No bugs
#: or security issues" names nothing found, while "No bugs, and security issues
#: were found" and "No bugs, security issues were found" say something was.
_AFTER_NOUN = re.compile(r"\s+or\s+", re.IGNORECASE)


def _labels_of(text: str, allowed: frozenset[str], noun_ok: bool) -> bool:
    """Whether ``text`` is a list of labels made only of ``allowed`` words.

    The labels are split at commas, "and" and "or"; each is one to three words, a
    hyphenated word counts by its parts, and with ``noun_ok`` a
    :data:`_NONE_NOUN` word is allowed too ("bugs or security issues"), but a
    label with one may be followed only by a plain "or" (:data:`_AFTER_NOUN`).
    """
    parts = _KIND_LABELS.split(text.strip())
    labels, joints = parts[0::2], [*parts[1::2], ""]
    for label, joint in zip(labels, joints, strict=True):
        words = label.split()
        if not 1 <= len(words) <= 3:
            return False
        has_noun = False
        for word in words:
            if noun_ok and _NONE_NOUN_WORD.fullmatch(word):
                has_noun = True
                continue
            if not all(part in allowed for part in word.lower().split("-")):
                return False
        if has_noun and joint and not _AFTER_NOUN.fullmatch(joint):
            return False
    return True


def says_nothing_found(sentence: str) -> bool:
    """Whether the whole ``sentence`` only says the review found nothing.

    It must match one of :data:`_NOTHING_FOUND_SENTENCES` from start to end,
    name its kinds only from :data:`_KIND_WORDS` (or none: "No concerns.") and
    where it looked only from :data:`_PLACE_WORDS` after "the" or "this", and
    be at most :data:`_NOTHING_FOUND_MAX_CHARS` long. Everything else, however
    close, is read for concern words: when in doubt a sentence is listed.
    """
    if len(sentence) > _NOTHING_FOUND_MAX_CHARS:
        return False
    for template in _NOTHING_FOUND_SENTENCES:
        found = template.fullmatch(sentence)
        if found is None:
            continue
        groups = found.groupdict()
        kinds = (groups.get("kinds") or "").strip()
        tail = (groups.get("tail") or "").strip()
        if kinds and not _labels_of(kinds, _KIND_WORDS, noun_ok=True):
            continue
        if tail:
            place = re.sub(r"^\w+\s+(?:(?:the|this|these)\s+)?", "", tail, flags=re.IGNORECASE)
            if not _labels_of(place, _PLACE_WORDS, noun_ok=False):
                continue
        return True
    return False


#: The change as a sentence's subject: "This", "This change", "The new PR", "It also".
#: A bare "The" or "It" is not enough: "The fixes leak memory", "It blocks forever".
_CHANGE = r"(?:change|pr|pull\s+request|commit|patch|update|fix|diff|round)"
_SUBJECT = (
    r"(?:this(?:\s+(?:incremental|new|small|follow-up))?(?:\s+" + _CHANGE + r")?"
    r"|the(?:\s+(?:incremental|new|small|follow-up))?\s+" + _CHANGE + r"|it\s+also)"
    r"(?:\s+(?:also|now))?"
)
#: One word of free text: ASCII letters, digits, hyphens, apostrophes and underscores, or
#: a code span (`.gitattributes`). Any other character (a dash, an ellipsis, a fullwidth
#: stop) rejects the template, since it could start another clause.
_WORD = r"(?:`[^`\s]+`|[A-Za-z0-9_'-])+"
_SPACE = r"[ \t]+"
_END = r"[.!]?"
#: The holes a "closes" or "prevents" sentence may name: never a safeguard ("the check").
_HOLE = r"(?:paths?|gaps?|holes?|windows?|cases?|routes?|loopholes?)"
#: The only words the "capture failed X" shape allows before "to": what records the
#: failures, never an outcome they could be counted as ("the success counter").
_RECORDERS = frozenset(
    "the its loop review model usage token metrics telemetry log logs report reports "
    "marker markers".split()
)
#: The only words the "distinguish a failed X" shape allows before "to": a gate that
#: judges reviewers, never a page that only shows them ("the docs").
_CHECKERS = frozenset("the its labels label review status check checks gate".split())
#: Verbs in a closing clause that say the hole can come back, so a risk remains.
_COMES_BACK = re.compile(
    r"\b(?:re-?open\w*|reintroduc\w*|resurfac\w*|bypass\w*|undo\w*|undid|revert\w*|"
    r"reappear\w*|return\w*|come\s+back|comes\s+back)\b",
    re.IGNORECASE,
)
#: The nouns a "failed" may name in a description ("failed model attempts").
_FAILED_NOUN = (
    r"(?:attempts?|requests?|reviewers?|runs?|jobs?|calls?|checks?|writes?|builds?|steps?)"
)
#: Words that may not appear in free text: each could start a second clause, hedge the
#: fix, or say a concern remains ("the tests don't cover", "the reader also has").
_CONNECTIVES = frozenset(
    "and or but then because when while if unless so as to though although yet except "
    "however whereas albeit nor otherwise also again still only until which with without "
    "instead forever never not no none nothing cannot can't don't doesn't isn't aren't "
    "won't wasn't weren't by at for via using through of one".split()
)
#: Words that turn a ", with ... tests updated" tail into a missing or disabled check. The
#: tail is never masked, so a concern word in it still counts.
_TAIL_DENIALS = frozenset(
    "no not without none never removed disabled skipped xfail fewer flaky".split()
)
#: A hypothetical in a relative clause states the hole being closed ("could hide").
_HYPOTHETICAL = frozenset({"could", "might", "would"})
_FREE = r"(?:" + _SPACE + _WORD + r")"
#: Whole-sentence templates for a sentence that only describes what the change does or
#: fixes: the three shapes of the live false concerns (gtm_toolkit #264-#271), and no
#: more. Each looser version let a real concern through (three council rounds). Named
#: ``free*``, ``clause``, ``recorders`` and ``checkers`` groups are free text, checked by
#: :func:`_free_text_ok`; ``masked`` is the only part that may hold concern words.
_DESCRIPTIONS = tuple(
    re.compile(template, re.IGNORECASE)
    for template in (
        # "This closes the paths where X could hide Y, with corresponding tests updated."
        _SUBJECT
        + _SPACE
        + r"(?:closes|prevents)"
        + _SPACE
        + r"(?P<masked>(?:the|a|an)"
        + _SPACE
        + _HOLE
        + _SPACE
        + r"(?:where|in"
        + _SPACE
        + r"which)(?P<clause>"
        + _FREE
        + r"{2,16}))"
        + r"(?:,"
        + _SPACE
        + r"with(?P<tail>"
        + _FREE
        + r"{0,4})"
        + _SPACE
        + r"tests"
        + _SPACE
        + r"(?:updated|added))?"
        + _END,
        # "The change extends loop metrics to capture failed model attempts."
        _SUBJECT
        + _SPACE
        + r"(?:extends|updates|hardens|adds)(?P<recorders>"
        + _FREE
        + r"{1,3})"
        + _SPACE
        + r"to"
        + _SPACE
        + r"(?:capture|track|count|record)"
        + _SPACE
        + r"(?P<masked>failed)(?:"
        + _SPACE
        + r"model)?(?:"
        + _SPACE
        + r"request)?"
        + _SPACE
        + _FAILED_NOUN
        + _END,
        # "It also hardens the labels check to distinguish a failed reviewer from a pending one."
        _SUBJECT
        + _SPACE
        + r"(?:extends|updates|hardens|adds)(?P<checkers>"
        + _FREE
        + r"{1,3})"
        + _SPACE
        + r"to"
        + _SPACE
        + r"distinguish"
        + _SPACE
        + r"an?"
        + _SPACE
        + r"(?P<masked>failed)"
        + _SPACE
        + _FAILED_NOUN
        + _SPACE
        + r"from"
        + _SPACE
        + r"an?(?P<free2>"
        + _FREE
        + r")"
        + _SPACE
        + r"one"
        + _END,
    )
)
#: The longest sentence :func:`_described` reads; a longer one is read like any other.
_DESCRIPTION_MAX_CHARS = 400


def _free_text_ok(found: re.Match[str]) -> bool:
    """Return whether a template's free text and clause hold no connective.

    The fixed-shape ", with ... tests updated" tail may join words with "and", but may not
    deny a test (:data:`_TAIL_DENIALS`).

    A ``clause`` must also be hypothetical (:data:`_HYPOTHETICAL`): "where X could hide
    Y" states the hole the change closes; "that holds failed writes" may be a safeguard.
    """
    groups = found.groupdict()
    tail = {w.lower() for w in re.split(r"[\s-]+", groups.get("tail") or "") if w}
    if tail & _TAIL_DENIALS:  # "with no tests added": it says what is missing
        return False
    free = " ".join(v for k, v in groups.items() if k.startswith("free") and v)
    clause = groups.get("clause") or ""
    words = [w.strip("`").lower() for w in re.split(r"[\s-]+", f"{free} {clause}") if w]
    if any(w in _CONNECTIVES or w.endswith("n't") for w in words):
        return False
    for name, allowed in (("recorders", _RECORDERS), ("checkers", _CHECKERS)):
        if groups.get(name) is not None:
            if not set(re.split(r"[\s-]+", groups[name].strip().lower())) <= allowed:
                return False
    if clause and _COMES_BACK.search(clause):
        return False
    return not clause or bool(_HYPOTHETICAL & {w.lower() for w in clause.split()})


def _described(sentence: str) -> str:
    """Return ``sentence`` with the words that only describe the change masked.

    Only a whole sentence matching one of :data:`_DESCRIPTIONS` is masked: an allowlist
    of narrow shapes, so an unforeseen phrasing leaves the sentence listed. Its free text
    may hold only plain words with no connective (:func:`_free_text_ok`), and only its
    ``masked`` part (the problem a closing verb removes, or "failed" naming what the
    change now handles) is removed; any concern word left elsewhere still counts.
    """
    text = sentence.strip()
    if len(text) > _DESCRIPTION_MAX_CHARS:
        return sentence
    for template in _DESCRIPTIONS:
        found = template.fullmatch(text)
        if found is not None and _free_text_ok(found):
            start, end = found.span("masked")
            return text[:start] + text[end:]
    return sentence


def names_concern(sentence: str) -> bool:
    """Whether one summary sentence names a concern, not only what the change does.

    A sentence names a concern when it has a :data:`_CONCERN_WORDS` word. When
    in doubt it does. Two exceptions: a whole sentence that only says the review found
    nothing of named review areas (:func:`says_nothing_found`: "No bugs or security
    issues were found in the workflow configuration", PRs #264-#266), and a sentence that
    only describes what the change does or fixes (:func:`_described`: "This closes the
    paths where a pull request's `.gitattributes` could hide changed content", "The
    change extends loop metrics to capture failed model attempts", gtm_toolkit #264-#271).
    "No issues were found, but the writer could lose rows", "This change adds a cache
    that may serve stale scores" and every failure word outside those two shapes are
    still listed.
    """
    if says_nothing_found(sentence):
        return False
    return bool(_CONCERN_WORDS.search(_described(sentence)))


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
#: Why a concern the summary names alone is listed as not posted.
SUMMARY_ONLY_REASON = (
    "named only in the review's summary: the reply gave no finding with a file and line for it"
)
#: Why a summary concern of a reply with findings, but matching none of them, is listed.
SUMMARY_UNMATCHED_REASON = (
    "named in the review's summary, but it names no finding's file and shares few words "
    "with any finding's or listed concern's title: check whether one above covers it"
)
#: The share of a title's words a summary sentence must contain to be about it.
SUMMARY_TITLE_COVER = 0.5


def _has_title(sentence: str, title: str) -> bool:
    """Whether a summary sentence has most of a title's words (:data:`SUMMARY_TITLE_COVER`)."""
    words = _words(title)
    return bool(words) and len(words & _words(sentence)) / len(words) >= SUMMARY_TITLE_COVER


def _about_finding(sentence: str, finding: Finding) -> bool:
    """Whether a summary sentence is about ``finding``: it names its file, or most title words."""
    if finding.path.rsplit("/", 1)[-1].lower() in sentence.lower():
        return True
    return _has_title(sentence, finding.title)


def summary_concerns(
    summary: str, findings: Sequence[Finding] = (), listed: Sequence[NotPosted] = ()
) -> list[str]:
    """Return the sentences of ``summary`` that name a concern no finding covers.

    A summary should name only the findings and ``not_posted`` concerns the
    reply lists; a sentence that names any other concern (:func:`names_concern`)
    is a hidden finding (PR #229: "**No findings**" under a summary naming
    three concerns), so it is listed as not posted. A sentence about a finding
    (:func:`_about_finding`) or about a ``listed`` concern (most of its title's
    words) is left out. A false match only lists a harmless sentence; a miss
    would hide a concern, so the word list is broad, the exceptions narrow and
    the match to a finding strict.
    """
    sentences = (" ".join(part.split()) for part in _SENTENCE_END.split(summary))
    return [
        sentence
        for sentence in sentences
        if sentence
        and names_concern(sentence)
        and not any(_about_finding(sentence, finding) for finding in findings)
        and not any(_has_title(sentence, concern.title) for concern in listed)
    ]
