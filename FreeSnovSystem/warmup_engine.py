import datetime
import random
import smtplib
import sqlite3
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import pandas as pd
import streamlit as st

DEFAULT_DB_PATH = "outreach_pro_v3.db"

WARMUP_TOPICS = [
    ("Weekly sync & check-in", "Hi {name},\n\nQuick warm-up check-in for today. Hope your day is going well!"),
    ("Quick note on workflow", "Hey {name},\n\nJust sending a brief warm-up note to keep our inbox activity healthy."),
    ("Catching up on tasks", "Hi {name},\n\nHope things are moving smoothly on your end today. Wanted to drop a quick note."),
    ("Afternoon status check", "Hello {name},\n\nJust a routine warm-up ping to keep deliverability and engagement steady."),
]


def ensure_warmup_schema(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS warmup_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            app_password TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS warmup_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            direction TEXT,
            from_email TEXT,
            to_email TEXT,
            subject_tag TEXT,
            status TEXT,
            sent_at TEXT
        )
        """
    )
    conn.commit()


def _send_single_warmup_email(
    from_email: str,
    from_pass: str,
    to_email: str,
    tag: str,
) -> tuple[bool, str, str]:
    clean_tag = (tag or "[WARMUP]").strip()
    if not clean_tag.startswith("["):
        clean_tag = f"[{clean_tag}]"

    topic_sub, topic_body = random.choice(WARMUP_TOPICS)
    to_name = to_email.split("@")[0].replace(".", " ").title()
    now_stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    subject = f"{clean_tag} {topic_sub} ({now_stamp})"
    body = f"{topic_body.format(name=to_name)}\n\nTag: {clean_tag}"

    try:
        msg = MIMEMultipart()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
            server.login(from_email.strip(), from_pass.replace(" ", "").strip())
            server.sendmail(from_email.strip(), to_email.strip(), msg.as_string())

        return True, subject, "Sent"
    except Exception as exc:
        return False, subject, f"Failed: {exc}"


def run_bidirectional_warmup(
    db_path: str,
    selected_senders: list[tuple[str, str]],
    selected_warmups: list[tuple[str, str]],
    rounds_per_pair: int = 1,
    tag: str = "[WARMUP]",
    delay_seconds: int = 3,
) -> dict:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    ensure_warmup_schema(conn)

    sent_ok = 0
    sent_fail = 0
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        for _ in range(max(1, int(rounds_per_pair))):
            # 1) Sender -> Warm-up
            for s_email, s_pass in selected_senders:
                for w_email, _ in selected_warmups:
                    if s_email.strip().lower() == w_email.strip().lower():
                        continue
                    ok, subj, status_msg = _send_single_warmup_email(s_email, s_pass, w_email, tag)
                    conn.execute(
                        """
                        INSERT INTO warmup_logs (direction, from_email, to_email, subject_tag, status, sent_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("Sender -> Warm-up", s_email, w_email, subj, status_msg, now_str),
                    )
                    if ok:
                        sent_ok += 1
                    else:
                        sent_fail += 1
                    conn.commit()
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)

            # 2) Warm-up -> Sender
            for w_email, w_pass in selected_warmups:
                for s_email, _ in selected_senders:
                    if w_email.strip().lower() == s_email.strip().lower():
                        continue
                    ok, subj, status_msg = _send_single_warmup_email(w_email, w_pass, s_email, tag)
                    conn.execute(
                        """
                        INSERT INTO warmup_logs (direction, from_email, to_email, subject_tag, status, sent_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        ("Warm-up -> Sender", w_email, s_email, subj, status_msg, now_str),
                    )
                    if ok:
                        sent_ok += 1
                    else:
                        sent_fail += 1
                    conn.commit()
                    if delay_seconds > 0:
                        time.sleep(delay_seconds)
    finally:
        conn.close()

    return {"sent_ok": sent_ok, "sent_fail": sent_fail}


def render_warmup_manager_page(db_path: str = DEFAULT_DB_PATH) -> None:
    st.title("🔥 Automated Email Warm-up Pool & Bidirectional Engine")
    st.markdown(
        "Yahan apne **Warm-up Accounts** add karein aur **Sender Accounts** ke sath "
        "dono taraf (**Sender ↔ Warm-up**) tagged warm-up emails bhejein."
    )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    ensure_warmup_schema(conn)

    with st.form("warmup_add_form"):
        w_email = st.text_input("Warm-up Gmail Address")
        w_pass = st.text_input("16-Digit App Password", type="password")
        if st.form_submit_button("➕ Add to Warm-up Pool"):
            if w_email and w_pass:
                conn.execute(
                    "INSERT OR IGNORE INTO warmup_accounts (email, app_password) VALUES (?, ?)",
                    (w_email.strip(), w_pass.replace(" ", "").strip()),
                )
                conn.commit()
                conn.close()
                st.success(f"Account '{w_email}' warm-up pool mein shamil ho gaya!")
                st.rerun()
            else:
                st.error("Email aur App Password dono darj karein.")

    st.divider()
    st.subheader("🔁 Run Bidirectional Warm-up (Sender ↔ Warm-up)")

    senders_df = pd.read_sql("SELECT email, app_password FROM sender_accounts", conn)
    warmups_df = pd.read_sql("SELECT id, email, app_password FROM warmup_accounts", conn)

    if senders_df.empty:
        st.warning("Pehle 'Campaign Launcher & Senders' mein kam az kam ek Sender Account add karein.")
    elif warmups_df.empty:
        st.info("Abhi tak koi warm-up account add nahi kiya gaya. Upar form se warm-up account add karein.")
    else:
        col_a, col_b = st.columns(2)
        with col_a:
            st.write("**Select Sender Accounts:**")
            chosen_senders = []
            for idx, r in senders_df.iterrows():
                if st.checkbox(f"📤 {r['email']}", value=True, key=f"wu_snd_{idx}"):
                    chosen_senders.append((r["email"], r["app_password"]))

        with col_b:
            st.write("**Select Warm-up Accounts:**")
            chosen_warmups = []
            for idx, r in warmups_df.iterrows():
                if st.checkbox(f"🔥 {r['email']}", value=True, key=f"wu_pool_{idx}"):
                    chosen_warmups.append((r["email"], r["app_password"]))

        c1, c2, c3 = st.columns(3)
        with c1:
            email_tag = st.text_input("Warm-up Tag", value="[WARMUP]", key="wu_tag_input")
        with c2:
            rounds = st.number_input("Rounds", min_value=1, max_value=5, value=1, key="wu_rounds_input")
        with c3:
            delay_sec = st.slider("Delay (Seconds)", 1, 15, 3, key="wu_delay_input")

        if st.button("🚀 Start Bidirectional Warm-up Now"):
            if not chosen_senders or not chosen_warmups:
                st.warning("Kam az kam ek Sender aur ek Warm-up account select karein.")
            else:
                res = run_bidirectional_warmup(
                    db_path=db_path,
                    selected_senders=chosen_senders,
                    selected_warmups=chosen_warmups,
                    rounds_per_pair=int(rounds),
                    tag=email_tag,
                    delay_seconds=int(delay_sec),
                )
                st.success(
                    f"Warm-up complete! Sent: {res['sent_ok']} | Failed: {res['sent_fail']}"
                )
                st.rerun()

    st.divider()
    st.subheader("📋 Saved Warm-up Accounts & Recent Warm-up Logs")
    if not warmups_df.empty:
        st.dataframe(warmups_df[["id", "email"]], use_container_width=True, hide_index=True)

    logs_df = pd.read_sql("SELECT * FROM warmup_logs ORDER BY id DESC LIMIT 50", conn)
    conn.close()
    if not logs_df.empty:
        st.dataframe(logs_df, use_container_width=True, hide_index=True)