"""Competitors from the benchmark: the sites in your cohort (same country and category) that
score just above you, plus the cohort leader, with where they are ahead.

Pure functions (no database): `rows` are the latest scores per domain (store.latest_scores).
Overall is recomputed for everyone from the same four scores (PSA, SEO, GEO, AI), so a site
measured by psa.fajaede.nl (no security score) is compared like with like."""

from __future__ import annotations

from typing import Any

from backend.benchmark import rules

METRICS = {"psa": "psa_score", "seo": "seo_score", "geo": "geo_score", "ai": "ai_visibility_score"}
LABELS = {"psa": "PSA (privacy en security)", "seo": "SEO", "geo": "GEO (lokale vindbaarheid)", "ai": "AI-zichtbaarheid"}
AHEAD_POINTS = 10       # a lead of at least this many points counts as a strength
MIN_GROUP = 5           # a cohort needs a few sites before naming competitors makes sense


def overall(scores: dict[str, float | None]) -> int | None:
    """Trust (PSA) 50% and visibility (average of SEO, GEO, AI) 50%, re-weighted when missing."""
    visibility = [scores[k] for k in ("seo", "geo", "ai") if scores.get(k) is not None]
    parts = [(scores.get("psa"), 50), (sum(visibility) / len(visibility) if visibility else None, 50)]
    present = [(value, weight) for value, weight in parts if value is not None]
    total = sum(weight for _, weight in present)
    return round(sum(value * weight for value, weight in present) / total) if total else None


def _row_scores(row: Any) -> dict[str, float | None]:
    return {param: row[column] for param, column in METRICS.items()}


def _strengths(theirs: dict[str, float | None], yours: dict[str, float | None]) -> list[dict[str, Any]]:
    found = []
    for param in METRICS:
        a, b = theirs.get(param), yours.get(param)
        if a is not None and b is not None and a - b >= AHEAD_POINTS:
            found.append({"metric": param, "label": LABELS[param], "theirs": round(a), "yours": round(b),
                          "difference": round(a - b)})
    return sorted(found, key=lambda s: -s["difference"])


def _entry(row: Any, yours: dict[str, float | None]) -> dict[str, Any]:
    scores = _row_scores(row)
    return {"domain": row["root_domain"], "overall": overall(scores),
            "scores": {k: (round(v) if v is not None else None) for k, v in scores.items()},
            "ahead_on": _strengths(scores, yours)}


def find_competitors(rows: list[Any], domain: str, scores: dict[str, float], category: str | None = None,
                     limit: int = 3) -> dict[str, Any]:
    """Cohort = the most specific of country+category, category, country, all with at least MIN_GROUP
    sites. Returns the `limit` sites just above you (closest first), the leader and the one just below."""
    known = next((row for row in rows if row["root_domain"] == domain), None)
    country = known["country"] if known else rules.country_of(domain)
    cat = known["category"] if known else (category if category in rules.CATEGORIES else None)
    candidates = rules.cohorts(country, cat) if cat else [f"{country}:*", "*:*"]
    yours = {param: scores.get(param) for param in METRICS}
    your_overall = overall(yours)

    for cohort in candidates:
        group_country, group_category = cohort.split(":", 1)
        group = [row for row in rows if row["root_domain"] != domain
                 and (group_country == "*" or row["country"] == group_country)
                 and (group_category == "*" or row["category"] == group_category)
                 and overall(_row_scores(row)) is not None]
        if len(group) >= MIN_GROUP:
            break
    else:
        return {"domain": domain, "cohort": None, "your_overall": your_overall, "ahead": [], "leader": None,
                "behind": None, "group_size": 0}

    ranked = sorted(group, key=lambda row: (overall(_row_scores(row)), row["root_domain"]))
    if your_overall is None:
        ahead_rows, behind_row = [], None
    else:
        ahead_rows = [row for row in ranked if overall(_row_scores(row)) > your_overall][:limit]
        below = [row for row in ranked if overall(_row_scores(row)) <= your_overall]
        behind_row = below[-1] if below else None
    leader_row = ranked[-1]
    leader = _entry(leader_row, yours)
    ahead = [_entry(row, yours) for row in ahead_rows]
    return {
        "domain": domain,
        "cohort": cohort,
        "group_size": len(group),
        "your_overall": your_overall,
        "ahead": ahead,
        # the leader separately, unless it is already in the "just above you" list
        "leader": None if any(e["domain"] == leader["domain"] for e in ahead) else leader,
        "behind": _entry(behind_row, yours) if behind_row is not None else None,
        "you_lead": your_overall is not None and not ahead_rows,
    }
