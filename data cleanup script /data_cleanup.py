import os
import re
import csv
import json
import psycopg2
import psycopg2.extras
import pandas as pd
import argparse
import multiprocessing as mp
import logging
from datetime import datetime
from dotenv import load_dotenv

# Import sentence-transformers
from sentence_transformers import SentenceTransformer, util

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 5432)),
    "database": os.getenv("DB_NAME", "mitra_ap_ptm"),
    "user":     os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "postgres"),
}

BOT_ID = 1

CATEGORIES = [
    "EDUCATION_QUALITY_RATING",
    "PRINCIPAL_SATISFACTION",
    "CARETAKER_SATISFACTION",
    "TEACHER_COMMUNICATION_RATING",
    "CLEANLINESS_RATING",
    "DRINKING_WATER_RATING",
    "HEALTHCARE_RATING",
    "DIET_MENU_RATING",
    "SAFETY_MEASURES_RATING",
    "EXTRACURRICULAR_RATING",
    "OVERALL_SATISFACTION_RATING",
]

# ── Smarter Logic Assets ──────────────────────────────────────────────────────
WORD_TO_NUM = {
    "one": 1, "first": 1,
    "two": 2, "second": 2,
    "three": 3, "third": 3,
    "four": 4, "fourth": 4,
    "five": 5, "fifth": 5, "last": 5
}

# Matches: 5, 5.0, 5th, 5/5, 4 / 5  — requires word boundaries so it won't
# fire on digits embedded in longer tokens like "12345" or "5because".
REGEX_RATING = re.compile(r'\b([1-5])(?:\s*(?:th|\.0|/\s*5))?\b', re.IGNORECASE)

RATING_ANCHORS = {
    1: ["terrible", "very bad", "completely unsatisfied", "horrible", "worst", "unacceptable"],
    2: ["bad", "poor", "unsatisfied", "disappointed", "not good"],
    3: ["okay", "average", "neutral", "fine", "acceptable", "mediocre"],
    4: ["good", "satisfied", "nice", "well done", "pleasant"],
    5: ["excellent", "very good", "great", "awesome", "perfect", "highly satisfied", "outstanding"]
}

# ── Clean-reason pattern ──────────────────────────────────────────────────────
# Strips ANY leading numeric prefix from a reason string, including:
#   • bare digits of any length  → "5 good", "12345 safe"
#   • digit + separator + digit  → "5/5", "4.0", "4-5"
#   • word numbers               → "one", "first", …
#   • trailing punctuation/space after the number
CLEAN_PATTERN = re.compile(
    r'^\s*'
    r'(?:'
      r'\d+'                                                            # any leading number
      r'(?:\s*[/.:)\-]\s*\d*)?'                                        # optional /5  .0  :5  -5
      r'|'
      r'(?:one|two|three|four|five|first|second|third|fourth|fifth)'   # word numbers
    r')'
    r'[\s\n.:)\-]*',                                                    # trailing whitespace / separators
    re.IGNORECASE
)

# ── Globals for Workers ───────────────────────────────────────────────────────
model = None
anchor_embs = None

def worker_init():
    """Initializes the model in each process worker."""
    global model, anchor_embs
    model = SentenceTransformer('sentence-transformers/all-MiniLM-L6-v2')
    anchor_embs = {
        rating: model.encode(texts, convert_to_tensor=True)
        for rating, texts in RATING_ANCHORS.items()
    }

# ── Transformation Logic ──────────────────────────────────────────────────────
def clean_reason(text, rating=None):
    """
    Strips any leading rating number / word-number from *text* and returns
    the remainder.  Returns None when nothing meaningful is left.

    Handles all of:
      "5 good"                   → "good"
      "5/5\\nWe are satisfied"   → "We are satisfied"
      "5because pure water"      → "because pure water"
      "12345 safe zoon"          → "safe zoon"
      "12345life time …"         → "life time …"
      "one good school"          → "good school"
      "5"  /  "55"  /  "F"      → None  (nothing meaningful left)
    """
    if not text:
        return None
    cleaned = CLEAN_PATTERN.sub('', str(text), count=1).strip()
    return cleaned if len(cleaned) > 1 else None


def extract_rating_and_reason(user_text):
    """
    Returns (rating, reason) from a user message.

    Priority order:
      1. Regex  – standalone digit 1-5 (with optional suffix like /5, th)
      2. Word   – number word (one/two/…/first/…/fifth)
      3. Semantic – cosine similarity against anchor phrases (min length 3,
                    threshold 0.30 to reduce spurious matches on short/
                    meaningless strings like single letters)
    """
    if not user_text:
        return None, None
    text = str(user_text).strip()
    if not text:
        return None, None

    # 1. Regex check (digit + optional suffix)
    match = REGEX_RATING.search(text)
    if match:
        rating = int(match.group(1))
        return rating, clean_reason(text, rating)

    # 2. Word-number check
    words = text.lower().split()
    for word in words:
        if word in WORD_TO_NUM:
            rating = WORD_TO_NUM[word]
            return rating, clean_reason(text, rating)

    # 3. Semantic fallback
    #    Guard: skip very short strings that are likely noise (single chars,
    #    pure punctuation, etc.) and raise the similarity threshold to 0.30
    #    to avoid spurious matches (was 0.25).
    if len(text) >= 3:
        text_emb = model.encode(text, convert_to_tensor=True)
        best_rating, highest_score = None, -1.0
        for rating, embs in anchor_embs.items():
            score = float(util.cos_sim(text_emb, embs).max())
            if score > highest_score:
                highest_score, best_rating = score, rating

        if highest_score > 0.30:
            # FIX: apply clean_reason here too so the reason never contains
            # a leading digit that the regex path failed to recognise
            # (e.g. "12345 safe", "5because pure water").
            return best_rating, clean_reason(text, best_rating)

    # Nothing matched
    return None, text if len(text) > 3 else None

# ── Database Layer ────────────────────────────────────────────────────────────
def get_connection():
    return psycopg2.connect(**DB_CONFIG)

def init_db(conn):
    """Ensures the analytics table exists before starting extraction."""
    query = """
    CREATE TABLE IF NOT EXISTS analytics (
        id SERIAL PRIMARY KEY,
        session_id VARCHAR(255) NOT NULL,
        user_id BIGINT,
        dist_code VARCHAR(50),
        district VARCHAR(255),
        school_code VARCHAR(50),
        school_name TEXT,
        category VARCHAR(100),
        rating INTEGER,
        rating_reason TEXT,
        original_message TEXT,
        translated_message TEXT,
        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_analytics_category ON analytics(category);
    CREATE INDEX IF NOT EXISTS idx_analytics_session ON analytics(session_id);
    """
    with conn.cursor() as cur:
        cur.execute(query)
        conn.commit()

def fetch_all_sessions(conn, limit=50000):
    """Fetches unique session IDs that haven't been processed yet."""
    query = """
        SELECT DISTINCT c.session 
        FROM chatbot_companychat c
        LEFT JOIN analytics a ON c.session = a.session_id
        WHERE c.stage = 'CLOSING'
          AND a.session_id IS NULL
        LIMIT %s;
    """
    with conn.cursor() as cur:
        cur.execute(query, (limit,))
        return [r[0] for r in cur.fetchall()]

def fetch_batch_messages(conn, session_ids):
    """Fetches all messages for a chunk of session IDs in one go."""
    placeholders = ",".join(["%s"] * len(CATEGORIES))
    query = f"""
        SELECT session, stage, sender_id, receiver_id, message, translated_message
        FROM chatbot_companychat
        WHERE session IN %s
          AND stage IN ({placeholders})
        ORDER BY session, id;
    """
    with conn.cursor() as cur:
        cur.execute(query, (tuple(session_ids), *CATEGORIES))
        return pd.DataFrame(cur.fetchall(), columns=[desc[0] for desc in cur.description])

def fetch_user_profiles(conn, user_ids):
    """Fetches and parses other_params from chatbot_profile for a batch of user IDs."""
    if not user_ids: return {}
    query = "SELECT id, other_params FROM chatbot_profile WHERE id IN %s"
    with conn.cursor() as cur:
        cur.execute(query, (tuple(user_ids),))
        profiles = {}
        for uid, params in cur.fetchall():
            if params and isinstance(params, dict):
                profiles[uid] = {
                    "dist_code":   params.get("distCode"),
                    "district":    params.get("district"),
                    "school_code": params.get("schoolCode"),
                    "school_name": params.get("schoolName"),
                }
            else:
                profiles[uid] = {k: None for k in ["dist_code", "district", "school_code", "school_name"]}
        return profiles

def save_ratings_batch(conn, data_rows):
    """High-speed bulk insertion into the normalized analytics table."""
    if not data_rows: return

    columns = [
        "session_id", "user_id",
        "dist_code", "district", "school_code", "school_name",
        "category", "rating", "rating_reason",
        "original_message", "translated_message"
    ]

    query = f"INSERT INTO analytics ({', '.join(columns)}) VALUES %s"

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, query, data_rows)
        conn.commit()

# ── Processing Core ───────────────────────────────────────────────────────────
def process_session_data(args):
    sid, df_session = args
    extracted = {}
    stage_messages = {}
    user_id = None

    stage_groups = df_session.groupby("stage", sort=False)
    for stage, group in stage_groups:
        raw_texts = []
        trans_texts = []
        for _, m in group.iterrows():
            # Identify User ID (not bot 1)
            if m["sender_id"] != BOT_ID:
                user_id = m["sender_id"]
            elif m["receiver_id"] != BOT_ID:
                user_id = m["receiver_id"]

            if m["sender_id"] != BOT_ID:
                msg = m.get("message")
                trans = m.get("translated_message")
                if pd.notna(msg) and str(msg).strip():
                    raw_texts.append(str(msg).strip())
                if pd.notna(trans) and str(trans).strip():
                    trans_texts.append(str(trans).strip())

        # Use translated messages for extraction if available, else raw
        analysis_text = " ".join(trans_texts) if trans_texts else " ".join(raw_texts)
        rating, reason = extract_rating_and_reason(analysis_text)

        extracted[stage] = rating
        extracted[f"{stage}_reason"] = reason
        stage_messages[stage] = {
            "original":   " ".join(raw_texts)   if raw_texts   else None,
            "translated": " ".join(trans_texts) if trans_texts else None,
        }

    return sid, extracted, user_id, stage_messages

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Extreme Speed Semantic Rating Extraction")
    parser.add_argument("session_id", type=str, nargs="?", help="Optional specific session ID to process")
    parser.add_argument("--limit", type=int, default=None, help="Total sessions to process")
    parser.add_argument("--batch", type=int, default=1000, help="Sessions per DB fetch batch")
    args = parser.parse_args()

    # Configure logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"data_cleanup_log_{timestamp}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(message)s",
        handlers=[
            logging.FileHandler(log_filename, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    logger = logging.getLogger(__name__)

    logger.info(f"🚀 Starting Extreme Speed Extraction (CPU Cores: {mp.cpu_count()})")
    conn = get_connection()

    if args.limit is None:
        logger.info("  No limit specified. Querying total session count...")
        with conn.cursor() as cur:
            cur.execute("SELECT count(DISTINCT session) FROM chatbot_companychat WHERE stage = 'CLOSING'")
            args.limit = cur.fetchone()[0]
        logger.info(f"  Calculated limit: {args.limit}")

    logger.info("🛠️ Checking/Initializing analytics database table...")
    init_db(conn)

    if args.session_id:
        session_ids = [args.session_id]
        logger.info(f"📋 Processing single session: {args.session_id}")
    else:
        logger.info("📋 Fetching pending session IDs...")
        session_ids = fetch_all_sessions(conn, limit=args.limit)
    total = len(session_ids)
    logger.info(f"  Found {total} sessions to process.")

    if total == 0:
        logger.info("✅ No new sessions to process.")
        return

    pool = mp.Pool(processes=mp.cpu_count(), initializer=worker_init)

    processed_count = 0
    for i in range(0, total, args.batch):
        batch_ids = session_ids[i:i + args.batch]
        logger.info(f"\n📦 Processing batch {i//args.batch + 1} ({len(batch_ids)} sessions)...")

        logger.info("  📥 Fetching message chunks from DB...")
        df_batch = fetch_batch_messages(conn, batch_ids)

        session_payloads = [
            (sid, group) for sid, group in df_batch.groupby("session", sort=False)
        ]

        logger.info(f"  ⚡ Extracting semantics with {mp.cpu_count()} processes...")
        results = pool.map(process_session_data, session_payloads)

        logger.info("  🔍 Fetching user profiles/location data...")
        uids = {r[2] for r in results if r[2] is not None}
        profiles = fetch_user_profiles(conn, list(uids))

        logger.info("  📤 Saving results to database...")
        final_db_rows = []
        for sid, ext_dict, uid, stage_msgs in results:
            p = profiles.get(uid, {k: None for k in ["dist_code", "district", "school_code", "school_name"]})

            for cat in CATEGORIES:
                rating = ext_dict.get(cat)
                reason = ext_dict.get(f"{cat}_reason")
                msgs = stage_msgs.get(cat, {"original": None, "translated": None})

                if rating is not None or reason is not None:
                    final_db_rows.append([
                        sid, uid, p["dist_code"], p["district"], p["school_code"], p["school_name"],
                        cat, rating, reason,
                        msgs["original"], msgs["translated"]
                    ])

        save_ratings_batch(conn, final_db_rows)

        if results:
            sid, js, uid, msgs = results[0]
            logger.info(f"  ✅ [Session {sid}] {json.dumps(js, ensure_ascii=False)[:300]}...")

        processed_count += len(batch_ids)
        logger.info(f"  📈 Progress: {processed_count}/{total} ({(processed_count/total)*100:.1f}%)")

    pool.close()
    pool.join()
    conn.close()
    logger.info(f"\n✨ Operation Complete. Processed {processed_count} sessions.")

if __name__ == "__main__":
    main()