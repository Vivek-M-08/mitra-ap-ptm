import os
import logging
from datetime import datetime
import pandas as pd
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()

DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes", "t")

# ── Logging setup ────────────────────────────────────────────────────────────
log_filename = f"school_cleanup_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_filename, encoding="utf-8"),
        logging.StreamHandler(),          # keeps output in the terminal too
    ],
)
log = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────────────────────


def main():
    log.info("=========================================")
    log.info("  CLEANUP SCRIPT")
    log.info(f"  DRY_RUN: {DRY_RUN}")
    log.info(f"  Log file: {log_filename}")
    log.info("=========================================\n")

    DB_CONFIG = {
        "host":     os.getenv("DB_HOST", "localhost"),
        "port":     int(os.getenv("DB_PORT", 5432)),
        "database": os.getenv("DB_NAME", "mitra_ap_ptm"),
        "user":     os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", "postgres"),
    }

    csv_file = "schools_to_retain.csv"
    try:
        df = pd.read_csv(csv_file)
        valid_school_codes = df['school_code'].dropna().astype(str).tolist()
        log.info(f"[*] Loaded {len(valid_school_codes)} valid school codes from '{csv_file}'.")
    except Exception as e:
        log.error(f"[!] Error reading CSV file: {e}")
        return

    if not valid_school_codes:
        log.warning("[!] No school codes found in the CSV file. Aborting to prevent deleting everything.")
        return

    log.info("[*] Connecting to the database...")
    try:
        db_url = (
            f"postgresql://{DB_CONFIG['user']}:{DB_CONFIG['password']}"
            f"@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}"
        )
        engine = create_engine(db_url)

        with engine.begin() as conn:
            formatted_codes = ', '.join([f"'{code}'" for code in valid_school_codes])

            select_query = text(f"""
                SELECT MAX(school_name) as school_name, school_code, COUNT(*) as record_count
                FROM analytics
                WHERE school_code NOT IN ({formatted_codes})
                GROUP BY school_code
            """)

            log.info("[*] Identifying records to delete...")
            result = conn.execute(select_query)
            rows = result.fetchall()

            if not rows:
                log.info("\n[+] No orphaned records found. The 'analytics' table is clean.")
                return

            total_to_delete = sum(row[2] for row in rows)
            log.info(f"\n[!] Found {total_to_delete} total records to delete across {len(rows)} unmatched school codes.\n")

            log.info("--- DATA TO BE DELETED ---")
            log.info(f"{'School Name':<50} | {'School Code':<15} | {'Records':<7}")
            log.info("-" * 78)
            for row in rows:
                name = str(row[0])[:48] if row[0] else "Unknown"
                log.info(f"{name:<50} | {str(row[1]):<15} | {str(row[2]):<7}")
            log.info("-" * 78 + "\n")

            if DRY_RUN:
                log.info("[DRY RUN ACTIVE] The above records were NOT deleted. Set DRY_RUN=false in .env to delete.")
            else:
                log.info("[ACTIVE RUN] Deleting records...")
                delete_query = text(f"""
                    DELETE FROM analytics
                    WHERE school_code NOT IN ({formatted_codes})
                """)
                del_result = conn.execute(delete_query)
                log.info(f"[+] Successfully deleted {del_result.rowcount} records from the 'analytics' table.")

    except Exception as e:
        log.error(f"[!] Database operation failed: {e}")


if __name__ == "__main__":
    main()
