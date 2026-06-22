#!/usr/bin/env python3
"""
generate_report_bedrock.py
--------------------------
Generates the 8-metric AP PTM qualitative report using Claude via AWS Bedrock.

Architecture:
  - Python pre-aggregates ALL counts, percentages, and unique-user totals
  - Claude receives only compact, pre-computed summaries — never raw rows
  - One focused Claude API call per metric (stays well within token limits)
  - Fully assembles a .md report with exactly 8 metrics

Backend:
  - Provider : AWS Bedrock  (boto3  bedrock-runtime)
  - Model    : global.anthropic.claude-sonnet-4-5-20250929-v1:0
  - Region   : ap-south-1
  - Auth     : AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY
               (set as env vars, or pass --access-key / --secret-key flags)
  - API      : bedrock-runtime.invoke_model  (Anthropic Messages format)

Usage:
  # Option A — environment variables (recommended)
  export AWS_ACCESS_KEY_ID=AKIAxxxxxxxxxxxxxxxx
  export AWS_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  python3 generate_report_bedrock.py [raw_csv] [output_md]

  # Option B — CLI flags
  python3 generate_report_bedrock.py raw.csv output.md \\
      --access-key AKIAxxxxxxxxxxxxxxxx \\
      --secret-key xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  Defaults:
    raw_csv    → ./raw.csv
    output_md  → ./qualitative_report_output.md
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

# ── Config ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RAW  = os.path.join(SCRIPT_DIR, "raw.csv")
DEFAULT_OUT  = os.path.join(SCRIPT_DIR, "qualitative_report_output.md")

# AWS Bedrock settings
BEDROCK_MODEL_ID  = "global.anthropic.claude-sonnet-4-5-20250929-v1:0"
BEDROCK_REGION    = "ap-south-1"
ANTHROPIC_VERSION = "bedrock-2023-05-31"   # required by Bedrock's Anthropic API
MAX_TOKENS        = 6000

# Retry settings for long-running metric calls
MAX_RETRIES       = 3
RETRY_DELAY_S     = 10   # seconds to wait between retries

# ── Junk filter ────────────────────────────────────────────────────────────────
JUNK_EXACT = {
    "ok", "okay", "good", "fine", "nice", "yes", "no", "na", "n/a", "none",
    "best", "great", "super", "excellent", "satisfied", "father", "mother",
    "parent", "sir", "madam", "reply", "next", "star", "quickly", "done",
    "noted", "thanks", "thank you", "hi", "hello", "all good", "very good",
    "very nice", "all ok", "all fine", "no issues", "no problem",
    "no complaints", "no issue", "no concerns", "nothing", "nill", "nil",
    "nothing to say", "everything is good", "everything is fine",
    "no comments", "nc", "not applicable",
}


def meaningful(text: str) -> bool:
    """Return True if the comment contains actionable content."""
    if not text:
        return False
    t = text.strip().lower()
    if t in JUNK_EXACT:
        return False
    
    # Check for important education/facility keywords or negation prefixes
    keywords = {"water", "food", "toilet", "menu", "bad", "poor", "dirty", "smell", "ro", "tap", "rice", "games", "play", "sport", "pet", "teacher"}
    has_keyword = any(kw in t for kw in keywords) or t.startswith("no ")
    
    # If it contains critical keywords or a negation prefix, allow down to 5 chars
    min_len = 5 if has_keyword else 10
    if len(t) < min_len:
        return False
        
    # Single word shorter than 18 chars
    if " " not in t and len(t) < 18:
        if not has_keyword:
            return False
            
    # Emoji / punctuation only
    if re.match(r"^[\W_]+$", t):
        return False
        
    # Repetitive characters (e.g. "aaaaaaa", "111111")
    cleaned = t.replace(" ", "")
    if len(cleaned) > 0 and len(set(cleaned)) < 3:
        return False
    return True


def safe_int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except Exception:
        return None


# ── Load & pre-aggregate ───────────────────────────────────────────────────────

def load_data(raw_csv: str) -> Tuple[List[dict], Dict[Tuple, int], Dict[Tuple, set]]:
    """
    Returns
    -------
    complaints : list of dicts
        Only Rating 1 or 2 rows with meaningful rating_reason text.
    all_totals : dict  {(school_name, district, category): total_row_count}
        Total feedback rows for EVERY (school, district, category) combination
        across all ratings — used as denominators for percentages.
    all_unique_users : dict  {(school_name, district, category): set(user_ids)}
        Unique user IDs across ALL ratings for each (school, district, category)
        combination — used as the correct Unique User Count in Metric 1.
    """
    complaints: List[dict] = []
    all_totals: Dict[Tuple, int] = defaultdict(int)
    # FIX: track unique users across ALL ratings (not just complaints)
    all_unique_users: Dict[Tuple, set] = defaultdict(set)
    raw_count   = 0
    skipped     = 0

    with open(raw_csv, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            raw_count += 1
            district    = (r.get("district") or "").strip()
            school      = (r.get("school_name") or "").strip()
            school_code = (r.get("school_code") or "").strip()
            category    = (r.get("category") or "").strip()
            rating      = safe_int(r.get("rating", ""))
            # actual column name in the CSV is rating_reason
            reason      = (r.get("rating_reason") or "").strip()
            user_id     = (r.get("user_id") or "").strip()

            if not district or not school or not category or rating is None:
                skipped += 1
                continue

            key = (school, district, category)

            # Count every row as a feedback (all ratings, all reasons)
            all_totals[key] += 1

            # Track unique users across ALL ratings for this (school, district, category)
            if user_id:
                all_unique_users[key].add(user_id)

            # Only collect rating 1 / 2 rows with meaningful text
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


def get_total(all_totals: dict, school: str, district: str,
              category: str = None) -> int:
    """Return total feedback count for a school (optionally filtered by category)."""
    if category:
        return all_totals.get((school, district, category), 0)
    return sum(
        v for (s, d, c), v in all_totals.items()
        if s == school and d == district
    )


def get_unique_users(all_unique_users: dict, school: str, district: str,
                     category: str) -> int:
    """Return count of unique users across ALL ratings for (school, district, category)."""
    return len(all_unique_users.get((school, district, category), set()))


# ── Pre-aggregation text builders ─────────────────────────────────────────────

def build_agg_text(complaints: List[dict], all_totals: dict,
                   all_unique_users: dict) -> str:
    """
    Metric 1 / 2 / 3 input: group by (district, school, category).
    Pre-computes all counts. Claude only reads complaint text to assign themes.

    Unique User Count = unique users across ALL ratings (not just complaints),
    sourced from all_unique_users so the denominator matches total feedbacks.
    """
    Group = Dict
    groups: Dict[Tuple, Group] = defaultdict(
        lambda: {"rows": [], "r1": 0}
    )
    for r in complaints:
        key = (r["district"], r["school"], r["category"])
        groups[key]["rows"].append(r)
        if r["rating"] == 1:
            groups[key]["r1"] += 1

    lines = [
        "District | School | Category | Total Feedbacks | "
        "Complaint Count | Unique Users | Rating 1 Count | Sample Complaints"
    ]

    # Sort by complaint count desc
    sorted_groups = sorted(groups.items(), key=lambda x: -len(x[1]["rows"]))
    for (dist, school, cat), d in sorted_groups:
        total = get_total(all_totals, school, dist, cat)
        cc    = len(d["rows"])
        pct   = round(cc / total * 100) if total else 0
        r1    = d["r1"]
        # FIX: use all-ratings unique user count for this (school, district, category)
        uniq  = get_unique_users(all_unique_users, school, dist, cat)
        # Up to 3 sample complaints, truncated
        samples = " | ".join(
            r["reason"][:120].replace("\n", " ") for r in d["rows"][:3]
        )
        lines.append(
            f"{dist} | {school[:55]} | {cat} | {total} | "
            f"{cc} ({pct}%) | {uniq} | {r1} | {samples}"
        )
    return "\n".join(lines)


def build_school_agg_text(complaints: List[dict], all_totals: dict) -> str:
    """
    Metric 4 input: school-level aggregation, fully pre-computed.
    Top 60 schools by complaint count.
    """
    school_data: Dict[Tuple, dict] = defaultdict(
        lambda: {"complaints": 0, "r1": 0, "categories": set()}
    )
    for r in complaints:
        key = (r["school"], r["district"])
        school_data[key]["complaints"] += 1
        school_data[key]["r1"] += 1 if r["rating"] == 1 else 0
        school_data[key]["categories"].add(r["category"])

    lines = [
        "District | School | Total Feedbacks | "
        "Complaint Count | Rating 1 Count | Categories with Complaints"
    ]
    for (school, dist), d in sorted(
        school_data.items(), key=lambda x: -x[1]["complaints"]
    )[:60]:
        total = get_total(all_totals, school, dist)
        pct   = round(d["complaints"] / total * 100) if total else 0
        lines.append(
            f"{dist} | {school[:55]} | {total} | "
            f"{d['complaints']} ({pct}%) | {d['r1']} | "
            f"{len(d['categories'])}"
        )
    return "\n".join(lines)


def build_category_text(complaints: List[dict], all_totals: dict,
                        category: str, max_rows: int = 120) -> str:
    """
    Metrics 6 / 7 / 8 input: filter to one category, include pre-computed
    school-level totals alongside each row.
    """
    cat_rows = [r for r in complaints if r["category"] == category]
    if not cat_rows:
        return ""

    # Pre-compute per-school stats once
    school_stats: Dict[Tuple, dict] = {}
    for r in cat_rows:
        key = (r["school"], r["district"])
        if key not in school_stats:
            total = get_total(all_totals, r["school"], r["district"], category)
            cc    = sum(
                1 for x in cat_rows
                if x["school"] == r["school"] and x["district"] == r["district"]
            )
            r1    = sum(
                1 for x in cat_rows
                if x["school"] == r["school"]
                and x["district"] == r["district"]
                and x["rating"] == 1
            )
            pct = round(cc / total * 100) if total else 0
            school_stats[key] = {"total": total, "cc": cc, "r1": r1, "pct": pct}

    lines = [
        "District | School | Total Feedbacks | "
        "Complaint Count | Rating 1 Count | Rating | Reason"
    ]
    for r in cat_rows[:max_rows]:
        key  = (r["school"], r["district"])
        s    = school_stats[key]
        reason = r["reason"][:200].replace("\n", " ")
        lines.append(
            f"{r['district']} | {r['school'][:55]} | {s['total']} | "
            f"{s['cc']} ({s['pct']}%) | {s['r1']} | {r['rating']} | {reason}"
        )
    return "\n".join(lines)


def build_top_complaints_text(complaints: List[dict], max_rows: int = 60) -> str:
    """
    Metric 5 input: most severe individual complaints.
    Rating 1 first, then Rating 2; within each group, longer reasons first.
    """
    sorted_rows = sorted(
        complaints,
        key=lambda r: (r["rating"], -len(r["reason"]))
    )
    lines = ["District | School | Category | Rating | Reason"]
    for r in sorted_rows[:max_rows]:
        reason = r["reason"][:200].replace("\n", " ")
        lines.append(
            f"{r['district']} | {r['school'][:55]} | "
            f"{r['category']} | {r['rating']} | {reason}"
        )
    return "\n".join(lines)


def build_eligible_schools(complaints: List[dict], all_totals: dict,
                           category: str,
                           min_complaints: int = 3) -> List[dict]:
    """
    Return schools that meet the red-flag threshold:
    complaint_count >= min_complaints  OR  any Rating 1.
    """
    school_stats: Dict[Tuple, dict] = defaultdict(
        lambda: {"count": 0, "r1": 0}
    )
    for r in complaints:
        if r["category"] != category:
            continue
        key = (r["district"], r["school"])
        school_stats[key]["count"] += 1
        if r["rating"] == 1:
            school_stats[key]["r1"] += 1

    eligible = []
    for (dist, school), d in sorted(
        school_stats.items(), key=lambda x: -x[1]["count"]
    ):
        if d["count"] >= min_complaints or d["r1"] >= 1:
            total = get_total(all_totals, school, dist, category)
            pct   = round(d["count"] / total * 100) if total else 0
            eligible.append(
                {
                    "district": dist,
                    "school":   school,
                    "total":    total,
                    "count":    d["count"],
                    "pct":      pct,
                    "r1":       d["r1"],
                }
            )
    return eligible


# ── Env loading & OpenRouter helpers ───────────────────────────────────────────


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


# Global token usage tracker
TOKEN_TRACKER = {}

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


# ── Bedrock client factory ─────────────────────────────────────────────────────

def make_bedrock_client(access_key: str, secret_key: str):
    """
    Returns a boto3 bedrock-runtime client authenticated with the given
    IAM credentials.  The policy must allow bedrock:InvokeModel on the
    configured BEDROCK_MODEL_ID inference profile.
    """
    import boto3  # noqa: PLC0415
    return boto3.client(
        service_name="bedrock-runtime",
        region_name=BEDROCK_REGION,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


# ── Claude call (via Bedrock invoke_model or OpenRouter) ────────────────────────

def claude(client, prompt: str, max_tokens: int = MAX_TOKENS,
           label: str = "") -> str:
    """
    Calls Claude on AWS Bedrock using the Anthropic Messages format,
    or OpenRouter if configured as the active provider.
    Retries up to MAX_RETRIES times on timeout or transient errors for Bedrock.
    """
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.environ.get("OPENROUTER_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct").strip()

    # Auto-detect if provider not specified
    if not provider:
        if openrouter_api_key:
            provider = "openrouter"
        elif os.environ.get("AWS_ACCESS_KEY_ID"):
            provider = "bedrock"

    if provider == "openrouter":
        if not openrouter_api_key:
            return "_OpenRouter API key not configured in environment or .env file._"
        try:
            return call_openrouter(prompt, openrouter_api_key, openrouter_model, label=label)
        except Exception as e:
            return f"_OpenRouter Error: {e}_"

    # Bedrock path
    if client is None:
        return "_Bedrock client not initialised — check credentials._"

    body = json.dumps(
        {
            "anthropic_version": ANTHROPIC_VERSION,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
    )

    for attempt in range(1, MAX_RETRIES + 1):
        t0 = time.time()
        try:
            resp = client.invoke_model(
                modelId=BEDROCK_MODEL_ID,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            # Bedrock streams the body as a StreamingBody — read & decode it
            raw  = resp["body"].read()
            data = json.loads(raw)

            elapsed = round(time.time() - t0, 1)
            usage = data.get("usage", {})
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            track_tokens(label, input_tokens, output_tokens)

            print(
                f"    ✓ {label} done in {elapsed}s "
                f"(input: {input_tokens:,}, output: {output_tokens:,}, total: {input_tokens + output_tokens:,} tokens)"
            )

            # Anthropic Messages format: content[0].text
            content = data.get("content", [])
            if content and content[0].get("type") == "text":
                return content[0]["text"].strip()
            # Fallback: join all text blocks
            return "\n".join(
                c["text"] for c in content if c.get("type") == "text"
            ).strip()

        except Exception as e:
            elapsed = round(time.time() - t0, 1)
            is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()
            if attempt < MAX_RETRIES and is_timeout:
                print(
                    f"    ⚠ {label} attempt {attempt}/{MAX_RETRIES} timed out "
                    f"after {elapsed}s — retrying in {RETRY_DELAY_S}s …"
                )
                time.sleep(RETRY_DELAY_S)
            else:
                print(f"    ✗ {label} failed (attempt {attempt}): {e}")
                return f"_Error generating this section: {e}_"

    return f"_Error generating this section: exceeded {MAX_RETRIES} retries._"


# ── Metric generators ──────────────────────────────────────────────────────────

def gen_header(complaints: List[dict], all_totals: dict) -> str:
    total_resp = sum(all_totals.values())
    districts  = len(set(r["district"] for r in complaints))
    schools    = len(set((r["school"], r["district"]) for r in complaints))
    r1_count   = sum(1 for r in complaints if r["rating"] == 1)
    r2_count   = sum(1 for r in complaints if r["rating"] == 2)
    return (
        "# AP PTM School Survey — Qualitative Complaint Analysis Report\n\n"
        "**Survey Scope:** AP Gurukulam & Government Welfare Schools, Andhra Pradesh  \n"
        "**Survey Type:** Parent-Teacher Meeting (PTM) Feedback  \n"
        f"**Total Responses Analysed:** {total_resp:,}  \n"
        f"**Meaningful Complaints (Rating 1–2):** {len(complaints):,} "
        f"(Rating 1: {r1_count:,} | Rating 2: {r2_count:,})  \n"
        f"**Districts with Complaints:** {districts}  \n"
        f"**Schools with Complaints:** {schools}  \n\n"
        "---"
    )


def gen_metric1(agg_text: str, total_resp: int, client) -> str:
    if not client:
        return "_Bedrock client not set_"
    prompt = f"""You are an education data analyst generating Metric 1 of an AP PTM qualitative report.

Below is pre-aggregated parent complaint data (Rating 1 and 2 only, with meaningful text).
All counts and percentages are PRE-COMPUTED — do NOT recalculate them.
Your only job: read the Sample Complaints text to assign a descriptive Issue Theme name per group.

COLUMNS: District | School | Category | Total Feedbacks | Complaint Count | Unique Users | Rating 1 Count | Sample Complaints

DATA:
{agg_text}

TASK — Generate Metric 1: Issues or Complaints Across Category, School, District.

RULES:
1. Assign an Issue Theme (3–6 words) based on the Sample Complaints text, e.g.:
   "No RO water / plant absent", "Toilets not cleaned daily", "No medical staff available"
2. Copy the pre-computed values directly into the table — do NOT change them.
3. Format Complaint Count exactly as it appears in the data: N (X%)  →  change to: N (X% of feedbacks)
4. Sort rows by Complaint Count descending (already sorted in the data above).
5. Show top 50 rows maximum.
6. Do NOT add a Risk Level or Severity column.

Output ONLY this markdown block (note + table, nothing else):

> ⚠️ All entries are driven by Rating 1 or 2 responses. Counts are pre-computed from {total_resp:,} total survey responses.

| District | School | Category | Issue Theme | Unique User Count | Total Feedbacks | Complaint Count |
|---|---|---|---|---|---|---|
"""
    return claude(client, prompt, label="Metric 1")


def gen_metric2(agg_text: str, client) -> str:
    if not client:
        return "_Bedrock client not set_"
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
    if not client:
        return "_Bedrock client not set_"
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
Include specific language directly from the Sample Complaints column.]

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
    if not client:
        return "_Bedrock client not set_"
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
    if not client:
        return "_Bedrock client not set_"
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


def gen_metric6(category_text: str, eligible: List[dict], client) -> str:
    if not client:
        return "_Bedrock client not set_"
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


def gen_metric7(category_text: str, eligible: List[dict], client) -> str:
    if not client:
        return "_Bedrock client not set_"
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


def gen_metric8(category_text: str, eligible: List[dict], client) -> str:
    if not client:
        return "_Bedrock client not set_"
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


# ── Assemble ───────────────────────────────────────────────────────────────────

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

*Report ends. Total metrics: 8. All findings are evidence-based on parent feedback
rated 1 or 2 from the AP PTM School Survey dataset.*
"""


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    # Load env variables from .env
    load_env()

    # ── CLI argument parsing ──────────────────────────────────────────────────
    parser = argparse.ArgumentParser(
        description="Generate AP PTM qualitative report via AWS Bedrock (Claude)."
    )
    parser.add_argument(
        "raw_csv", nargs="?", default=DEFAULT_RAW,
        help="Path to the raw survey CSV file (default: ./raw.csv)"
    )
    parser.add_argument(
        "output_md", nargs="?", default=DEFAULT_OUT,
        help="Output .md path (default: ./qualitative_report_output.md)"
    )
    parser.add_argument(
        "--access-key",
        default=os.environ.get("AWS_ACCESS_KEY_ID", ""),
        help="AWS Access Key ID (or set AWS_ACCESS_KEY_ID env var)"
    )
    parser.add_argument(
        "--secret-key",
        default=os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        help="AWS Secret Access Key (or set AWS_SECRET_ACCESS_KEY env var)"
    )
    args = parser.parse_args()

    raw_csv    = args.raw_csv
    out_md     = args.output_md
    access_key = args.access_key.strip()
    secret_key = args.secret_key.strip()

    # ── Step 1: Load & pre-aggregate ──────────────────────────────────────────
    print("Loading and pre-aggregating data …")
    complaints, all_totals, all_unique_users = load_data(raw_csv)
    total_resp = sum(all_totals.values())

    file_size_mb = os.path.getsize(raw_csv) / (1024 * 1024)
    print(f"  Input CSV file size              : {file_size_mb:.2f} MB")
    print(f"  Total response rows              : {total_resp:,}")
    print(f"  Rating 1/2 meaningful complaints : {len(complaints):,}")
    print(f"  (school, district, category) keys: {len(all_totals):,}")
    print(f"  Rating 1                         : {sum(1 for r in complaints if r['rating']==1):,}")
    print(f"  Rating 2                         : {sum(1 for r in complaints if r['rating']==2):,}")

    # ── Step 2: Bedrock client / Provider Setup ───────────────────────────────
    client = None
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    openrouter_api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    openrouter_model = os.environ.get("OPENROUTER_MODEL", "nvidia/llama-3.1-nemotron-70b-instruct").strip()

    # Detect and alert if model is a reranker
    if "rerank" in openrouter_model.lower():
        print(f"\n  [ERROR] OpenRouter model '{openrouter_model}' is a reranker model and cannot be used for completions.\n"
              f"  Please configure a valid chat completion model (e.g. google/gemini-2.5-flash or meta-llama/llama-3.3-70b-instruct:free)\n"
              f"  in your environment variables (export OPENROUTER_MODEL=...) or .env file.\n", file=sys.stderr)
        sys.exit(1)

    if not provider and openrouter_api_key:
        provider = "openrouter"

    if provider == "openrouter":
        print(
            f"  OpenRouter active ✓  "
            f"(model={os.environ.get('OPENROUTER_MODEL', 'nvidia/llama-3.1-nemotron-70b-instruct')})"
        )
    elif not access_key or not secret_key:
        print(
            "\n  ⚠  AWS credentials not found.\n"
            "     Set AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY env vars,\n"
            "     or pass --access-key / --secret-key flags.\n"
            "     Metric sections will contain placeholder text.\n"
        )
    else:
        try:
            import boto3  # noqa: PLC0415
            client = make_bedrock_client(access_key, secret_key)
            print(
                f"  Bedrock client ready ✓  "
                f"(region={BEDROCK_REGION}  model={BEDROCK_MODEL_ID})"
            )
        except ImportError:
            print(
                "  boto3 not installed — run:  pip install boto3\n"
                "  Metric sections will contain placeholder text."
            )
        except Exception as exc:
            print(f"  Bedrock client error: {exc}")

    # ── Step 3: Build all aggregation texts ONCE (reused across metrics) ──────
    print("\nPre-computing aggregation tables …")
    # FIX: pass all_unique_users to build_agg_text so Metric 1 gets correct counts
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

    # ── Step 4: Build report header ───────────────────────────────────────────
    header = gen_header(complaints, all_totals)

    # ── Step 5: Claude generates each metric via Bedrock ─────────────────────
    print("\nGenerating metrics via Claude on AWS Bedrock …")
    print("Generating Metric 1 …"); m1 = gen_metric1(agg_text, total_resp, client)
    print("Generating Metric 2 …"); m2 = gen_metric2(agg_text, client)
    print("Generating Metric 3 …"); m3 = gen_metric3(agg_text, client)
    print("Generating Metric 4 …"); m4 = gen_metric4(school_agg_text, client)
    print("Generating Metric 5 …"); m5 = gen_metric5(top_cmp_text, client)
    print("Generating Metric 6 …"); m6 = gen_metric6(principal_text, principal_elig, client)
    print("Generating Metric 7 …"); m7 = gen_metric7(caretaker_text, caretaker_elig, client)
    print("Generating Metric 8 …"); m8 = gen_metric8(water_text, water_elig, client)

    # ── Step 6: Assemble & save ───────────────────────────────────────────────
    report = assemble(header, m1, m2, m3, m4, m5, m6, m7, m8)
    os.makedirs(os.path.dirname(os.path.abspath(out_md)), exist_ok=True)
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"\nReport saved → {out_md}  ({len(report):,} chars)")

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