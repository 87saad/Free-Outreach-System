import datetime
import sqlite3
import pandas as pd


def ensure_dataset_schema(conn: sqlite3.Connection) -> None:
    """
    Har uploaded sheet/dataset ke liye alag dataset_key + email uniqueness
    banata hai taake ek hi sheet mein duplicate na ho, lekin do alag sheets
    mein same lead aapas mein collide na kare.
    """
    schema_sql_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='leads'"
    ).fetchone()
    if schema_sql_row and schema_sql_row[0] and "email text unique" in schema_sql_row[0].lower():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset_name TEXT DEFAULT 'Default_Dataset',
                url TEXT,
                email TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'Pending',
                added_date TEXT,
                UNIQUE(dataset_name, email)
            )
            """
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO leads_new (dataset_name, url, email, first_name, status, added_date)
            SELECT 'Default_Dataset', url, email, first_name, COALESCE(status, 'Pending'), COALESCE(added_date, '')
            FROM leads
            """
        )
        conn.execute("DROP TABLE leads")
        conn.execute("ALTER TABLE leads_new RENAME TO leads")
        conn.commit()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lead_datasets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_name TEXT UNIQUE NOT NULL,
            source_filename TEXT NOT NULL,
            uploaded_at TEXT NOT NULL
        )
        """
    )

    cols = {row[1] for row in conn.execute("PRAGMA table_info(leads)").fetchall()}
    if "dataset_name" not in cols:
        conn.execute("ALTER TABLE leads ADD COLUMN dataset_name TEXT DEFAULT 'Default_Dataset'")

    indexes = conn.execute("PRAGMA index_list(leads)").fetchall()
    for idx in indexes:
        idx_name = idx[1]
        is_unique = idx[2]
        if is_unique and not idx_name.startswith("sqlite_autoindex_"):
            try:
                conn.execute(f"DROP INDEX IF EXISTS {idx_name}")
            except Exception:
                pass

    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_leads_dataset_email
        ON leads (dataset_name, LOWER(TRIM(email)))
        """
    )

    conn.execute("UPDATE leads SET dataset_name = 'Default_Dataset' WHERE dataset_name IS NULL OR TRIM(dataset_name) = ''")
    conn.execute(
        """
        INSERT OR IGNORE INTO lead_datasets (dataset_name, source_filename, uploaded_at)
        VALUES ('Default_Dataset', 'legacy_or_default', ?)
        """,
        (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
    )
    conn.commit()


def make_unique_dataset_name(conn: sqlite3.Connection, base_name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in ("_", "-", " ") else "_" for c in (base_name or "").strip())
    cleaned = "_".join(cleaned.split()).strip("_") or "Sheet_Dataset"

    existing = {
        row[0].lower()
        for row in conn.execute("SELECT dataset_name FROM lead_datasets").fetchall()
    }
    if cleaned.lower() not in existing:
        return cleaned

    counter = 2
    while f"{cleaned}_v{counter}".lower() in existing:
        counter += 1
    return f"{cleaned}_v{counter}"


def save_sheet_as_separate_dataset(
    db_path: str,
    df: pd.DataFrame,
    source_filename: str,
    custom_dataset_name: str = "",
) -> dict:
    """
    Har upload ko ek alag dataset ke taur par save karta hai.
    Ussi sheet ke andar duplicate emails skip hoti hain, lekin doosri sheets ke sath clash nahi hota.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        ensure_dataset_schema(conn)
        raw_label = custom_dataset_name.strip() or source_filename.rsplit(".", 1)[0]
        dataset_name = make_unique_dataset_name(conn, raw_label)
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")

        conn.execute(
            """
            INSERT INTO lead_datasets (dataset_name, source_filename, uploaded_at)
            VALUES (?, ?, ?)
            """,
            (dataset_name, source_filename, now_str),
        )

        added_count = 0
        skipped_in_sheet = 0
        seen_emails_in_batch = set()

        for _, row in df.iterrows():
            email_val = str(row.get("Email", "")).strip()
            if not email_val or email_val.lower() == "nan":
                continue
            email_key = email_val.lower()
            if email_key in seen_emails_in_batch:
                skipped_in_sheet += 1
                continue
            seen_emails_in_batch.add(email_key)

            url_val = str(row.get("URL", "")).strip()
            if url_val.lower() == "nan":
                url_val = ""

            fn_val = str(row.get("First_Name", "")).strip()
            if fn_val.lower() in ("", "nan", "none"):
                fn_val = "there"

            cur = conn.execute(
                """
                INSERT OR IGNORE INTO leads (dataset_name, url, email, first_name, status, added_date)
                VALUES (?, ?, ?, ?, 'Pending', ?)
                """,
                (dataset_name, url_val, email_val, fn_val, today_str),
            )
            if cur.rowcount > 0:
                added_count += 1
            else:
                skipped_in_sheet += 1

        conn.commit()
        return {
            "dataset_name": dataset_name,
            "added": added_count,
            "skipped": skipped_in_sheet,
        }
    finally:
        conn.close()


def list_datasets_summary(db_path: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    try:
        ensure_dataset_schema(conn)
        query = """
            SELECT
                d.dataset_name AS "Dataset / Sheet",
                d.source_filename AS "File Name",
                COUNT(l.id) AS "Total Leads",
                SUM(CASE WHEN l.status = 'Pending' THEN 1 ELSE 0 END) AS "Pending",
                SUM(CASE WHEN l.status = 'Sent' THEN 1 ELSE 0 END) AS "Sent",
                SUM(CASE WHEN l.status = 'Failed' THEN 1 ELSE 0 END) AS "Failed",
                d.uploaded_at AS "Uploaded At"
            FROM lead_datasets d
            LEFT JOIN leads l ON d.dataset_name = l.dataset_name
            GROUP BY d.id, d.dataset_name, d.source_filename, d.uploaded_at
            ORDER BY d.id DESC
        """
        df = pd.read_sql(query, conn)
        for col in ["Total Leads", "Pending", "Sent", "Failed"]:
            if col in df.columns:
                df[col] = df[col].fillna(0).astype(int)
        return df
    finally:
        conn.close()