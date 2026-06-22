#!/usr/bin/env python3
"""
generate_report.py
------------------
Generates the 8-metric AP PTM qualitative report using Claude.

Strategy:
  - Load raw.csv, keep only rating 1/2 rows with meaningful text (~1000 rows)
  - One focused Claude API call per metric (max_tokens=6000 each)
  - Assembles full markdown report

Usage:
  export ANTHROPIC_API_KEY=sk-ant-...
  python3 generate_report.py [raw_csv] [aggregate_csv] [output_md]
"""

import csv, os, sys, time
from collections import defaultdict
from typing import Optional

# ── Paths ──────────────────────────────────────────────────────────────────
SCRIPT_DIR    = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RAW   = os.path.join(SCRIPT_DIR, "promts", "raw.csv")
DEFAULT_AGG   = os.path.join(SCRIPT_DIR, "promts", "school_aggregate.csv")
DEFAULT_OUT   = os.path.join(SCRIPT_DIR, "promts", "qualitative_report_output.md")
MODEL         = "claude-3-5-sonnet-20241022"
MAX_TOKENS    = 6000

# ── Junk filter ────────────────────────────────────────────────────────────
JUNK = {
    "ok","okay","good","fine","nice","yes","no","na","none","best","great",
    "super","excellent","satisfied","father","mother","parent","sir","madam",
    "reply","next","star","quickly","done","noted","thanks","thank you",
}

def meaningful(text: str) -> bool:
    if not text: return False
    t = text.strip().lower()
    if len(t) < 10: return False
    if t in JUNK: return False
    if " " not in t and len(t) < 18: return False
    return True

def safe_int(v) -> Optional[int]:
    try: return int(str(v).strip())
    except: return None

# ── Load data ──────────────────────────────────────────────────────────────

def load_complaints(raw_csv: str) -> list:
    """Return list of dicts for rating 1/2 rows with meaningful reasons."""
    rows = []
    with open(raw_csv, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rating = safe_int(r.get("rating",""))
            if rating not in (1, 2): continue
            reason = (r.get("rating_reason") or "").strip()
            if not meaningful(reason): continue
            rows.append({
                "district":    (r.get("district") or "").strip(),
                "school":      (r.get("school_name") or "").strip(),
                "school_code": (r.get("school_code") or "").strip(),
                "category":    (r.get("category") or "").strip(),
                "rating":      rating,
                "reason":      reason,
                "user_id":     (r.get("user_id") or "").strip(),
            })
    return rows

def load_aggregate(agg_csv: str) -> dict:
    agg = {}
    with open(agg_csv, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            key = (r["school_name"].strip(), r["district"].strip())
            agg[key] = {
                "total": safe_int(r.get("total_feedback",0)) or 0,
                "r1":    safe_int(r.get("rating_1",0)) or 0,
                "r2":    safe_int(r.get("rating_2",0)) or 0,
            }
    return agg

# ── Format complaints as plain text table for LLM prompts ─────────────────

def fmt(rows: list, max_rows: int = 9999) -> str:
    lines = ["District | School | Category | Rating | Complaint"]
    for r in rows[:max_rows]:
        school = r["school"][:60]
        reason = r["reason"][:200].replace("\n", " ")
        lines.append(f"{r['district']} | {school} | {r['category']} | {r['rating']} | {reason}")
    return "\n".join(lines)

# ── Claude call ────────────────────────────────────────────────────────────

def claude(client, prompt: str, max_tokens: int = MAX_TOKENS, label: str = "") -> str:
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        elapsed = round(time.time() - t0, 1)
        print(f"    ✓ {label} done in {elapsed}s ({resp.usage.output_tokens} output tokens)")
        return resp.content[0].text.strip()
    except Exception as e:
        print(f"    ✗ {label} failed: {e}")
        return f"_Error generating this section: {e}_"

# ── Metric generators ──────────────────────────────────────────────────────

def gen_header(rows: list, agg: dict) -> str:
    total_resp = sum(v["total"] for v in agg.values())
    r12 = sum(v["r1"]+v["r2"] for v in agg.values())
    districts = len(set(r["district"] for r in rows))
    return f"""# AP PTM School Survey — Qualitative Complaint Analysis Report

**Survey Scope:** AP Gurukulam & Government Welfare Schools, Andhra Pradesh
**Survey Type:** Parent-Teacher Meeting (PTM) Feedback
**Total Responses Analysed:** {total_resp:,} | **Rating 1–2 (Critical):** {r12:,} | **Meaningful Complaints (Rating 1–2):** {len(rows):,}
**Districts Covered:** {districts} districts across Andhra Pradesh

---"""

def gen_metric1(rows: list, client) -> str:
    if not client: return "_API key not set_"
    data = fmt(rows)  # send all complaints
    prompt = f"""You are an education data analyst generating a qualitative report for AP PTM school surveys.

Below is a list of parent complaints (all Rating 1 or 2) from the survey:

{data}

Generate Metric 1: Issues or Complaints Across Category, School, District.

Rules:
- Group semantically similar complaints into named Issue Themes (e.g., "No RO/water plant, water facility absent")
- For each (District, School, Category, Issue Theme) group, count unique users and total complaints
- Sort by Complaint Count descending
- Show top 50 rows maximum
- Do NOT include a Total Feedbacks or Risk Level column

Output ONLY this markdown table (no explanation before or after):

> ⚠️ All entries below are driven by Rating 1 or 2 responses.

| District | School | Category | Issue Theme | Unique User Count | Complaint Count |
|---|---|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 1")

def gen_metric2(rows: list, client) -> str:
    if not client: return "_API key not set_"
    data = fmt(rows)  # send all complaints
    prompt = f"""You are an education data analyst generating a qualitative report for AP PTM school surveys.

Below is a list of parent complaints (all Rating 1 or 2):

{data}

Generate Metric 2: Top Recurring Issues Across Category, School, District.

Rules:
- Identify themes that appear in multiple schools/districts
- For each theme: count total frequency, rating 1/2 count, districts impacted, schools impacted
- Write a 1-2 sentence Sample Summary using direct parent language where possible
- Sort by Rating 1/2 Count descending
- Show top 20 themes maximum
- Do NOT include a Risk Level or Severity column

Output ONLY this markdown (note + table, no other text):

> ⚠️ Themes with the highest Rating 1/2 counts are ranked first.

| Issue Theme | Category | Frequency | Rating 1/2 Count | Districts Impacted | Schools Impacted | Sample Summary |
|---|---|---|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 2")

def gen_metric3(rows: list, client) -> str:
    if not client: return "_API key not set_"
    data = fmt(rows)  # send all complaints
    prompt = f"""You are an education data analyst writing the Challenges section of an AP PTM qualitative report.

Below is a list of parent complaints (Rating 1 or 2):

{data}

Generate Metric 3: Challenges That Require Attention.

Identify 6-8 major challenge themes. For EACH challenge write exactly this block:

**Challenge Title:** [title]

**Description:**
[2-4 sentences: what is happening, who is affected, include actual parent quotes from the data]

**Affected Districts:**
[comma-separated list]

**Affected Schools:**
[district: school (X complaints), school (Y complaints); next district: ...]

**Suggested Action:**
[1-2 specific actionable recommendations for district administrators]

---

Focus on: Drinking Water, Food Quality, Caretaker conduct, Teacher communication, Healthcare, Safety, Extracurricular, Cleanliness.
Do NOT include a Risk Level field."""
    return claude(client, prompt, max_tokens=5000, label="Metric 3")

def gen_metric4(rows: list, agg: dict, client) -> str:
    # Pre-aggregate complaint counts per school
    school_counts = defaultdict(lambda: {"complaints": 0, "categories": set(), "district": ""})
    for r in rows:
        key = (r["school"], r["district"])
        school_counts[key]["complaints"] += 1
        school_counts[key]["categories"].add(r["category"])
        school_counts[key]["district"] = r["district"]

    lines = ["District | School | Total Feedbacks | Complaint Count (R1/R2) | Categories Impacted"]
    for (school, dist), d in sorted(school_counts.items(), key=lambda x: -x[1]["complaints"])[:60]:
        total = (agg.get((school, dist)) or {}).get("total", 0)
        pct = round(d["complaints"]/total*100) if total else 0
        lines.append(f"{dist} | {school[:55]} | {total} | {d['complaints']} ({pct}%) | {len(d['categories'])}")

    data = "\n".join(lines)
    prompt = f"""You are an education data analyst. Below is school-level complaint data (Rating 1/2 only):

{data}

Generate Metric 4: Red-Flagged Schools, Category, District Chart.

Rules:
- Include only schools with meaningful complaint counts
- Sort by Complaint Count descending
- Show top 30 schools maximum
- Do NOT include a Risk Level column

Output ONLY this markdown (note + table):

> Schools with the highest concentration of Rating 1–2 complaints across multiple categories.

| District | School | Total Feedbacks | Complaint Count | Categories Impacted |
|---|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 4") if client else "_API key not set_"

def gen_metric5(rows: list, client) -> str:
    data = fmt(rows, 200)
    prompt = f"""You are an education data analyst. Below are parent complaints (Rating 1 or 2):

{data}

Generate Metric 5: Master Table (All Actionable Comments).

For each complaint row:
- Extract a 3-6 word theme label
- Write a short Action Required note
- Assign Priority: Critical (Rating 1, serious harm) or High (Rating 1-2, service failure)

Rules:
- Include all rating 1 rows and most impactful rating 2 rows
- Sort by District, then School
- Show top 60 rows maximum

Output ONLY this markdown (note + table):

> All entries carry Rating 1 or 2 with specific, actionable complaint content.

| District | School | Category | Rating | Original Comment | Extracted Theme | Action Required | Priority |
|---|---|---|---|---|---|---|---|
[rows here]"""
    return claude(client, prompt, max_tokens=5000, label="Metric 5") if client else "_API key not set_"

def gen_metric6(rows: list, client) -> str:
    cat_rows = [r for r in rows if r["category"] == "PRINCIPAL_SATISFACTION"]
    if not cat_rows:
        return "_No principal complaints found._"
    data = fmt(cat_rows, 100)
    prompt = f"""You are an education data analyst. Below are principal-related complaints (Rating 1 or 2):

{data}

Generate Metric 6: Red-Flagged Principals.

Rules:
- Group by school; include schools with ≥1 Rating 1 complaint or ≥2 complaints total
- Summarise common issues per school
- Do NOT include a Risk Level column

Output ONLY this markdown (note + table + bullet issues per school):

> Principals/schools where feedback identifies leadership failure. Includes only schools with Complaint Count ≥ 2 or any Rating 1.

| District | School | Complaint Count | Common Issues |
|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 6") if client else "_API key not set_"

def gen_metric7(rows: list, client) -> str:
    cat_rows = [r for r in rows if r["category"] == "CARETAKER_SATISFACTION"]
    if not cat_rows:
        return "_No caretaker complaints found._"
    data = fmt(cat_rows, 100)
    prompt = f"""You are an education data analyst. Below are caretaker-related complaints (Rating 1 or 2):

{data}

Generate Metric 7: Red-Flagged Caretakers.

Rules:
- Group by school; include schools with ≥1 Rating 1 complaint or ≥3 complaints total
- Summarise common issues per school (rude behaviour, absence, neglect)
- Do NOT include a Risk Level column

Output ONLY this markdown (note + table):

> Caretakers/schools with repeated complaints. Includes schools with Complaint Count ≥ 3 or any Rating 1.

| District | School | Complaint Count | Common Issues |
|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 7") if client else "_API key not set_"

def gen_metric8(rows: list, client) -> str:
    cat_rows = [r for r in rows if r["category"] == "DRINKING_WATER_RATING"]
    if not cat_rows:
        return "_No water complaints found._"
    data = fmt(cat_rows, 150)
    prompt = f"""You are an education data analyst. Below are drinking water complaints (Rating 1 or 2):

{data}

Generate Metric 8: Red-Flagged Clean Water Facility.

Rules:
- Group by school
- Include schools with ≥2 complaints OR any Rating 1 complaint
- Write a descriptive Water Issue Theme summarising what parents reported (use their language)
- Sort by Complaint Count descending
- Do NOT include a Risk Level column

Output ONLY this markdown (note + table):

> Schools where drinking water complaints meet the threshold: Complaint Count ≥ 2 or any Rating 1.

| District | School | Complaint Count | Water Issue Theme |
|---|---|---|---|
[rows here]"""
    return claude(client, prompt, label="Metric 8") if client else "_API key not set_"

# ── Assemble ───────────────────────────────────────────────────────────────

def assemble(header, m1, m2, m3, m4, m5, m6, m7, m8) -> str:
    return f"""{header}

## Metric 1. Issues or Complaints Across Category, School, District

{m1}

---

## Metric 2. Top Recurring Issues Across Category, School, District

{m2}

---

## Metric 3. Challenges That Require Attention

{m3}

---

## Metric 4. Red-Flagged Schools, Category, District Chart

{m4}

---

## Metric 5. Master Table (All Actionable Comments)

{m5}

---

## Metric 6. Red-Flagged Principals

{m6}

---

## Metric 7. Red-Flagged Caretakers

{m7}

---

## Metric 8. Red-Flagged Clean Water Facility

{m8}

---

*Report ends. Total metrics: 8. All findings are evidence-based on parent feedback rated 1 or 2 from the AP PTM School Survey dataset.*
"""

# ── Main ───────────────────────────────────────────────────────────────────

def main():
    raw_csv = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_RAW
    agg_csv = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_AGG
    out_md  = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_OUT
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")

    print("Loading data …")
    complaints = load_complaints(raw_csv)
    agg        = load_aggregate(agg_csv)
    print(f"  Rating 1/2 meaningful complaints: {len(complaints):,}")
    print(f"  Schools in aggregate: {len(agg):,}")

    client = None
    if api_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            print("  Claude client ready ✓")
        except ImportError:
            print("  anthropic package not installed — run: pip install anthropic")
    else:
        print("  ANTHROPIC_API_KEY not set — output will be placeholder text")

    header = gen_header(complaints, agg)

    print("Generating Metric 1 …"); m1 = gen_metric1(complaints, client)
    print("Generating Metric 2 …"); m2 = gen_metric2(complaints, client)
    print("Generating Metric 3 …"); m3 = gen_metric3(complaints, client)
    print("Generating Metric 4 …"); m4 = gen_metric4(complaints, agg, client)
    print("Generating Metric 5 …"); m5 = gen_metric5(complaints, client)
    print("Generating Metric 6 …"); m6 = gen_metric6(complaints, client)
    print("Generating Metric 7 …"); m7 = gen_metric7(complaints, client)
    print("Generating Metric 8 …"); m8 = gen_metric8(complaints, client)

    report = assemble(header, m1, m2, m3, m4, m5, m6, m7, m8)
    os.makedirs(os.path.dirname(out_md), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport saved → {out_md}  ({len(report):,} chars)")

if __name__ == "__main__":
    main()
