"""
team_tiers.py — team-tier assignment for Lane4 Data Harvester.

Reads output/team_scores.csv and produces three output files:
  output/team_tiers.csv         — full per-team tier rows
  output/team_tier_summary.csv  — one row per conference+gender table
  output/team_tier_qa.csv       — validation warnings and skipped tables

Tier framework (locked)
-----------------------
  champion_score     = highest score in the conference+gender table
  total_meet_points  = sum of all scores in the table
  champion_ratio     = team_score / champion_score
  meet_share         = team_score / total_meet_points

  Base tier from champion_ratio:
    Tier 1 : champion_ratio >= 0.85
    Tier 2 : 0.60 <= champion_ratio < 0.85
    Tier 3 : 0.35 <= champion_ratio < 0.60
    Tier 4 : champion_ratio < 0.35

  Tier 1 split by meet_share:
    Tier 1A (Super Powerhouse) : meet_share >= 0.24
    Tier 1B (Powerhouse)       : meet_share <  0.24

Hardening
---------
  * Input validation per table before tiering
  * Conservative name normalization (whitespace only)
  * Duplicate team detection and safe resolution
  * Deterministic sort: conference → gender → rank asc → score desc → team
  * Graceful failure: skip malformed tables, record in QA file
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Output schemas
# ─────────────────────────────────────────────────────────────────────────────

TIERS_HEADER = [
    "bundle_id", "conference", "gender",
    "rank", "team", "score",
    "champion_score", "total_meet_points",
    "champion_ratio", "meet_share",
    "base_tier", "power_class", "final_tier",
]

SUMMARY_HEADER = [
    "bundle_id", "conference", "gender",
    "number_of_teams", "winner",
    "champion_score", "total_meet_points",
    "tier_1a_count", "tier_1b_count",
    "tier_2_count", "tier_3_count", "tier_4_count",
]

QA_HEADER = [
    "bundle_id", "conference", "gender",
    "issue_type", "team", "detail",
]

# ─────────────────────────────────────────────────────────────────────────────
# Tier thresholds (locked)
# ─────────────────────────────────────────────────────────────────────────────

TIER1_MIN_RATIO     = 0.85
TIER2_MIN_RATIO     = 0.60
TIER3_MIN_RATIO     = 0.35
TIER1A_MIN_SHARE    = 0.24   # within Tier 1: 1A if meet_share >= this

# ─────────────────────────────────────────────────────────────────────────────
# Name normalization (conservative)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """
    Return a stable comparison key for team-name deduplication.
    Conservative: only collapse internal whitespace and lowercase.
    No abbreviation expansion, no suffix stripping.
    """
    return re.sub(r'\s+', ' ', name.strip()).lower()

# ─────────────────────────────────────────────────────────────────────────────
# Input parsing + validation
# ─────────────────────────────────────────────────────────────────────────────

def _parse_score(raw: str) -> Optional[float]:
    """Return float or None if unparseable."""
    s = raw.strip().replace(',', '')
    try:
        v = float(s)
        return v
    except ValueError:
        return None


def _parse_rank(raw: str) -> Optional[int]:
    """Return int or None if unparseable."""
    s = raw.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def _load_scores(scores_path: Path) -> list[dict]:
    """Read team_scores.csv and return list of raw row dicts."""
    with open(scores_path, newline="") as f:
        return list(csv.DictReader(f))


def _group_tables(raw_rows: list[dict]) -> dict[tuple, list[dict]]:
    """Group rows by (bundle_id, conference, gender) → list of rows."""
    tables: dict[tuple, list[dict]] = defaultdict(list)
    for r in raw_rows:
        key = (r["bundle_id"], r["conference"], r["gender_or_division"])
        tables[key].append(r)
    return dict(tables)

# ─────────────────────────────────────────────────────────────────────────────
# Per-table validation
# ─────────────────────────────────────────────────────────────────────────────

def _validate_table(
    key: tuple,
    rows: list[dict],
) -> tuple[list[dict], list[dict]]:
    """
    Validate and clean one conference+gender table.

    Returns (clean_rows, qa_issues).
    qa_issues is a list of QA row dicts.
    If the table cannot be used at all, clean_rows is [].
    """
    bundle_id, conference, gender = key
    qa: list[dict] = []

    def _qa(issue_type: str, team: str = "", detail: str = "") -> dict:
        return {
            "bundle_id":  bundle_id,
            "conference": conference,
            "gender":     gender,
            "issue_type": issue_type,
            "team":       team,
            "detail":     detail,
        }

    # ── 1. score parseability ─────────────────────────────────────────────
    valid: list[dict] = []
    for r in rows:
        score = _parse_score(r.get("score", ""))
        if score is None:
            qa.append(_qa("malformed_score", r.get("team", ""), f"raw={r.get('score','')!r}"))
            continue
        if score < 0:
            qa.append(_qa("negative_score", r.get("team", ""), f"score={score}"))
            continue
        valid.append({**r, "_score_f": score})

    if not valid:
        qa.append(_qa("invalid_table", detail="no_parseable_rows"))
        return [], qa

    # ── 2. rank parseability + missing-rank warning ────────────────────────
    for r in valid:
        rk = _parse_rank(r.get("rank", ""))
        if rk is None:
            qa.append(_qa("missing_rank", r.get("team", ""), f"raw={r.get('rank','')!r}"))
        r["_rank_i"] = rk  # may be None

    # ── 3. duplicate team detection ───────────────────────────────────────
    by_norm: dict[str, list[dict]] = defaultdict(list)
    for r in valid:
        key_name = _normalize_name(r.get("team", ""))
        by_norm[key_name].append(r)

    deduped: list[dict] = []
    for norm_key, grp in by_norm.items():
        if len(grp) == 1:
            deduped.append(grp[0])
            continue

        # Multiple rows for the same (normalized) team name
        scores_in_grp = [g["_score_f"] for g in grp]
        all_same_score = len(set(scores_in_grp)) == 1
        all_same_rank  = len({g.get("rank","") for g in grp}) == 1

        if all_same_score and all_same_rank:
            # Clearly identical rows — keep one, log as info
            qa.append(_qa(
                "duplicate_team_row",
                grp[0].get("team", ""),
                f"n={len(grp)} identical rows — kept one",
            ))
            deduped.append(grp[0])
        else:
            # Ambiguous duplicates — keep highest-score row, warn
            best = max(grp, key=lambda g: g["_score_f"])
            qa.append(_qa(
                "duplicate_team_row",
                grp[0].get("team", ""),
                (
                    f"n={len(grp)} rows with differing scores "
                    f"{scores_in_grp} — kept highest ({best['_score_f']})"
                ),
            ))
            deduped.append(best)

    # ── 4. rank uniqueness check (after dedup) ────────────────────────────
    seen_ranks: dict[int, str] = {}
    for r in deduped:
        rk = r.get("_rank_i")
        if rk is None:
            continue
        if rk in seen_ranks:
            qa.append(_qa(
                "duplicate_rank",
                r.get("team", ""),
                f"rank={rk} also assigned to {seen_ranks[rk]}",
            ))
        else:
            seen_ranks[rk] = r.get("team", "")

    # ── 5. need at least one team with score > 0 ──────────────────────────
    positive_scores = [r["_score_f"] for r in deduped if r["_score_f"] > 0]
    if not positive_scores:
        qa.append(_qa("invalid_table", detail="all_scores_zero_or_negative"))
        return [], qa

    return deduped, qa

# ─────────────────────────────────────────────────────────────────────────────
# Tier assignment
# ─────────────────────────────────────────────────────────────────────────────

def _assign_tiers(rows: list[dict]) -> list[dict]:
    """
    Given clean rows (with _score_f), assign tier columns.
    Mutates in place and returns rows.
    """
    champion_score    = max(r["_score_f"] for r in rows)
    total_meet_points = sum(r["_score_f"] for r in rows)

    for r in rows:
        s = r["_score_f"]
        champion_ratio = round(s / champion_score, 6)    if champion_score else 0.0
        meet_share     = round(s / total_meet_points, 6) if total_meet_points else 0.0

        if champion_ratio >= TIER1_MIN_RATIO:
            base_tier = "Tier 1"
            if meet_share >= TIER1A_MIN_SHARE:
                power_class = "Super Powerhouse"
                final_tier  = "Tier 1A"
            else:
                power_class = "Powerhouse"
                final_tier  = "Tier 1B"
        elif champion_ratio >= TIER2_MIN_RATIO:
            base_tier   = "Tier 2"
            power_class = ""
            final_tier  = "Tier 2"
        elif champion_ratio >= TIER3_MIN_RATIO:
            base_tier   = "Tier 3"
            power_class = ""
            final_tier  = "Tier 3"
        else:
            base_tier   = "Tier 4"
            power_class = ""
            final_tier  = "Tier 4"

        r["champion_score"]    = champion_score
        r["total_meet_points"] = total_meet_points
        r["champion_ratio"]    = champion_ratio
        r["meet_share"]        = meet_share
        r["base_tier"]         = base_tier
        r["power_class"]       = power_class
        r["final_tier"]        = final_tier

    return rows

# ─────────────────────────────────────────────────────────────────────────────
# Deterministic sort
# ─────────────────────────────────────────────────────────────────────────────

def _sort_key(r: dict) -> tuple:
    """
    conference → gender → rank asc (None last) → score desc → team asc
    """
    rk = r.get("_rank_i")
    rank_sort = rk if rk is not None else 9999
    return (
        r.get("conference", ""),
        r.get("gender_or_division", r.get("gender", "")),
        rank_sort,
        -r.get("_score_f", 0.0),
        r.get("team", ""),
    )

# ─────────────────────────────────────────────────────────────────────────────
# Build output rows
# ─────────────────────────────────────────────────────────────────────────────

def _build_tier_row(src: dict) -> dict:
    """Convert an internal row (with _* keys) to a TIERS_HEADER output row."""
    return {
        "bundle_id":          src.get("bundle_id", ""),
        "conference":         src.get("conference", ""),
        "gender":             src.get("gender_or_division", src.get("gender", "")),
        "rank":               src.get("rank", ""),
        "team":               src.get("team", "").strip(),
        "score":              src["_score_f"],
        "champion_score":     src["champion_score"],
        "total_meet_points":  src["total_meet_points"],
        "champion_ratio":     src["champion_ratio"],
        "meet_share":         src["meet_share"],
        "base_tier":          src["base_tier"],
        "power_class":        src["power_class"],
        "final_tier":         src["final_tier"],
    }


def _build_summary_row(key: tuple, rows: list[dict]) -> dict:
    """Build a SUMMARY_HEADER row for one conference+gender table."""
    bundle_id, conference, gender = key

    # Winner = rank-1 team (or highest score if ranks missing)
    r1 = [r for r in rows if r.get("_rank_i") == 1]
    if r1:
        winner = r1[0].get("team", "").strip()
    else:
        winner = max(rows, key=lambda r: r["_score_f"]).get("team", "").strip()

    champion_score    = rows[0]["champion_score"]
    total_meet_points = rows[0]["total_meet_points"]

    counts: dict[str, int] = {"Tier 1A": 0, "Tier 1B": 0, "Tier 2": 0, "Tier 3": 0, "Tier 4": 0}
    for r in rows:
        ft = r.get("final_tier", "")
        if ft in counts:
            counts[ft] += 1

    return {
        "bundle_id":          bundle_id,
        "conference":         conference,
        "gender":             gender,
        "number_of_teams":    len(rows),
        "winner":             winner,
        "champion_score":     champion_score,
        "total_meet_points":  total_meet_points,
        "tier_1a_count":      counts["Tier 1A"],
        "tier_1b_count":      counts["Tier 1B"],
        "tier_2_count":       counts["Tier 2"],
        "tier_3_count":       counts["Tier 3"],
        "tier_4_count":       counts["Tier 4"],
    }

# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_team_tiers(
    scores_path: Path,
    output_dir: Path,
) -> dict:
    """
    Read team_scores.csv, assign tiers, write three output CSVs.

    Parameters
    ----------
    scores_path : Path
        Path to output/team_scores.csv
    output_dir : Path
        Directory for output files (will be created if missing)

    Returns
    -------
    dict with summary statistics for caller display
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    raw_rows = _load_scores(scores_path)
    tables   = _group_tables(raw_rows)

    # Sort table keys for deterministic processing order
    sorted_keys = sorted(tables.keys())

    all_tier_rows:    list[dict] = []
    all_summary_rows: list[dict] = []
    all_qa_rows:      list[dict] = []
    skipped_tables:   list[tuple] = []
    tier1a_teams:     list[dict] = []
    tables_tiered:    int = 0

    for key in sorted_keys:
        bundle_id, conference, gender = key
        raw = tables[key]

        # Validate
        clean, qa_issues = _validate_table(key, raw)
        all_qa_rows.extend(qa_issues)

        if not clean:
            # Table is malformed — record skip and move on
            all_qa_rows.append({
                "bundle_id":  bundle_id,
                "conference": conference,
                "gender":     gender,
                "issue_type": "skipped_table",
                "team":       "",
                "detail":     "table_failed_validation",
            })
            skipped_tables.append(key)
            continue

        # Assign tiers
        clean = _assign_tiers(clean)

        # Sort deterministically
        clean.sort(key=_sort_key)

        # Build output rows
        tier_rows    = [_build_tier_row(r) for r in clean]
        summary_row  = _build_summary_row(key, clean)

        all_tier_rows.extend(tier_rows)
        all_summary_rows.append(summary_row)
        tables_tiered += 1

        # Collect Tier 1A for return summary
        for r in clean:
            if r.get("final_tier") == "Tier 1A":
                tier1a_teams.append({
                    "bundle_id":   bundle_id,
                    "conference":  conference,
                    "gender":      gender,
                    "team":        r.get("team", "").strip(),
                    "score":       r["_score_f"],
                    "meet_share":  r["meet_share"],
                    "final_tier":  r["final_tier"],
                })

    # ── Write CSVs ──────────────────────────────────────────────────────────
    tiers_path   = output_dir / "team_tiers.csv"
    summary_path = output_dir / "team_tier_summary.csv"
    qa_path      = output_dir / "team_tier_qa.csv"

    with open(tiers_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TIERS_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_tier_rows)

    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SUMMARY_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_summary_rows)

    with open(qa_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=QA_HEADER, extrasaction="ignore")
        w.writeheader()
        w.writerows(all_qa_rows)

    # ── Console summary ─────────────────────────────────────────────────────
    print(f"  [team_tiers] Tables processed:  {tables_tiered} / {len(sorted_keys)}")
    print(f"  [team_tiers] Tables skipped:    {len(skipped_tables)}")
    print(f"  [team_tiers] Teams tiered:      {len(all_tier_rows)}")
    print(f"  [team_tiers] QA issues logged:  {len(all_qa_rows)}")
    print(f"  [team_tiers] Wrote {tiers_path.name}  ({len(all_tier_rows)} rows)")
    print(f"  [team_tiers] Wrote {summary_path.name}")
    print(f"  [team_tiers] Wrote {qa_path.name}")

    return {
        "tables_tiered":   tables_tiered,
        "tables_skipped":  len(skipped_tables),
        "skipped_keys":    skipped_tables,
        "total_teams":     len(all_tier_rows),
        "qa_issues":       len(all_qa_rows),
        "tier1a_teams":    tier1a_teams,
        "summary_rows":    all_summary_rows,
    }
