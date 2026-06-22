#!/usr/bin/env python3
"""
generate_csvs.py
----------------
Reads the raw AP PTM survey CSV and produces two output files:

  1. school_aggregate.csv  — School-level aggregate stats
     Columns:
       school_name, district, school_code, dist_code,
       total_feedback, rating_1, rating_2, rating_3, rating_4, rating_5,
       total_rated, reasons_with_text

  2. filtered_with_reasons.csv  — Only rows where rating_reason is not null/empty
     Same columns as the raw CSV:
       id, session_id, user_id, dist_code, district,
       school_code, school_name, category, rating,
       rating_reason, original_message, translated_message, processed_at

Usage:
    python generate_csvs.py [input_csv] [output_dir]

Defaults:
    input_csv  = ./qualitative_analysis/promts/raw.csv
    output_dir = ./qualitative_analysis/promts/
"""

import sys
import os
import csv
from collections import defaultdict
from typing import Optional

# ── Configuration ───────────────────────────────────────────────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(SCRIPT_DIR, "promts", "raw.csv")
DEFAULT_OUTPUT_DIR = os.path.join(SCRIPT_DIR, "promts")

RAW_COLS = [
    "id", "session_id", "user_id", "dist_code", "district",
    "school_code", "school_name", "category", "rating",
    "rating_reason", "original_message", "translated_message", "processed_at",
]

AGG_COLS = [
    "school_name", "district", "school_code", "dist_code",
    "total_feedback",
    "rating_1", "rating_2", "rating_3", "rating_4", "rating_5",
    "total_rated",
    "reasons_with_text",
]

# ── Helpers ──────────────────────────────────────────────────────────────────

def is_meaningful_reason(text: str) -> bool:
    """Return True if rating_reason contains non-empty, non-trivial text."""
    if not text:
        return False
    stripped = text.strip()
    return bool(stripped)


def safe_int(val: str) -> Optional[int]:
    """Try to parse val as int; return None on failure."""
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return None


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    input_csv = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT
    output_dir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUTPUT_DIR

    os.makedirs(output_dir, exist_ok=True)

    out_aggregate = os.path.join(output_dir, "school_aggregate.csv")
    out_filtered  = os.path.join(output_dir, "filtered_with_reasons.csv")

    print(f"Reading: {input_csv}")

    # ── Data structures for aggregation ──────────────────────────────────
    # Key: (school_code, school_name, district, dist_code)
    school_meta: dict[tuple, dict] = {}   # stores first-seen meta
    school_stats: dict[tuple, dict] = defaultdict(lambda: {
        "total_feedback": 0,
        "rating_1": 0, "rating_2": 0, "rating_3": 0,
        "rating_4": 0, "rating_5": 0,
        "total_rated": 0,
        "reasons_with_text": 0,
    })

    filtered_rows: list[dict] = []
    bad_rows = 0

    # ── Read raw CSV ──────────────────────────────────────────────────────
    with open(input_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        for row in reader:
            # Skip rows that clearly don't match expected schema
            # (malformed rows appear in the raw file — column count mismatch)
            if "school_name" not in row or "district" not in row:
                bad_rows += 1
                continue

            school_name = (row.get("school_name") or "").strip()
            district    = (row.get("district") or "").strip()
            school_code = (row.get("school_code") or "").strip()
            dist_code   = (row.get("dist_code") or "").strip()

            # Skip rows with no school identity
            if not school_name and not school_code:
                bad_rows += 1
                continue

            key = (school_code, school_name, district, dist_code)

            if key not in school_meta:
                school_meta[key] = {
                    "school_name": school_name,
                    "district": district,
                    "school_code": school_code,
                    "dist_code": dist_code,
                }

            stats = school_stats[key]
            stats["total_feedback"] += 1

            rating_raw = (row.get("rating") or "").strip()
            rating_int = safe_int(rating_raw)

            if rating_int is not None:
                stats["total_rated"] += 1
                if rating_int == 1:
                    stats["rating_1"] += 1
                elif rating_int == 2:
                    stats["rating_2"] += 1
                elif rating_int == 3:
                    stats["rating_3"] += 1
                elif rating_int == 4:
                    stats["rating_4"] += 1
                elif rating_int == 5:
                    stats["rating_5"] += 1

            reason = (row.get("rating_reason") or "").strip()
            if is_meaningful_reason(reason):
                stats["reasons_with_text"] += 1
                # Collect for filtered CSV
                filtered_rows.append({
                    "id":                row.get("id", ""),
                    "session_id":        row.get("session_id", ""),
                    "user_id":           row.get("user_id", ""),
                    "dist_code":         dist_code,
                    "district":          district,
                    "school_code":       school_code,
                    "school_name":       school_name,
                    "category":          (row.get("category") or "").strip(),
                    "rating":            rating_raw,
                    "rating_reason":     reason,
                    "original_message":  (row.get("original_message") or "").strip(),
                    "translated_message":(row.get("translated_message") or "").strip(),
                    "processed_at":      (row.get("processed_at") or "").strip(),
                })

    print(f"  Total rows read  : {sum(s['total_feedback'] for s in school_stats.values()):,}")
    print(f"  Malformed/skipped: {bad_rows:,}")
    print(f"  Unique schools   : {len(school_stats):,}")
    print(f"  Rows with reasons: {len(filtered_rows):,}")

    # ── Write CSV-1: School Aggregate ─────────────────────────────────────
    with open(out_aggregate, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=AGG_COLS)
        writer.writeheader()

        # Sort by district then school name for readability
        sorted_keys = sorted(school_stats.keys(), key=lambda k: (k[2], k[1]))

        for key in sorted_keys:
            meta  = school_meta[key]
            stats = school_stats[key]
            writer.writerow({
                "school_name":      meta["school_name"],
                "district":         meta["district"],
                "school_code":      meta["school_code"],
                "dist_code":        meta["dist_code"],
                "total_feedback":   stats["total_feedback"],
                "rating_1":         stats["rating_1"],
                "rating_2":         stats["rating_2"],
                "rating_3":         stats["rating_3"],
                "rating_4":         stats["rating_4"],
                "rating_5":         stats["rating_5"],
                "total_rated":      stats["total_rated"],
                "reasons_with_text":stats["reasons_with_text"],
            })

    print(f"\nCSV-1 written → {out_aggregate}")

    # ── Write CSV-2: Filtered rows with reasons ───────────────────────────
    with open(out_filtered, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RAW_COLS)
        writer.writeheader()
        writer.writerows(filtered_rows)

    print(f"CSV-2 written → {out_filtered}")
    print("\nDone ✓")


if __name__ == "__main__":
    main()
