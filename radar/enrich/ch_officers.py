"""Companies House officers, PSC and prior appointments — with the privacy
filter applied **at ingest**.

The officers endpoint hands back partial dates of birth, correspondence
addresses, nationality and country of residence. None of that is ever allowed
near the database: it is dropped *here, in the adapter*, not hidden at render
time (03-data-model §2, 05-pipeline ⑤).

The mechanism is structural rather than a promise. `Founder` and `PscHolder`
are `slots=True` dataclasses whose field lists contain no forbidden name, so
`hasattr(founder, "date_of_birth")` is False by construction — there is no
attribute to leak, no dict to accidentally `**`-splat into an INSERT.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

log = logging.getLogger(__name__)

CH_API_BASE = "https://api.company-information.service.gov.uk"
CH_PROFILE_URL = "https://find-and-update.company-information.service.gov.uk/company/{}"

#: 03-data-model §2. Enforced on the table by `test_schema_privacy`, and on the
#: in-memory records by `test_ch_officer_ingest_drops_dob_and_address`.
FORBIDDEN_FIELDS: frozenset[str] = frozenset({
    "email", "phone", "address", "postcode", "date_of_birth",
    "dob_month", "dob_year", "nationality", "country_of_residence",
})

#: Everything Companies House sends that we refuse to carry, including the
#: aliases it uses on the wire.
_PERSONAL_KEYS: frozenset[str] = FORBIDDEN_FIELDS | {
    "principal_office_address", "service_address", "usual_residential_address",
    "date_of_birth", "premises", "address_line_1", "address_line_2",
    "postal_code", "care_of", "po_box", "occupation",
}

#: Officer roles that are a corporate body, not a person. A company whose only
#: officer is one of these is a formation-agent shell (04-sources §3.4 #6).
CORPORATE_ROLES: frozenset[str] = frozenset({
    "corporate-director", "corporate-secretary", "corporate-nominee-director",
    "corporate-nominee-secretary", "corporate-llp-designated-member",
    "corporate-llp-member", "corporate-managing-officer",
    "corporate-member-of-a-management-organ",
    "corporate-member-of-a-supervisory-organ",
    "corporate-member-of-an-administrative-organ",
})

SECRETARY_ROLES: frozenset[str] = frozenset({
    "secretary", "corporate-secretary", "nominee-secretary", "corporate-nominee-secretary",
})

#: Roles that can plausibly be a founder.
FOUNDER_ROLES: frozenset[str] = frozenset({
    "director", "llp-designated-member", "llp-member", "member-of-a-management-organ",
    "managing-officer", "judicial-factor",
})

_OFFICER_ID_RE = re.compile(r"/officers/([^/]+)/appointments")


# ------------------------------------------------------------------ records


@dataclass(frozen=True, slots=True)
class Founder:
    """A person, minimally. There is deliberately nowhere to put a DOB."""

    name: str
    norm_name: str
    role: str | None = None
    appointed_on: str | None = None
    officer_id: str | None = None
    appointments_url: str | None = None
    profile_url: str | None = None
    is_psc: bool = False
    is_corporate: bool = False
    resigned: bool = False
    prior_appointments: int | None = None
    source_url: str = ""


@dataclass(frozen=True, slots=True)
class PscHolder:
    """A person with significant control. Same privacy rules as `Founder`."""

    name: str
    norm_name: str
    notified_on: str | None = None
    natures_of_control: tuple[str, ...] = ()
    is_corporate: bool = False
    ceased: bool = False


# ------------------------------------------------------------------ helpers


def scrub(record: Mapping[str, Any]) -> dict:
    """Strip every personal field from a raw CH record.

    Used before anything is logged or quarantined, so a debugging aid can never
    become the leak.
    """
    return {k: v for k, v in dict(record).items() if k not in _PERSONAL_KEYS}


def norm_person(name: str) -> str:
    """Normalised person name — the `founder.norm_name` uniqueness key."""
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _titlecase(part: str) -> str:
    """`'SMITH'` → `'Smith'`, `"O'BRIEN"` → `"O'Brien"`, `'DE LA CRUZ'` → `'De La Cruz'`.

    ponytail: Companies House upper-cases surnames and gives no cased original,
    so `MCDONALD` becomes `Mcdonald`. Cosmetic only — `norm_name` is unaffected,
    so it never causes a duplicate founder.
    """
    if not part:
        return ""
    if part != part.upper():
        return part  # already mixed case; leave it alone
    return re.sub(r"[A-Za-z']+", lambda m: m.group(0).capitalize(), part.lower().title())


def format_officer_name(raw_name: str, name_elements: Mapping[str, Any] | None = None) -> str:
    """`'SMITH, Jane Elizabeth'` → `'Jane Elizabeth Smith'`."""
    if name_elements:
        parts = [
            name_elements.get("forename"),
            name_elements.get("other_forenames"),
            name_elements.get("middle_name"),
            name_elements.get("surname"),
        ]
        joined = " ".join(_titlecase(str(p)) for p in parts if p)
        if joined.strip():
            return re.sub(r"\s+", " ", joined).strip()

    name = (raw_name or "").strip()
    if "," in name:
        surname, _, rest = name.partition(",")
        rest = rest.strip()
        surname = _titlecase(surname.strip())
        return re.sub(r"\s+", " ", f"{rest} {surname}").strip()
    return re.sub(r"\s+", " ", name)


def officer_id_from(links: Mapping[str, Any] | None) -> tuple[str | None, str | None]:
    """Pull `(officer_id, appointments_path)` out of `links.officer.appointments`."""
    if not links:
        return None, None
    officer = links.get("officer")
    path: str | None = None
    if isinstance(officer, Mapping):
        path = officer.get("appointments")
    elif isinstance(officer, str):
        path = officer
    if not path:
        return None, None
    match = _OFFICER_ID_RE.search(str(path))
    return (match.group(1) if match else None), str(path)


# ------------------------------------------------------------------ parsing


def parse_officers(raw: Mapping[str, Any], *, source_url: str = "") -> list[Founder]:
    """Officers endpoint → `Founder[]`, personal data dropped on the way through.

    Returns *every* officer, corporate ones included and flagged, because
    "the only officer is a corporate secretary" is a filter that needs to see
    them (04-sources §3.4 #6).
    """
    out: list[Founder] = []
    for item in (raw or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        role = (item.get("officer_role") or "").strip().lower() or None
        is_corporate = bool(role and role in CORPORATE_ROLES) or bool(item.get("identification"))
        name = format_officer_name(item.get("name") or "", item.get("name_elements"))
        if not name:
            continue
        officer_id, appointments = officer_id_from(item.get("links"))
        out.append(
            Founder(
                name=name,
                norm_name=norm_person(name),
                role=role,
                appointed_on=item.get("appointed_on") or None,
                officer_id=officer_id,
                appointments_url=appointments,
                is_corporate=is_corporate,
                resigned=bool(item.get("resigned_on")),
                source_url=source_url,
            )
        )
    return out


def parse_psc(raw: Mapping[str, Any]) -> list[PscHolder]:
    """PSC endpoint → `PscHolder[]`, personal data dropped on the way through."""
    out: list[PscHolder] = []
    for item in (raw or {}).get("items") or []:
        if not isinstance(item, Mapping):
            continue
        kind = (item.get("kind") or "").lower()
        if "super-secure" in kind:
            continue  # a protected PSC; CH gives no name at all
        is_corporate = "individual" not in kind and "legal-person" not in kind
        name = format_officer_name(item.get("name") or "", item.get("name_elements"))
        if not name:
            continue
        controls = tuple(str(c) for c in (item.get("natures_of_control") or []))
        out.append(
            PscHolder(
                name=name,
                norm_name=norm_person(name),
                notified_on=item.get("notified_on") or None,
                natures_of_control=controls,
                is_corporate=is_corporate,
                ceased=bool(item.get("ceased_on") or item.get("ceased")),
            )
        )
    return out


def parse_appointment_count(raw: Mapping[str, Any]) -> int:
    """`/officers/{id}/appointments` → how many *other* companies this person runs.

    `total_results` includes the appointment we already know about, so subtract
    one. A repeat founder is `prior_appointments >= 1` (04-sources §3.4 #7).
    """
    total = (raw or {}).get("total_results")
    if total is None:
        total = len((raw or {}).get("items") or [])
    return max(int(total) - 1, 0)


# ------------------------------------------------------------------- rules


def founder_candidates(officers: Sequence[Founder]) -> list[Founder]:
    """Serving natural-person officers who could be founders."""
    return [
        o for o in officers
        if not o.is_corporate and not o.resigned
        and (o.role is None or o.role in FOUNDER_ROLES)
    ]


def only_corporate_secretary(officers: Sequence[Founder]) -> bool:
    """True when the register shows nothing but a corporate secretary.

    This check *cannot* run before the officers call — which is precisely why
    it sits in pass 2 of the noise filter, not in the free pass.
    """
    serving = [o for o in officers if not o.resigned]
    if not serving:
        return False
    return all(o.is_corporate and (o.role in SECRETARY_ROLES) for o in serving)


def apply_psc(officers: Sequence[Founder], pscs: Sequence[PscHolder]) -> list[Founder]:
    """Mark which officers actually control the company."""
    controlling = {p.norm_name for p in pscs if not p.is_corporate and not p.ceased}
    return [
        replace(o, is_psc=True) if o.norm_name in controlling else o
        for o in officers
    ]


def psc_only_founders(pscs: Sequence[PscHolder], *, source_url: str = "") -> list[Founder]:
    """PSCs who never appear on the officers list are still founders."""
    return [
        Founder(
            name=p.name,
            norm_name=p.norm_name,
            role=None,
            appointed_on=p.notified_on,
            is_psc=True,
            source_url=source_url,
        )
        for p in pscs
        if not p.is_corporate and not p.ceased
    ]


def merge_founders(
    officers: Sequence[Founder], pscs: Sequence[PscHolder], *, source_url: str = ""
) -> list[Founder]:
    """Officers ∪ PSCs, de-duplicated on `norm_name`, PSC flags applied.

    Founders are a **set-valued** field: unioned, never resolved. Losing a
    founder because one endpoint didn't mention them is a real bug
    (03-data-model §2).
    """
    merged = apply_psc(founder_candidates(officers), pscs)
    known = {f.norm_name for f in merged}
    for extra in psc_only_founders(pscs, source_url=source_url):
        if extra.norm_name not in known:
            known.add(extra.norm_name)
            merged.append(extra)
    return merged


# ------------------------------------------------------------------ fetches


def _get(http: Any, url: str, api_key: str) -> Any:
    resp = http.get(url, auth=(api_key, ""), check_robots=False)
    if resp.status == 404:
        return None
    if not resp.ok:
        log.warning("companies_house: HTTP %s for %s", resp.status, url)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("companies_house: unparseable body for %s", url)
        return None


def fetch_officers(http: Any, number: str, *, api_key: str, base_url: str = CH_API_BASE) -> Any:
    return _get(http, f"{base_url.rstrip('/')}/company/{number}/officers", api_key)


def fetch_psc(http: Any, number: str, *, api_key: str, base_url: str = CH_API_BASE) -> Any:
    return _get(
        http,
        f"{base_url.rstrip('/')}/company/{number}/persons-with-significant-control",
        api_key,
    )


def fetch_appointments(
    http: Any, officer_id: str, *, api_key: str, base_url: str = CH_API_BASE
) -> Any:
    return _get(http, f"{base_url.rstrip('/')}/officers/{officer_id}/appointments", api_key)


# ------------------------------------------------------------------ storage


def store_founders(
    db: Any,
    company_id: str,
    founders: Iterable[Founder],
    *,
    source_url: str,
    now: str | None = None,
) -> int:
    """Upsert founders. Idempotent on `(company_id, norm_name)`.

    Only the columns that exist on the table are ever named, and the table has
    no forbidden column — so this INSERT is incapable of storing a DOB.
    """
    from radar.store.db import now_iso

    stamp = now or now_iso()
    written = 0
    for f in founders:
        if not f.norm_name:
            continue
        db.execute(
            """INSERT INTO founder
                 (company_id, name, norm_name, role, profile_url, is_psc,
                  appointed_on, prior_appointments, source_url, first_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(company_id, norm_name) DO UPDATE SET
                 name               = excluded.name,
                 role               = COALESCE(excluded.role, founder.role),
                 profile_url        = COALESCE(excluded.profile_url, founder.profile_url),
                 is_psc             = MAX(founder.is_psc, excluded.is_psc),
                 appointed_on       = COALESCE(excluded.appointed_on, founder.appointed_on),
                 prior_appointments = COALESCE(excluded.prior_appointments,
                                               founder.prior_appointments)""",
            (
                company_id, f.name, f.norm_name, f.role, f.profile_url,
                1 if f.is_psc else 0, f.appointed_on, f.prior_appointments,
                f.source_url or source_url, stamp,
            ),
        )
        written += 1
    return written
