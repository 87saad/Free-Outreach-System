import os
import sqlite3
import pandas as pd
import streamlit as st

DEFAULT_DB_PATH = "outreach_pro_v3.db"


def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tracker_schema(conn: sqlite3.Connection) -> None:
    """
    Ensures required columns/tables exist safely without breaking existing schemas.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS campaign_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT NOT NULL,
            lead_email TEXT NOT NULL,
            lead_url TEXT DEFAULT '',
            first_name TEXT DEFAULT '',
            sent_status TEXT DEFAULT 'Sent',
            stage_status TEXT DEFAULT 'Initial Sent',
            reply_status TEXT DEFAULT 'No Reply',
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )

    # If an existing leads/outreach table is used, safely add columns if missing
    existing_tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    if "leads" in existing_tables:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        alter_statements = []
        if "campaign_name" not in cols:
            alter_statements.append("ALTER TABLE leads ADD COLUMN campaign_name TEXT DEFAULT ''")
        if "stage_status" not in cols:
            alter_statements.append("ALTER TABLE leads ADD COLUMN stage_status TEXT DEFAULT 'Initial Sent'")
        if "reply_status" not in cols:
            alter_statements.append("ALTER TABLE leads ADD COLUMN reply_status TEXT DEFAULT 'No Reply'")
        if "sent_status" not in cols:
            alter_statements.append("ALTER TABLE leads ADD COLUMN sent_status TEXT DEFAULT 'Sent'")
        for stmt in alter_statements:
            conn.execute(stmt)

    conn.commit()


def normalize_stage(raw_stage: str) -> str:
    val = (raw_stage or "").strip().lower()
    if "bounce" in val:
        return "Bounced"
    if "2" in val or "two" in val or "fu2" in val or "followup_2" in val:
        return "Follow-up 2 Sent"
    if "1" in val or "one" in val or "fu1" in val or "followup_1" in val:
        return "Follow-up 1 Sent"
    if "initial" in val or "sent" in val:
        return "Initial Sent"
    return raw_stage.strip() if raw_stage else "Initial Sent"


def normalize_reply(raw_reply: str, stage: str) -> str:
    if stage == "Bounced":
        return "Bounced (No Reply)"
    val = (raw_reply or "").strip().lower()
    if val in {"yes", "replied", "reply received", "true", "1", "received"}:
        return "Reply Received"
    return "No Reply"


def fetch_campaign_records(conn: sqlite3.Connection, campaign_query: str) -> pd.DataFrame:
    """
    Loads campaign records from `campaign_tracking` first, and falls back to `leads`
    if `campaign_tracking` has no rows for the entered campaign name.
    """
    clean_name = (campaign_query or "").strip()
    if not clean_name:
        return pd.DataFrame()

    rows = conn.execute(
        """
        SELECT
            lead_email AS "Recipient Email",
            lead_url AS "URL",
            first_name AS "First Name",
            sent_status AS "Send Status",
            stage_status AS "Stage / Bounce Status",
            reply_status AS "Reply Status",
            updated_at AS "Last Updated"
        FROM campaign_tracking
        WHERE LOWER(TRIM(campaign_name)) = LOWER(?)
        ORDER BY id DESC
        """,
        (clean_name,),
    ).fetchall()

    if rows:
        df = pd.DataFrame([dict(r) for r in rows])
    else:
        existing_tables = {
            r["name"]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "leads" not in existing_tables:
            return pd.DataFrame()

        cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(leads)").fetchall()
        }
        if "campaign_name" not in cols:
            return pd.DataFrame()

        email_col = "email" if "email" in cols else ("Email" if "Email" in cols else None)
        if not email_col:
            return pd.DataFrame()

        url_col = "' '"
        if "url" in cols:
            url_col = "COALESCE(url, '')"
        elif "URL" in cols:
            url_col = "COALESCE(URL, '')"

        name_col = "' '"
        if "first_name" in cols:
            name_col = "COALESCE(first_name, '')"
        elif "First_Name" in cols:
            name_col = "COALESCE(First_Name, '')"

        stage_expr = "COALESCE(stage_status, 'Initial Sent')" if "stage_status" in cols else "'Initial Sent'"
        reply_expr = "COALESCE(reply_status, 'No Reply')" if "reply_status" in cols else "'No Reply'"
        sent_expr = "COALESCE(sent_status, 'Sent')" if "sent_status" in cols else "'Sent'"

        lead_rows = conn.execute(
            f"""
            SELECT
                {email_col} AS "Recipient Email",
                {url_col} AS "URL",
                {name_col} AS "First Name",
                {sent_expr} AS "Send Status",
                {stage_expr} AS "Stage / Bounce Status",
                {reply_expr} AS "Reply Status",
                CURRENT_TIMESTAMP AS "Last Updated"
            FROM leads
            WHERE LOWER(TRIM(campaign_name)) = LOWER(?)
            """,
            (clean_name,),
        ).fetchall()

        if not lead_rows:
            return pd.DataFrame()
        df = pd.DataFrame([dict(r) for r in lead_rows])

    df["Stage / Bounce Status"] = df["Stage / Bounce Status"].apply(normalize_stage)
    df["Reply Status"] = [
        normalize_reply(r, s)
        for r, s in zip(df["Reply Status"], df["Stage / Bounce Status"])
    ]
    df["Send Status"] = [
        "Bounced" if s == "Bounced" else (v if v else "Sent")
        for v, s in zip(df["Send Status"], df["Stage / Bounce Status"])
    ]
    return df


def render_campaign_tracker_tab(db_path: str = DEFAULT_DB_PATH) -> None:
    st.subheader("Campaign Recipient, Stage & Reply Tracker")
    st.caption(
        "Kisi bhi campaign ka naam likh kar dekhein ke kin accounts/emails ko mail gayi, "
        "kaun sa stage/bounce status hai, aur reply aaya ya nahi."
    )

    conn = get_connection(db_path)
    try:
        ensure_tracker_schema(conn)

        campaign_input = st.text_input(
            "Enter Campaign Name",
            placeholder="e.g. Outreach_20_Nov",
            key="tracker_campaign_name_input",
        )

        # Optional quick status update helper so you can log replies, follow-ups, or bounces easily
        with st.expander("Update / Log Lead Stage, Reply or Bounce (Optional)", expanded=False):
            with st.form("tracker_update_form", clear_on_submit=True):
                f_camp = st.text_input(
                    "Campaign Name",
                    value=campaign_input.strip(),
                    placeholder="e.g. Outreach_20_Nov",
                )
                f_email = st.text_input("Recipient Email", placeholder="lead@example.com")
                c1, c2, c3 = st.columns(3)
                with c1:
                    f_stage = st.selectbox(
                        "Stage / Bounce Status",
                        [
                            "Initial Sent",
                            "Follow-up 1 Sent",
                            "Follow-up 2 Sent",
                            "Bounced",
                        ],
                    )
                with c2:
                    f_reply = st.selectbox(
                        "Reply Status",
                        ["No Reply", "Reply Received"],
                    )
                with c3:
                    f_url = st.text_input("Lead URL (Optional)", placeholder="https://example.com")

                submitted = st.form_submit_button("Save / Update Status")
                if submitted:
                    clean_c = f_camp.strip()
                    clean_e = f_email.strip()
                    if not clean_c or not clean_e:
                        st.warning("Campaign Name aur Recipient Email dono zaroori hain.")
                    else:
                        final_stage = normalize_stage(f_stage)
                        final_reply = normalize_reply(f_reply, final_stage)
                        final_send = "Bounced" if final_stage == "Bounced" else "Sent"

                        existing = conn.execute(
                            """
                            SELECT id FROM campaign_tracking
                            WHERE LOWER(TRIM(campaign_name)) = LOWER(?)
                              AND LOWER(TRIM(lead_email)) = LOWER(?)
                            """,
                            (clean_c, clean_e),
                        ).fetchone()

                        if existing:
                            conn.execute(
                                """
                                UPDATE campaign_tracking
                                SET lead_url = CASE WHEN ? != '' THEN ? ELSE lead_url END,
                                    sent_status = ?,
                                    stage_status = ?,
                                    reply_status = ?,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = ?
                                """,
                                (f_url.strip(), f_url.strip(), final_send, final_stage, final_reply, existing["id"]),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO campaign_tracking (
                                    campaign_name, lead_email, lead_url,
                                    sent_status, stage_status, reply_status
                                ) VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (clean_c, clean_e, f_url.strip(), final_send, final_stage, final_reply),
                            )
                        conn.commit()
                        st.success(f"Status save ho gaya: {clean_e} -> {final_stage} | {final_reply}")

        if not campaign_input.strip():
            st.info("Upar kisi campaign ka naam enter karein (maslan `Outreach_20_Nov`).")
            return

        df = fetch_campaign_records(conn, campaign_input)
        if df.empty:
            st.warning(f"'{campaign_input.strip()}' ke liye abhi koi record nahi mila.")
            return

        # Summary metrics
        total_sent = int((df["Send Status"] == "Sent").sum())
        total_replies = int((df["Reply Status"] == "Reply Received").sum())
        total_bounced = int((df["Stage / Bounce Status"] == "Bounced").sum())
        fu_count = int(
            df["Stage / Bounce Status"].isin(["Follow-up 1 Sent", "Follow-up 2 Sent"]).sum()
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Emails Sent", total_sent)
        m2.metric("Follow-ups Sent", fu_count)
        m3.metric("Replies Received", total_replies)
        m4.metric("Bounced", total_bounced)

        # Filters
        filter_col1, filter_col2 = st.columns(2)
        with filter_col1:
            stage_filter = st.multiselect(
                "Filter by Stage / Bounce",
                ["Initial Sent", "Follow-up 1 Sent", "Follow-up 2 Sent", "Bounced"],
                default=["Initial Sent", "Follow-up 1 Sent", "Follow-up 2 Sent", "Bounced"],
                key="tracker_stage_filter",
            )
        with filter_col2:
            reply_filter = st.multiselect(
                "Filter by Reply Status",
                ["Reply Received", "No Reply", "Bounced (No Reply)"],
                default=["Reply Received", "No Reply", "Bounced (No Reply)"],
                key="tracker_reply_filter",
            )

        filtered_df = df[
            df["Stage / Bounce Status"].isin(stage_filter)
            & df["Reply Status"].isin(reply_filter)
        ].copy()

        display_cols = [
            "Recipient Email",
            "Send Status",
            "Stage / Bounce Status",
            "Reply Status",
            "URL",
            "Last Updated",
        ]
        available_cols = [c for c in display_cols if c in filtered_df.columns]

        st.dataframe(
            filtered_df[available_cols],
            use_container_width=True,
            hide_index=True,
        )

        csv_bytes = filtered_df[available_cols].to_csv(index=False).encode("utf-8")
        safe_name = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in campaign_input.strip())
        st.download_button(
            label="Download Campaign Tracker CSV",
            data=csv_bytes,
            file_name=f"{safe_name}_tracker.csv",
            mime="text/csv",
        )
    finally:
        conn.close()