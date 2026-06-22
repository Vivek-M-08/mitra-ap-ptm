#!/usr/bin/env python3
"""
generate_metric1.py
-------------------
Extracts and modifies Metric 1 generation logic from generate_report_bedrock.py.
Loads all Rating 1 and 2 text comments directly to let the LLM filter out positive reviews
and calculate the corrected true complaint count.
Supports both OpenRouter (NVIDIA Nemotron) and AWS Bedrock configurations via .env.
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
import concurrent.futures

# Ensure current directory is in Python path for import
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from generate_report_bedrock import (
    make_bedrock_client,
    claude,
    DEFAULT_RAW,
    BEDROCK_REGION,
    BEDROCK_MODEL_ID,
    safe_int,
    get_total,
    get_unique_users,
    TOKEN_TRACKER,
    meaningful,
    build_agg_text,
    build_school_agg_text,
    build_top_complaints_text,
    build_category_text,
    build_eligible_schools
)


def load_env():
    """Reads key-value pairs from .env and loads them into os.environ if not already set."""
    # Look in the script directory first, then the parent directory
    for path in [os.path.join(SCRIPT_DIR, ".env"), os.path.join(os.path.dirname(SCRIPT_DIR), ".env")]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        key = k.strip()
                        if key not in os.environ:
                            # Strip spaces and optional surrounding quotes
                            v_str = v.strip()
                            if (v_str.startswith('"') and v_str.endswith('"')) or (v_str.startswith("'") and v_str.endswith("'")):
                                v_str = v_str[1:-1]
                            os.environ[key] = v_str
            break




def track_tokens(label: str, input_tokens: int, output_tokens: int):
    if not label:
        return
    tot = input_tokens + output_tokens
    TOKEN_TRACKER[label] = {
        "input": input_tokens,
        "output": output_tokens,
        "total": tot
    }


def call_openrouter(prompt: str, api_key: str, model_id: str, label: str = "") -> str:
    """Invokes OpenRouter API using requests (if available) or urllib as a fallback."""
    import urllib.request
    import urllib.error
    import json
    import time
    import random

    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/google/antigravity",
        "X-Title": "Mitra qualitative report analyzer"
    }
    data = {
        "model": model_id,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }
    
    try:
        import requests
        has_requests = True
    except ImportError:
        has_requests = False
        
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        if has_requests:
            try:
                print(f"      (Using requests to call OpenRouter model: {model_id}... attempt {attempt}/{max_retries})")
                resp = requests.post(url, headers=headers, json=data, timeout=90)
                
                if resp.status_code == 429:
                    if attempt < max_retries:
                        sleep_time = attempt * 3 + random.uniform(1, 3)
                        print(f"      [429 Too Many Requests] Rate limit hit on {label or model_id}. Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                        
                resp.raise_for_status()
                res_json = resp.json()
                
                if "error" in res_json:
                    err_msg = res_json["error"].get("message", "Unknown error")
                    if "rate limit" in err_msg.lower() or "429" in err_msg or "too many requests" in err_msg.lower():
                        if attempt < max_retries:
                            sleep_time = attempt * 3 + random.uniform(1, 3)
                            print(f"      Rate limit in response error on {label or model_id}. Retrying in {sleep_time:.2f}s: {err_msg}")
                            time.sleep(sleep_time)
                            continue
                    raise Exception(f"OpenRouter Error: {err_msg}")
                    
                choices = res_json.get("choices", [])
                if not choices:
                    raise Exception("OpenRouter returned an empty choices block.")
                    
                content = choices[0].get("message", {}).get("content")
                if content is None:
                    raise Exception("OpenRouter response message content is empty.")
                    
                usage = res_json.get("usage", {})
                input_tokens = usage.get("prompt_tokens", 0)
                output_tokens = usage.get("completion_tokens", 0)
                track_tokens(label, input_tokens, output_tokens)
                
                return content.strip()
            except Exception as e:
                # Check if it's a 429 status code
                status_code = getattr(getattr(e, 'response', None), 'status_code', None)
                if status_code == 429:
                    if attempt < max_retries:
                        sleep_time = attempt * 3 + random.uniform(1, 3)
                        print(f"      Rate limit hit (429) on {label or model_id}. Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                if attempt < max_retries:
                    sleep_time = attempt * 2 + random.uniform(1, 2)
                    print(f"      Exception during call to {label or model_id}: {e}. Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue
                raise Exception(f"OpenRouter Call Failed: {e}")
        else:
            # urllib fallback
            print(f"      (requests library not found; using urllib to call OpenRouter model: {model_id}... attempt {attempt}/{max_retries})")
            req = urllib.request.Request(url, data=json.dumps(data).encode("utf-8"), headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as response:
                    res_json = json.loads(response.read().decode("utf-8"))
                    
                    if "error" in res_json:
                        err_msg = res_json["error"].get("message", "Unknown error")
                        if "rate limit" in err_msg.lower() or "429" in err_msg or "too many requests" in err_msg.lower():
                            if attempt < max_retries:
                                sleep_time = attempt * 3 + random.uniform(1, 3)
                                print(f"      Rate limit in response error on {label or model_id}. Retrying in {sleep_time:.2f}s: {err_msg}")
                                time.sleep(sleep_time)
                                continue
                        raise Exception(f"OpenRouter Error: {err_msg}")
                        
                    choices = res_json.get("choices", [])
                    if not choices:
                        raise Exception("OpenRouter returned an empty choices block.")
                        
                    content = choices[0].get("message", {}).get("content")
                    if content is None:
                        raise Exception("OpenRouter response message content is empty.")
                        
                    usage = res_json.get("usage", {})
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    track_tokens(label, input_tokens, output_tokens)
                    
                    return content.strip()
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if attempt < max_retries:
                        sleep_time = attempt * 3 + random.uniform(1, 3)
                        print(f"      urllib HTTP Error 429 on {label or model_id}. Retrying in {sleep_time:.2f}s...")
                        time.sleep(sleep_time)
                        continue
                err_body = e.read().decode("utf-8")
                raise Exception(f"OpenRouter HTTP Error {e.code}: {err_body}")
            except Exception as e:
                if attempt < max_retries:
                    sleep_time = attempt * 2 + random.uniform(1, 2)
                    print(f"      urllib error on {label or model_id}: {e}. Retrying in {sleep_time:.2f}s...")
                    time.sleep(sleep_time)
                    continue
                raise Exception(f"OpenRouter Connection Error: {e}")


def load_data_all_ratings_1_2(raw_csv: str):
    """
    Loads all data but keeps all comments with rating 1 or 2.
    Does NOT filter using meaningful() to let Claude/LLM determine sentiment/relevance.
    """
    complaints = []
    all_totals = defaultdict(int)
    all_unique_users = defaultdict(set)
    raw_count = 0
    skipped = 0

    with open(raw_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            raw_count += 1
            district    = (r.get("district") or "").strip()
            school      = (r.get("school_name") or "").strip()
            school_code = (r.get("school_code") or "").strip()
            category    = (r.get("category") or "").strip()
            rating      = safe_int(r.get("rating", ""))
            reason      = (r.get("rating_reason") or "").strip()
            user_id     = (r.get("user_id") or "").strip()

            if not district or not school or not category or rating is None:
                skipped += 1
                continue

            key = (school, district, category)

            # Count every row as a feedback (all ratings, all reasons)
            all_totals[key] += 1

            # Track unique users across ALL ratings
            if user_id:
                all_unique_users[key].add(user_id)

            # Keep all rating 1 / 2 comments with meaningful text
            if rating not in (1, 2):
                continue
            if not meaningful(reason):
                continue

            complaints.append(
                {
                    "district":    district,
                    "school":      school,
                    "school_code": school_code,
                    "category":    category,
                    "rating":      rating,
                    "reason":      reason,
                    "user_id":     user_id,
                }
            )

    print(f"  Raw CSV rows (total)             : {raw_count:,}")
    print(f"  Skipped (missing field/rating)   : {skipped:,}")
    return complaints, dict(all_totals), dict(all_unique_users)


def build_agg_text_for_metric1(complaints, all_totals, all_unique_users):
    """
    Aggregates complaints by (district, school, category).
    Includes all rating 1/2 comment texts joined together.
    """
    groups = defaultdict(lambda: {"rows": []})
    for r in complaints:
        key = (r["district"], r["school"], r["category"])
        groups[key]["rows"].append(r)

    lines = [
        "District | School | Category | Total Feedbacks | "
        "Complaint Count with Reason | Unique Users | Sample Complaints"
    ]

    # Sort by complaint count desc
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]["rows"]))
    for (dist, school, cat), d in sorted_groups:
        total = get_total(all_totals, school, dist, cat)
        cc    = len(d["rows"])
        uniq  = get_unique_users(all_unique_users, school, dist, cat)
        
        # Format and join all reasons
        reasons = []
        for r in d["rows"]:
            cleaned_reason = r["reason"].replace("\n", " ").replace("|", "\\|").strip()
            reasons.append(cleaned_reason)
        samples = " ; ".join(reasons)
        
        lines.append(
            f"{dist} | {school} | {cat} | {total} | "
            f"{cc} | {uniq} | {samples}"
        )
    return "\n".join(lines)


def load_metric1_csv_to_agg_text(csv_path: str) -> str:
    """
    Reads the metric1_output.csv file and converts it into the pre-aggregated text format
    expected by the Metric 2 and Metric 3 prompts.
    """
    import re
    lines = [
        "District | School | Category | Total Feedbacks | "
        "Complaint Count | Unique Users | Rating 1 Count | Sample Complaints"
    ]
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"'{csv_path}' not found. Please ensure Metric 1 ran successfully.")

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            district = r.get("District", "").strip()
            school = r.get("School", "").strip()
            category = r.get("Category", "").strip()
            unique_users = r.get("Unique User Count", "0").strip()
            total_feedbacks = r.get("Total Feedbacks", "0").strip()
            true_complaint_count_str = r.get("True Complaint Count", "0").strip()
            complaints = r.get("Complaints", "").strip()

            match = re.match(r"^(\d+)", true_complaint_count_str)
            cc = int(match.group(1)) if match else 0
            
            pct = 0
            try:
                total_val = int(total_feedbacks)
                if total_val > 0:
                    pct = round(cc / total_val * 100)
            except Exception:
                pass

            # Map to columns expected by prompt
            lines.append(
                f"{district} | {school} | {category} | {total_feedbacks} | "
                f"{cc} ({pct}%) | {unique_users} | {cc} | {complaints}"
            )
            
    return "\n".join(lines)


def load_filtered_data_from_csv_and_raw(csv_path: str, raw_csv: str):
    """
    Loads the filtered complaints from metric1_output.csv and matches them with the raw CSV
    to reconstruct the complaints, all_totals, and all_unique_users structures.
    """
    import csv
    
    # 1. Parse metric1_output.csv to find which (school, district, category, reason) are true complaints
    true_keys = set()
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"'{csv_path}' not found.")
        
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            district = r.get("District", "").strip()
            school = r.get("School", "").strip()
            category = r.get("Category", "").strip()
            complaints_str = r.get("Complaints", "").strip()
            if not complaints_str:
                continue
            # Split by " ; " to get individual complaint texts
            reasons = [part.strip() for part in complaints_str.split(" ; ") if part.strip()]
            for reason in reasons:
                # Store a key to match exactly
                true_keys.add((school, district, category, reason))
                
    # 2. Read raw CSV and filter complaints
    # We also keep all_totals and all_unique_users based on raw CSV
    complaints = []
    all_totals = defaultdict(int)
    all_unique_users = defaultdict(set)
    
    with open(raw_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            district    = (r.get("district") or "").strip()
            school      = (r.get("school_name") or "").strip()
            school_code = (r.get("school_code") or "").strip()
            category    = (r.get("category") or "").strip()
            rating      = safe_int(r.get("rating", ""))
            reason      = (r.get("rating_reason") or "").strip()
            user_id     = (r.get("user_id") or "").strip()

            if not district or not school or not category or rating is None:
                continue

            key = (school, district, category)
            all_totals[key] += 1
            if user_id:
                all_unique_users[key].add(user_id)

            if rating not in (1, 2):
                continue
            if not meaningful(reason):
                continue

            # Check if this complaint is in the true complaints set
            cleaned_reason = reason.replace("\n", " ").replace("|", "\\|").strip()
            # Match by (school, district, category, cleaned_reason)
            if (school, district, category, cleaned_reason) in true_keys:
                complaints.append({
                    "district":    district,
                    "school":      school,
                    "school_code": school_code,
                    "category":    category,
                    "rating":      rating,
                    "reason":      reason,
                    "user_id":     user_id,
                })
                
    return complaints, dict(all_totals), dict(all_unique_users)


def gen_metric2(agg_text: str, client) -> str:
    """Generates Metric 2: Top Recurring Issues Across Category, School, District."""
    prompt = f"""You are an education data analyst generating Metric 2 of an AP PTM qualitative report.

Below is pre-aggregated parent complaint data (Rating 1 and 2 only).
COLUMNS: District | School | Category | Total Feedbacks | Complaint Count | Unique Users | Rating 1 Count | Sample Complaints

DATA:
{agg_text}

TASK — Generate Metric 2: Top Recurring Issues Across Category, School, District.

RULES:
1. Group rows by similar Issue Theme across all schools/districts.
2. For each theme compute:
   - Rating 1/2 Count = total complaint rows across all groups sharing that theme (sum Complaint Count numbers only)
   - Districts Impacted = count distinct District values in that theme group
   - Schools Impacted   = count distinct School values in that theme group
3. Write a 1–2 sentence Sample Summary using direct language from Sample Complaints.
4. Sort by Rating 1/2 Count DESCENDING.
5. Include only themes appearing in ≥ 2 distinct schools OR with Rating 1/2 Count ≥ 5.
6. Show top 20 themes maximum.
7. Do NOT add a Risk Level or Severity column.

Output ONLY this markdown block (note + table, nothing else):

> ⚠️ Themes ranked by Rating 1/2 Count (descending).

| Issue Theme | Category | Rating 1/2 Count | Districts Impacted | Schools Impacted | Sample Summary |
|---|---|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 2")


def gen_metric3(agg_text: str, client) -> str:
    """Generates Metric 3: Challenges That Require Attention."""
    prompt = f"""You are a senior education analyst writing Metric 3 of an AP PTM qualitative report.

Below is pre-aggregated parent complaint data (Rating 1 and 2 only).
COLUMNS: District | School | Category | Total Feedbacks | Complaint Count | Unique Users | Rating 1 Count | Sample Complaints

DATA:
{agg_text}

TASK — Generate Metric 3: Challenges That Require Attention.

Identify 6–8 major challenge themes. Focus areas (use complaint data to find which are most prevalent):
- Drinking water access / quality
- Food and diet quality
- Caretaker conduct and hygiene
- Teacher communication gaps
- Healthcare and medical support
- Safety risks
- Extracurricular activities
- Cleanliness and infrastructure

For EACH challenge, output EXACTLY this block — no field may be skipped or abbreviated:

**Challenge Title:** [name]

**Description:**
[2–4 sentences: what is happening, who is affected, how it appears in the data.
Include specific language directly from the Complaints column.]

**Affected Districts:**
[comma-separated list of districts where this challenge appears]

**Affected Schools:**
[Format — District: School Name (total feedbacks: N | complaints: X), School Name (...); Next District: ...]
[Use the pre-computed Total Feedbacks and Complaint Count values from the data above.]

**Suggested Action:**
[1–2 specific, actionable recommendations for district administrators]

---

RULES:
- Use the pre-computed counts — do NOT invent or recalculate numbers.
- Do NOT add a Risk Level field anywhere.
- Separate each challenge block with --- as shown above.
"""
    return claude(client, prompt, max_tokens=5000, label="Metric 3")


def gen_metric4(school_agg_text: str, client) -> str:
    """Generates Metric 4: Red-Flagged Schools, Category, District Chart."""
    prompt = f"""You are an education data analyst generating Metric 4 of an AP PTM qualitative report.

Below is pre-aggregated school-level complaint data. All values are PRE-COMPUTED.
COLUMNS: District | School | Total Feedbacks | Complaint Count | Rating 1 Count | Categories with Complaints

DATA:
{school_agg_text}

TASK — Generate Metric 4: Red-Flagged Schools, Category, District Chart.

RULES:
1. Include only schools with Complaint Count ≥ 3.
2. Copy the pre-computed values exactly — do NOT recalculate.
3. Show top 30 schools (data is already sorted by Complaint Count descending).
4. Do NOT add a Risk Level or Severity column.

Output ONLY this markdown block (note + table, nothing else):

> Schools with the highest concentration of Rating 1–2 complaints across multiple categories.

| District | School | Total Feedbacks | Complaint Count | Rating 1 Count | Categories with Complaints |
|---|---|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 4")


def gen_metric5(top_complaints_text: str, client) -> str:
    """Generates Metric 5: Master Table (All Actionable Comments)."""
    prompt = f"""You are an education data analyst generating Metric 5 of an AP PTM qualitative report.

Below are the most severe individual parent complaints (Rating 1 priority, then Rating 2).
COLUMNS: District | School | Category | Rating | Reason

DATA:
{top_complaints_text}

TASK — Generate Metric 5: Master Table (All Actionable Comments).

For EACH row:
- Extract a 3–6 word Extracted Theme label from the Reason text
- Write a concise Action Required note (≤ 10 words)

RULES:
1. Include all Rating 1 rows and the most impactful Rating 2 rows.
2. Sort by Rating (Rating 1 first), then District, then School.
3. Max 60 rows.
4. Do NOT add a Risk Level, Severity, or Priority column.
5. Keep Original Comment to ≤ 120 characters in the table cell.

Output ONLY this markdown block (note + table, nothing else):

> All entries carry Rating 1 or 2 with specific, actionable complaint content.

| District | School | Category | Rating | Original Comment | Extracted Theme | Action Required |
|---|---|---|---|---|---|---|
"""
    return claude(client, prompt, max_tokens=5000, label="Metric 5")


def gen_metric6(category_text: str, eligible: list, client) -> str:
    """Generates Metric 6: Red-Flagged Principals."""
    if not eligible:
        return "_No schools met the red-flag threshold for principals (≥ 3 complaints or any Rating 1)._"

    eligible_lines = "\n".join(
        f"{e['district']} | {e['school']} | total: {e['total']} | "
        f"complaints: {e['count']} ({e['pct']}%) | rating-1: {e['r1']}"
        for e in eligible
    )

    prompt = f"""You are an education data analyst generating Metric 6 of an AP PTM qualitative report.

Below are principal-related parent complaints (PRINCIPAL_SATISFACTION, Rating 1 or 2).
All counts are PRE-COMPUTED.
COLUMNS: District | School | Total Feedbacks | Complaint Count | Rating 1 Count | Rating | Reason

COMPLAINT DATA:
{category_text}

PRE-COMPUTED ELIGIBLE SCHOOLS (threshold: Complaint Count ≥ 3 OR any Rating 1):
{eligible_lines}

TASK — Generate Metric 6: Red-Flagged Principals.

RULES:
1. Include ONLY the eligible schools listed above — no others.
2. Use the pre-computed values from the eligible school list — do NOT recalculate.
3. Format Complaint Count as: N (X%)
4. Do NOT add a Risk Level column.
5. Do NOT add any bullet points or issue summaries after the table.

Output ONLY this markdown block (note + table, nothing else):

> Principals/schools where parent feedback identifies leadership failure.
> Threshold: Complaint Count ≥ 3 or any Rating 1.

| District | School | Total Feedbacks | Complaint Count |
|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 6")


def gen_metric7(category_text: str, eligible: list, client) -> str:
    """Generates Metric 7: Red-Flagged Caretakers."""
    if not eligible:
        return "_No schools met the red-flag threshold for caretakers (≥ 3 complaints or any Rating 1)._"

    eligible_lines = "\n".join(
        f"{e['district']} | {e['school']} | total: {e['total']} | "
        f"complaints: {e['count']} ({e['pct']}%) | rating-1: {e['r1']}"
        for e in eligible
    )

    prompt = f"""You are an education data analyst generating Metric 7 of an AP PTM qualitative report.

Below are caretaker-related parent complaints (CARETAKER_SATISFACTION, Rating 1 or 2).
All counts are PRE-COMPUTED.
COLUMNS: District | School | Total Feedbacks | Complaint Count | Rating 1 Count | Rating | Reason

COMPLAINT DATA:
{category_text}

PRE-COMPUTED ELIGIBLE SCHOOLS (threshold: Complaint Count ≥ 3 OR any Rating 1):
{eligible_lines}

TASK — Generate Metric 7: Red-Flagged Caretakers.

RULES:
1. Include ONLY the eligible schools listed above — no others.
2. Use the pre-computed values — do NOT recalculate.
3. Format Complaint Count as: N (X%)
4. Do NOT add a Risk Level column.
5. Do NOT add any bullet points or issue summaries after the table.

Output ONLY this markdown block (note + table, nothing else):

> Caretakers/schools with repeated parent complaints.
> Threshold: Complaint Count ≥ 3 or any Rating 1.

| District | School | Total Feedbacks | Complaint Count |
|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 7")


def gen_metric8(category_text: str, eligible: list, client) -> str:
    """Generates Metric 8: Red-Flagged Clean Water Facility."""
    if not eligible:
        return "_No schools met the red-flag threshold for water (≥ 3 complaints or any Rating 1)._"

    eligible_lines = "\n".join(
        f"{e['district']} | {e['school']} | total: {e['total']} | "
        f"complaints: {e['count']} ({e['pct']}% of feedbacks) | rating-1: {e['r1']}"
        for e in eligible
    )

    prompt = f"""You are an education data analyst generating Metric 8 of an AP PTM qualitative report.

Below are drinking-water parent complaints (DRINKING_WATER_RATING, Rating 1 or 2).
All counts are PRE-COMPUTED.
COLUMNS: District | School | Total Feedbacks | Complaint Count | Rating 1 Count | Rating | Reason

COMPLAINT DATA:
{category_text}

PRE-COMPUTED ELIGIBLE SCHOOLS (threshold: Complaint Count ≥ 3 OR any Rating 1):
{eligible_lines}

TASK — Generate Metric 8: Red-Flagged Clean Water Facility.

RULES:
1. Include ONLY the eligible schools listed above — no others.
2. Use the pre-computed Complaint Count values — do NOT recalculate.
3. Format Complaint Count as: N (X% of feedbacks)
4. For the Water Issue Theme column, write 2–3 descriptive themes using parents' own language
   (e.g. "Foul-smelling water", "No water supply for days", "RO plant not working").
5. Sort by Complaint Count descending (data above is already sorted).
6. Do NOT add a Risk Level column.

Output ONLY this markdown block (note + table, nothing else):

> Schools where drinking water complaints meet the threshold: Complaint Count ≥ 3 or any Rating 1.

| District | School | Complaint Count | Water Issue Theme |
|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 8")


def gen_report_header_from_filtered(csv_path: str) -> str:
    """Computes basic report statistics directly from the filtered CSV to generate the header."""
    import csv
    import os
    import re
    
    total_resp = 0
    districts = set()
    schools = set()
    true_complaints_total = 0
    
    if os.path.exists(csv_path):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                district = r.get("District", "").strip()
                school = r.get("School", "").strip()
                total_feedbacks = r.get("Total Feedbacks", "0").strip()
                true_complaint_count_str = r.get("True Complaint Count", "0").strip()
                
                if district:
                    districts.add(district)
                if school and district:
                    schools.add((school, district))
                
                try:
                    total_resp += int(total_feedbacks)
                except ValueError:
                    pass
                
                match = re.match(r"^(\d+)", true_complaint_count_str)
                true_complaints_total += int(match.group(1)) if match else 0
                
    return (
        "# AP PTM School Survey — Qualitative Complaint Analysis Report (Metrics 1-3)\n\n"
        "**Survey Scope:** AP Gurukulam & Government Welfare Schools, Andhra Pradesh  \n"
        "**Survey Type:** Parent-Teacher Meeting (PTM) Feedback  \n"
        f"**Total Responses Analysed:** {total_resp:,}  \n"
        f"**LLM-Filtered True Complaints:** {true_complaints_total:,}  \n"
        f"**Districts with Complaints:** {len(districts)}  \n"
        f"**Schools with Complaints:** {len(schools)}  \n\n"
        "---"
    )


def gen_metric1_markdown_from_csv(csv_path: str) -> str:
    """Reads metric1_output.csv and formats it back to a clean Markdown table (top 50 rows) for the report."""
    lines = [
        "> ⚠️ All entries are driven by Rating 1 or 2 responses. Counts are computed from survey responses, filtered by the LLM to remove false-positive reviews.",
        "",
        "| District | School | Category | Issue Theme | Unique User Count | Total Feedbacks | True Complaint Count |",
        "|---|---|---|---|---|---|---|",
    ]
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for idx, r in enumerate(reader):
            if idx >= 50:
                break
            lines.append(
                f"| {r['District']} | {r['School']} | {r['Category']} | {r['Issue Theme']} | "
                f"{r['Unique User Count']} | {r['Total Feedbacks']} | {r['True Complaint Count']} |"
            )
    return "\n".join(lines)


def assemble_report(header: str, m1: str, m2: str, m3: str, m4: str, m5: str, m6: str, m7: str, m8: str) -> str:
    """Assembles metrics 1 through 8 into a single unified Markdown report."""
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

*Report ends. Total metrics: 8. All findings are evidence-based on parent feedback
rated 1 or 2 from the AP PTM School Survey dataset, filtered to remove false-positives.*
"""


def get_metric1_prompt(agg_text: str, total_resp: int) -> str:
    return f"""You are an education data analyst. Analyze the following parent comments from an AP PTM report.
Context: These comments are attached to low ratings (1 or 2), but some parents mistakenly left positive notes.
    
Rules:
1. Filter out all positive, complimentary, or satisfaction-expressing remarks.
2. Count only actual negative issues or complaints to determine the "True Complaint Count".
3. Calculate the "True Complaint Percentage" as: (True Complaint Count / Total Feedbacks) * 100, rounded to the nearest integer.
4. Format the final "True Complaint Count" column as: True Complaint Count (True Complaint Percentage% of feedbacks)
   Example: 5 (7% of feedbacks)
5. Include all the true complaints in the final output in the "Complaints" column (separated by " ; " if multiple).
6. If there are valid complaints, provide a 3-6 word "Issue Theme" summarizing them.
7. Sort the final rows by True Complaint Count descending.

INPUT DATA:
{agg_text}

Output ONLY a markdown table with this structure (nothing else):

| District | School | Category | Issue Theme | Unique User Count | Total Feedbacks | True Complaint Count | Complaints |
|---|---|---|---|---|---|---|---|
"""


def parse_markdown_table_rows(output_str: str) -> list:
    """Parses markdown table rows from LLM output, extracting structural fields and true complaint count."""
    import re
    rows = []
    if not output_str:
        return rows
    for line in output_str.strip().split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 9:
            district = parts[1]
            school = parts[2]
            category = parts[3]
            theme = parts[4]
            unique_users = parts[5]
            total_feedbacks = parts[6]
            true_complaint_count_str = parts[7]
            complaints = parts[8]
            
            # Skip header or separator rows
            if "district" in district.lower() or "---" in district:
                continue
                
            # Extract number from true_complaint_count_str (e.g. "5 (7% of feedbacks)" -> 5)
            try:
                count_val = 0
                match = re.match(r"^(\d+)", true_complaint_count_str)
                if match:
                    count_val = int(match.group(1))
            except Exception:
                count_val = 0
                
            rows.append({
                "district": district,
                "school": school,
                "category": category,
                "theme": theme,
                "unique_users": unique_users,
                "total_feedbacks": total_feedbacks,
                "true_complaint_count_str": true_complaint_count_str,
                "count_val": count_val,
                "complaints": complaints
            })
    return rows

def process_batch(batch_num: int, batch: list, header: str, total_resp: int, provider: str, 
                  openrouter_api_key: str, openrouter_model: str, 
                  aws_access_key: str, aws_secret_key: str, aws_region: str, aws_model_id: str,
                  client) -> tuple:
    """Runs a single batch LLM request and returns extracted rows and its tracker label."""
    batch_text = header + "\n" + "\n".join(batch)
    prompt = get_metric1_prompt(batch_text, total_resp)
    label = f"Metric 1 Batch {batch_num}"
    output = ""
    
    if provider == "openrouter":
        try:
            print(f"  [Batch {batch_num}] Starting call to OpenRouter (model={openrouter_model}) ...")
            output = call_openrouter(prompt, openrouter_api_key, openrouter_model, label=label)
        except Exception as e:
            print(f"  [Batch {batch_num}] OpenRouter call failed: {e}")
    elif provider == "bedrock":
        try:
            print(f"  [Batch {batch_num}] Starting call to AWS Bedrock (region={aws_region}, model={aws_model_id}) ...")
            output = claude(client, prompt, label=label)
        except Exception as e:
            print(f"  [Batch {batch_num}] AWS Bedrock call failed: {e}")
            
    rows = parse_markdown_table_rows(output)
    print(f"  [Batch {batch_num}] Completed: extracted {len(rows)} valid rows.")
    return rows, label


def run_metric1(raw_csv, output_csv, provider, openrouter_api_key, openrouter_model, 
                aws_access_key, aws_secret_key, aws_region, aws_model_id, client):
    # 1. Load and pre-aggregate
    print("Loading and pre-aggregating data ...")
    complaints, all_totals, all_unique_users = load_data_all_ratings_1_2(raw_csv)
    total_resp = sum(all_totals.values())
    
    file_size_mb = os.path.getsize(raw_csv) / (1024 * 1024)
    print(f"  Input CSV file size              : {file_size_mb:.2f} MB")
    print(f"  Total response rows              : {total_resp:,}")
    print(f"  Rating 1/2 complaints            : {len(complaints):,}")
    print(f"  (school, district, category) keys: {len(all_totals):,}")
    
    # 2. Build aggregation text and split into batches of 30
    agg_text = build_agg_text_for_metric1(complaints, all_totals, all_unique_users)
    all_lines = agg_text.strip().split("\n")
    header = all_lines[0]
    data_lines = all_lines[1:]
    
    # Using batch size of 30 as requested by the user
    batch_size = 30
    batches = [data_lines[i:i + batch_size] for i in range(0, len(data_lines), batch_size)]
    print(f"Split {len(data_lines)} pre-aggregated rows into {len(batches)} batches of up to {batch_size} rows.")
    print("Executing all API calls in parallel ...")
    
    all_extracted_rows = []
    total_in_tokens = 0
    total_out_tokens = 0
    batch_labels = []
    
    # Process all batches in parallel using ThreadPoolExecutor
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(batches)) as executor:
        futures = {
            executor.submit(
                process_batch,
                batch_num=idx + 1,
                batch=batch,
                header=header,
                total_resp=total_resp,
                provider=provider,
                openrouter_api_key=openrouter_api_key,
                openrouter_model=openrouter_model,
                aws_access_key=aws_access_key,
                aws_secret_key=aws_secret_key,
                aws_region=aws_region,
                aws_model_id=aws_model_id,
                client=client
            ): idx
            for idx, batch in enumerate(batches)
        }
        
        for future in concurrent.futures.as_completed(futures):
            idx = futures[future]
            batch_num = idx + 1
            try:
                rows, label = future.result()
                all_extracted_rows.extend(rows)
                batch_labels.append(label)
            except Exception as e:
                print(f"  [Batch {batch_num}] Worker thread failed: {e}")
                
    # Accumulate tokens and delete temporary batch entries to keep the usage table clean
    for label in batch_labels:
        if label in TOKEN_TRACKER:
            total_in_tokens += TOKEN_TRACKER[label].get("input", 0)
            total_out_tokens += TOKEN_TRACKER[label].get("output", 0)
            del TOKEN_TRACKER[label]
            
    # Save the consolidated token counts under "Metric 1"
    if total_in_tokens > 0 or total_out_tokens > 0:
        TOKEN_TRACKER["Metric 1"] = {
            "input": total_in_tokens,
            "output": total_out_tokens,
            "total": total_in_tokens + total_out_tokens
        }
        
    # 3. Globally sort all extracted rows and pick top 50
    sorted_rows = sorted(all_extracted_rows, key=lambda x: -x["count_val"])
    
    # Save the combined results to a CSV file
    try:
        with open(output_csv, "w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow([
                "District", "School", "Category", "Issue Theme",
                "Unique User Count", "Total Feedbacks", "True Complaint Count", "Complaints"
            ])
            for r in sorted_rows:
                writer.writerow([
                    r["district"],
                    r["school"],
                    r["category"],
                    r["theme"],
                    r["unique_users"],
                    r["total_feedbacks"],
                    r["true_complaint_count_str"],
                    r["complaints"]
                ])
        print(f"Saved {len(sorted_rows)} combined rows to CSV → {output_csv}")
    except Exception as e:
        print(f"Failed to save combined results to CSV: {e}")
    
    # 4. Format final consolidated output markdown table
    final_table_lines = [
        f"> ⚠️ All entries are driven by Rating 1 or 2 responses. Counts are computed from total survey responses, filtered by the LLM to remove false-positive positive reviews.",
        "",
        "| District | School | Category | Issue Theme | Unique User Count | Total Feedbacks | True Complaint Count | Complaints |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in sorted_rows[:50]:
        final_table_lines.append(
            f"| {row['district']} | {row['school']} | {row['category']} | {row['theme']} | "
            f"{row['unique_users']} | {row['total_feedbacks']} | {row['true_complaint_count_str']} | {row['complaints']} |"
        )
    final_output = "\n".join(final_table_lines)
    
    print("\n" + "="*80)
    print("=== METRIC 1 OUTPUT (CONSOLIDATED BATCHES LLM RESPONSE) ===")
    print("="*80)
    print(final_output)
    print("="*80 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Print input and output of Metric 1 using AWS Bedrock or OpenRouter in parallel."
    )
    parser.add_argument(
        "raw_csv", nargs="?", default=DEFAULT_RAW,
        help="Path to the raw survey CSV file (default: ./raw.csv)"
    )
    args = parser.parse_args()

    raw_csv = args.raw_csv

    # Load env variables from .env
    load_env()

    # Determine Provider
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.environ.get("OPENROUTER_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct").strip()

    # Detect and alert if model is a reranker
    if "rerank" in openrouter_model.lower():
        print(f"\n  [ERROR] OpenRouter model '{openrouter_model}' is a reranker model and cannot be used for completions.\n"
              f"  Please configure a valid chat completion model (e.g. google/gemini-2.5-flash or meta-llama/llama-3.3-70b-instruct:free)\n"
              f"  in your environment variables (export OPENROUTER_MODEL=...) or .env file.\n", file=sys.stderr)
        sys.exit(1)

    aws_access_key = os.environ.get("AWS_ACCESS_KEY_ID", "").strip()
    aws_secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", "").strip()
    aws_region = os.environ.get("AWS_REGION", BEDROCK_REGION).strip()
    aws_model_id = os.environ.get("AWS_MODEL_ID", BEDROCK_MODEL_ID).strip()

    # Auto-detect provider if not specified
    if not provider:
        if openrouter_api_key:
            provider = "openrouter"
        elif aws_access_key and aws_secret_key:
            provider = "bedrock"

    # Pre-create Bedrock client if needed
    client = None
    if provider == "bedrock":
        if aws_access_key and aws_secret_key:
            client = make_bedrock_client(aws_access_key, aws_secret_key)

    output_csv = os.path.join(SCRIPT_DIR, "metric1_output.csv")

    # To run Metrics 2 and 3 independently from the saved CSV, you can comment out the call below:
    run_metric1(raw_csv, output_csv, provider, openrouter_api_key, openrouter_model, 
                aws_access_key, aws_secret_key, aws_region, aws_model_id, client)

    # 5. Generate Metrics 2 to 8 if provider is set
    if provider:
        print("\nReconstructing clean filtered dataset from metric1_output.csv...")
        try:
            complaints, all_totals, all_unique_users = load_filtered_data_from_csv_and_raw(output_csv, raw_csv)
            
            print("\nPre-computing aggregation tables for Metrics 2-8...")
            agg_text        = build_agg_text(complaints, all_totals, all_unique_users)
            school_agg_text = build_school_agg_text(complaints, all_totals)
            top_cmp_text    = build_top_complaints_text(complaints, max_rows=60)

            principal_text  = build_category_text(complaints, all_totals, "PRINCIPAL_SATISFACTION",  max_rows=120)
            caretaker_text  = build_category_text(complaints, all_totals, "CARETAKER_SATISFACTION",  max_rows=120)
            water_text      = build_category_text(complaints, all_totals, "DRINKING_WATER_RATING",   max_rows=150)

            principal_elig  = build_eligible_schools(complaints, all_totals, "PRINCIPAL_SATISFACTION",  min_complaints=3)
            caretaker_elig  = build_eligible_schools(complaints, all_totals, "CARETAKER_SATISFACTION",  min_complaints=3)
            water_elig      = build_eligible_schools(complaints, all_totals, "DRINKING_WATER_RATING",   min_complaints=3)

            print(f"  Eligible schools — Principals : {len(principal_elig)}")
            print(f"  Eligible schools — Caretakers : {len(caretaker_elig)}")
            print(f"  Eligible schools — Water      : {len(water_elig)}")

            print("\nGenerating Metrics 2-8 via LLM...")
            print("Generating Metric 2 (Top Recurring Issues) ...")
            m2 = gen_metric2(agg_text, client)
            print("Generating Metric 3 (Challenges That Require Attention) ...")
            m3 = gen_metric3(agg_text, client)
            print("Generating Metric 4 (Red-Flagged Schools Chart) ...")
            m4 = gen_metric4(school_agg_text, client)
            print("Generating Metric 5 (Master Table of Actionable Comments) ...")
            m5 = gen_metric5(top_cmp_text, client)
            print("Generating Metric 6 (Red-Flagged Principals) ...")
            m6 = gen_metric6(principal_text, principal_elig, client)
            print("Generating Metric 7 (Red-Flagged Caretakers) ...")
            m7 = gen_metric7(caretaker_text, caretaker_elig, client)
            print("Generating Metric 8 (Red-Flagged Water Facility) ...")
            m8 = gen_metric8(water_text, water_elig, client)

            # Save to individual markdown files
            metric_files = {
                "metric2_output.md": m2,
                "metric3_output.md": m3,
                "metric4_output.md": m4,
                "metric5_output.md": m5,
                "metric6_output.md": m6,
                "metric7_output.md": m7,
                "metric8_output.md": m8,
            }
            for filename, content in metric_files.items():
                file_path = os.path.join(SCRIPT_DIR, filename)
                try:
                    with open(file_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    print(f"Saved {filename} → {file_path}")
                except Exception as e:
                    print(f"Failed to save {filename}: {e}")

            # Assemble and save the full consolidated report (Metrics 1-8)
            report_file = os.path.join(SCRIPT_DIR, "metrics_1_2_3_report.md")
            try:
                header_text = gen_report_header_from_filtered(output_csv)
                m1_table = gen_metric1_markdown_from_csv(output_csv)
                report_content = assemble_report(header_text, m1_table, m2, m3, m4, m5, m6, m7, m8)
                with open(report_file, "w", encoding="utf-8") as f:
                    f.write(report_content)
                print(f"\nSaved consolidated Metrics 1-8 Qualitative Report → {report_file} ({len(report_content):,} chars)")
            except Exception as e:
                print(f"Failed to save consolidated report: {e}")

        except Exception as e:
            print(f"Error executing Metrics 2-8 pipeline: {e}")

    if TOKEN_TRACKER:
        print("\n" + "="*80)
        print("=== TOKEN USAGE SUMMARY ===")
        print("="*80)
        print(f"{'Metric':<12} | {'Input Tokens':<12} | {'Output Tokens':<12} | {'Total Tokens':<12}")
        print("-" * 60)
        total_in = 0
        total_out = 0
        for name, tokens in sorted(TOKEN_TRACKER.items()):
            inp = tokens.get("input", 0)
            out = tokens.get("output", 0)
            tot = tokens.get("total", 0)
            total_in += inp
            total_out += out
            print(f"{name:<12} | {inp:<12,} | {out:<12,} | {tot:<12,}")
        print("-" * 60)
        print(f"{'Total':<12} | {total_in:<12,} | {total_out:<12,} | {total_in + total_out:<12,}")
        print("="*80 + "\n")

if __name__ == "__main__":
    main()
