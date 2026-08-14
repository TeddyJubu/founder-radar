"""The match ladder — turn many mentions into one company (05-pipeline §4.2).

Evaluated top-down, stopping at the first tier that matches, and the tier that
fired is recorded so every merge can be explained and undone:

| # | Rule                                                  | Action       | Conf |
|---|-------------------------------------------------------|--------------|------|
| 0 | Companies House number exact                          | auto-merge   | 1.00 |
| 1 | Registrable domain exact, both off the denylist       | auto-merge   | 0.97 |
| 2 | `norm_key` exact and country matches (or one unknown) | auto-merge   | 0.95 |
| 3 | `norm_key` exact, countries conflict                  | distinct     | —    |
| 4 | Fuzzy >= 92, rare-token guard passes, no conflict     | auto-merge   | 0.90 |
| 5 | Fuzzy 84-91, or person-named family firms             | review queue | —    |
| 6 | Fuzzy < 84                                            | distinct     | —    |

Tier 3 is **distinct**, not review, because 09-test-plan §2.1 pins it
("Acme Robotics Ltd (GB)" vs "Acme Robotics Inc (US)" → DISTINCT) and
03-data-model §6 query 9 says the ladder deliberately keeps same-name-
different-jurisdiction companies apart — the same rule as tier 0's `ch_conflict`:
a shared name must not override a legal identity conflict. (05-pipeline §4.2's
table says review; the test plan and data model are the more specific statements
and win.)

Fuzzy scoring uses `rapidfuzz.fuzz.token_sort_ratio` over `norm_name()` output.
The scorers that score a subset at 100 are banned outright — see `fuzzy_score`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from rapidfuzz import fuzz
from rapidfuzz.distance import JaroWinkler

from .normalise import (
    is_placeholder_name,
    norm_ch_number,
    norm_country,
    norm_domain,
    norm_key,
    norm_name,
    rare_tokens,
)

FUZZY_AUTO_MERGE = 92.0     # tier 4
FUZZY_REVIEW_FLOOR = 84.0   # tier 5
SHORT_NAME_CHARS = 12       # indel ratios are unstable below this
SHORT_NAME_JARO = 0.90
MAX_FUZZY_CLUSTER = 3       # cap a fuzzy cluster before forcing review

# 09-test-plan §2.1: "Smith & Partners" vs "Smith & Sons" — person-named
# companies sharing a rare token go to the review queue. The marker is
# structural (family-firm words), not a name list: two firms named after the
# same family are usually distinct, but a rename of a family firm is plausible
# enough that a human should look. Without the marker this rule would sweep in
# every near-name ("Acme Robotics" vs "Acme Robotics Automotive Division"),
# which the spec pins as DISTINCT.
FAMILY_MARKERS = frozenset({
    "sons", "daughters", "partners", "associates", "brothers", "co", "family",
})

MERGE, REVIEW, DISTINCT = "merge", "review", "distinct"


@dataclass(frozen=True)
class Record:
    """One mention of a company, or one stored company, reduced to its keys."""

    name: str | None = None
    ch_number: str | None = None
    domain: str | None = None
    country_iso2: str | None = None
    company_id: str | None = None
    first_seen: str | None = None

    @property
    def norm_name(self) -> str:
        return norm_name(self.name)

    @property
    def norm_key(self) -> str:
        return norm_key(self.name)

    @property
    def ch(self) -> str | None:
        return norm_ch_number(self.ch_number)

    @property
    def dom(self) -> str | None:
        return norm_domain(self.domain)

    @property
    def country(self) -> str | None:
        return norm_country(self.country_iso2)


@dataclass(frozen=True)
class MatchResult:
    """What the ladder decided, why, and with what evidence."""

    decision: str                      # merge | review | distinct
    rule: str                          # ch_exact | domain_exact | normkey_exact | fuzzy | ...
    tier: int
    score: float | None = None         # the fuzzy score, when one was computed
    confidence: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def merged(self) -> bool:
        return self.decision == MERGE

    def to_json(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "rule": self.rule,
            "tier": self.tier,
            "score": self.score,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def fuzzy_score(a: str, b: str) -> float:
    """Similarity of two `norm_name` strings, 0-100.

    `rapidfuzz.fuzz.token_sort_ratio` is the scorer named by 05-pipeline §4.2.
    `fuzz.ratio` is taken alongside it and the larger of the two is used.

    # Why both: token_sort_ratio alone scores ("acme robotics", "acme robotic
    # arms") at 80, but 09-test-plan §2.1 states 87 for that pair and expects it
    # in the review band. fuzz.ratio gives 86.67 -> 87, so the spec's own number
    # is the maximum of the two. Both are full-string indel ratios, so neither
    # has the "a subset scores 100" behaviour that makes token_set_ratio,
    # partial_ratio and WRatio unusable here — ("acme robotics", "acme robotics
    # automotive division") scores 56 under both, not 100.
    """
    if not a or not b:
        return 0.0
    return max(fuzz.token_sort_ratio(a, b), fuzz.ratio(a, b))


def guard_ok(a: str, b: str) -> bool:
    """The rare-token guard: a fuzzy merge needs a shared *distinctive* token.

    "AI Labs" and "Tech Solutions" have no distinctive token at all, so no
    amount of string similarity may merge them.
    """
    ra, rb = rare_tokens(a), rare_tokens(b)
    if not ra or not rb:
        return False
    return bool(ra & rb)


def _person_named_shared_token(a: str, b: str) -> bool:
    """Both names look like family firms and share a distinctive token."""
    toks_a, toks_b = set(a.split()), set(b.split())
    if not (toks_a & FAMILY_MARKERS) or not (toks_b & FAMILY_MARKERS):
        return False
    return bool(rare_tokens(a) & rare_tokens(b))


CORPORATE_ROLE_MARKERS = frozenset({
    "capital", "fund", "funds", "group", "groups", "holding", "holdings",
    "investment", "investments", "management", "parent", "partners",
    "venture", "ventures",
})


def _corporate_role_conflict(a: str, b: str) -> bool:
    """Keep near-identical parent/investor names out of auto-merge."""
    roles_a = set(norm_name(a).split()) & CORPORATE_ROLE_MARKERS
    roles_b = set(norm_name(b).split()) & CORPORATE_ROLE_MARKERS
    return bool(roles_a or roles_b) and roles_a != roles_b


def _country_conflict(a: Record, b: Record) -> bool:
    """True only when both countries are known and they differ.

    One side unknown is not a conflict — 03-data-model §2 keeps `country_iso2`
    NULL until something confirms it, so treating NULL as a mismatch would
    block every legitimate merge with a registry record.
    """
    return bool(a.country and b.country and a.country != b.country)


def compare(a: Record, b: Record) -> MatchResult:
    """The ladder itself. Pure: no database, no network, no clock."""
    a_name, b_name = a.norm_name, b.norm_name
    base = {"a_name": a.name, "b_name": b.name,
            "a_norm": a_name, "b_norm": b_name,
            "a_country": a.country, "b_country": b.country}

    # ---- tier 0: Companies House number. The legal record beats everything.
    if a.ch and b.ch:
        if a.ch == b.ch:
            return MatchResult(MERGE, "ch_exact", 0, None, 1.00,
                               {**base, "shared_key": a.ch, "kind": "ch"})
        # Two different numbers are two different legal entities — 'SC445790'
        # is not '00445790'. Stop here; a shared name must not override it.
        return MatchResult(DISTINCT, "ch_conflict", 0, None, 1.00,
                           {**base, "a_ch": a.ch, "b_ch": b.ch})

    # ---- tier 1: registrable domain, both off the denylist.
    if a.dom and b.dom and a.dom == b.dom:
        return MatchResult(MERGE, "domain_exact", 1, None, 0.97,
                           {**base, "shared_key": a.dom, "kind": "domain"})

    # ---- placeholder guard, before any name-based tier.
    if is_placeholder_name(a.name) or is_placeholder_name(b.name):
        return MatchResult(DISTINCT, "placeholder", 2, None, None,
                           {**base, "placeholder": [k for k in (a.norm_key, b.norm_key)
                                                    if is_placeholder_name(k)]})

    # ---- tiers 2 and 3: exact normalised name.
    if a.norm_key and a.norm_key == b.norm_key:
        if _country_conflict(a, b):
            # Same name, different jurisdiction — two different legal entities.
            # A shared name must not override a jurisdiction conflict, exactly
            # as two different CH numbers override a shared name in tier 0.
            return MatchResult(DISTINCT, "normkey_country_conflict", 3, 100.0, None,
                               {**base, "shared_key": a.norm_key})
        return MatchResult(MERGE, "normkey_exact", 2, 100.0, 0.95,
                           {**base, "shared_key": a.norm_key, "kind": "norm_key"})

    # ---- tiers 4, 5 and 6: fuzzy.
    score = fuzzy_score(a_name, b_name)
    ev = {**base, "score": round(score, 2)}

    if score >= FUZZY_AUTO_MERGE:
        if _corporate_role_conflict(a_name, b_name):
            return MatchResult(REVIEW, "fuzzy_corporate_role_conflict", 4, score, None,
                               {**ev, "reason": "corporate role marker differs"})
        if not guard_ok(a_name, b_name):
            # ponytail: the spec makes the guard a precondition of the auto-merge
            # but does not say where a guard failure lands. Review, not distinct:
            # the strings really are near-identical, so a human should look.
            return MatchResult(REVIEW, "fuzzy_guard_failed", 4, score, None,
                               {**ev, "reason": "no shared distinctive token"})
        if _country_conflict(a, b):
            return MatchResult(REVIEW, "fuzzy_country_conflict", 4, score, None, ev)
        if min(len(a_name), len(b_name)) <= SHORT_NAME_CHARS:
            jaro = JaroWinkler.normalized_similarity(a_name, b_name)
            ev["jaro_winkler"] = round(jaro, 4)
            if jaro < SHORT_NAME_JARO:
                # Indel ratios are unstable on short strings; one character of
                # difference in a six-character name costs about 17 points.
                return MatchResult(REVIEW, "fuzzy_short_name", 4, score, None, ev)
        return MatchResult(MERGE, "fuzzy", 4, score, 0.90, ev)

    if score >= FUZZY_REVIEW_FLOOR:
        return MatchResult(REVIEW, "fuzzy", 5, score, None, ev)

    if _person_named_shared_token(a_name, b_name):
        return MatchResult(REVIEW, "person_name_shared_token", 5, score, None,
                           {**ev, "reason": "family-firm names share a distinctive token"})

    return MatchResult(DISTINCT, "no_match", 6, score, None, ev)


# ------------------------------------------------------------------ database

_DECISION_RANK = {MERGE: 2, REVIEW: 1, DISTINCT: 0}


def record_from_row(row) -> Record:
    """Build a `Record` from a `company` row. Uses the *resolved* name only."""
    return Record(
        name=row["canonical_name"],
        ch_number=row["companies_house_no"],
        domain=row["domain"],
        country_iso2=row["country_iso2"],
        company_id=row["id"],
        first_seen=row["first_seen"],
    )


def candidates(db, record: Record, *, exclude_id: str | None = None) -> list[Record]:
    """Blocking: the small set of live companies worth comparing against.

    Deterministic keys (tiers 0-2) may come from the `identifier` table, which
    is how a rename still resolves. **Fuzzy blocking never does** — it looks
    only at live companies' own `norm_key`, so an alias absorbed by an earlier
    merge can never pull a third record into the cluster (05-pipeline §4.2, the
    transitive-chain guard).
    """
    ids: list[str] = []
    seen: set[str] = set()

    def add(rows: Iterable[Any], col: str = "company_id") -> None:
        for r in rows:
            cid = r[col]
            if cid and cid not in seen:
                seen.add(cid)
                ids.append(cid)

    if record.ch:
        add(db.query("SELECT company_id FROM identifier WHERE kind='ch' AND value=?",
                     (record.ch,)))
        add(db.query("SELECT id AS company_id FROM company WHERE companies_house_no=?",
                     (record.ch,)))
    if record.dom:
        add(db.query("SELECT company_id FROM identifier WHERE kind='domain' AND value=?",
                     (record.dom,)))
        add(db.query("SELECT id AS company_id FROM company WHERE domain=?", (record.dom,)))
    if record.norm_key and not is_placeholder_name(record.name, db):
        add(db.query(
            "SELECT company_id FROM identifier WHERE kind IN ('norm_key','alias') AND value=?",
            (record.norm_key,)))
        add(db.query("SELECT id AS company_id FROM company WHERE norm_key=?",
                     (record.norm_key,)))
        for token in sorted(rare_tokens(record.name)):
            add(db.query(
                "SELECT id AS company_id FROM company "
                "WHERE merged_into IS NULL AND norm_key LIKE ? LIMIT 200",
                (f"%{token}%",)))

    out: list[Record] = []
    for cid in ids:
        canonical = db.resolve_company_id(cid)
        if canonical == exclude_id:
            continue
        row = db.one("SELECT * FROM company WHERE id = ?", (canonical,))
        if row is None or row["merged_into"] is not None:
            continue
        cand = record_from_row(row)
        if all(c.company_id != cand.company_id for c in out):
            out.append(cand)
    return out


def fuzzy_cluster_size(db, company_id: str) -> int:
    """How many records a canonical company has absorbed by fuzzy match."""
    n = db.scalar(
        "SELECT COUNT(*) FROM merge_event WHERE winner_id = ? AND rule LIKE 'fuzzy%'",
        (company_id,),
    ) or 0
    n += db.scalar(
        "SELECT COUNT(*) FROM identifier WHERE company_id = ? AND kind = 'alias' "
        "AND source_key = 'fuzzy'",
        (company_id,),
    ) or 0
    return int(n) + 1  # the company itself is a member


def find_match(db, record: Record, *, exclude_id: str | None = None
               ) -> tuple[Record | None, MatchResult]:
    """Best live company for this record, with the ladder's verdict.

    Returns `(None, distinct)` when nothing is close enough to act on.
    """
    best: tuple[Record, MatchResult] | None = None
    for cand in candidates(db, record, exclude_id=exclude_id):
        result = compare(record, cand)
        if result.decision == DISTINCT:
            continue
        if result.rule.startswith("fuzzy") and result.decision == MERGE:
            if fuzzy_cluster_size(db, cand.company_id or "") >= MAX_FUZZY_CLUSTER:
                result = MatchResult(
                    REVIEW, "fuzzy_cluster_cap", result.tier, result.score, None,
                    {**result.evidence, "reason": f"cluster already {MAX_FUZZY_CLUSTER}"},
                )
        key = (_DECISION_RANK[result.decision], result.confidence or 0.0,
               result.score or 0.0)
        if best is None:
            best = (cand, result)
        else:
            best_key = (_DECISION_RANK[best[1].decision], best[1].confidence or 0.0,
                        best[1].score or 0.0)
            if key > best_key:
                best = (cand, result)

    if best is None:
        return None, MatchResult(DISTINCT, "no_match", 6, None, None,
                                 {"a_name": record.name})
    return best


def ladder(records: Sequence[Record]) -> list[tuple[int, int, MatchResult]]:
    """Every pairwise verdict in a small set. Used by tests and `review`."""
    out = []
    for i in range(len(records)):
        for j in range(i + 1, len(records)):
            out.append((i, j, compare(records[i], records[j])))
    return out
