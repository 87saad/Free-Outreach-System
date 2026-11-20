import datetime
import smtplib
import sqlite3
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from campaign_tracker_tab import ensure_tracker_schema, render_campaign_tracker_tab
from warmup_engine import render_warmup_manager_page

# ==========================================
# 1. PAGE CONFIGURATION & LIGHT THEME CSS
# ==========================================
st.set_page_config(
    page_title="Free Snov.io - Advanced Outreach Engine",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap');

    .stApp, body, [data-testid="stAppViewContainer"] {
        background-color: #FFFFFF !important;
        color: #1A1333 !important;
        font-family: 'Poppins', sans-serif !important;
    }
    [data-testid="stHeader"] {
        background-color: #FFFFFF !important;
        border-bottom: 1px solid #EAE4F2 !important;
    }

    [data-testid="stSidebar"] {
        background-color: #F7F5FC !important;
        border-right: 1px solid #EAE4F2 !important;
    }
    [data-testid="stSidebar"] span, [data-testid="stSidebar"] p, [data-testid="stSidebar"] label {
        color: #1A1333 !important;
        font-weight: 500;
    }
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] {
        gap: 8px;
    }
    [data-testid="stSidebar"] .stRadio label {
        background-color: #FFFFFF;
        padding: 10px 14px;
        border-radius: 8px;
        border: 1px solid #E2D9F3;
        box-shadow: 0 1px 3px rgba(108, 92, 231, 0.04);
        cursor: pointer;
    }
    [data-testid="stSidebar"] .stRadio label:hover {
        background-color: #EDE8F5;
        border-color: #6C5CE7;
    }

    h1, h2, h3, h4, h5, h6 {
        color: #1A1333 !important;
        font-weight: 600 !important;
    }
    p, label, span, div, .stMarkdown {
        color: #2D2640 !important;
    }

    [data-testid="stFileUploader"] {
        background-color: #F9F8FD !important;
        border: 2px dashed #6C5CE7 !important;
        border-radius: 10px !important;
        padding: 15px !important;
    }
    [data-testid="stFileUploader"] section {
        background-color: #F9F8FD !important;
    }

    div.stMetric {
        background-color: #F9F8FD;
        padding: 15px;
        border-radius: 10px;
        border: 1px solid #E2D9F3;
    }

    .stButton>button {
        background-color: #6C5CE7 !important;
        color: #FFFFFF !important;
        border-radius: 8px;
        padding: 0.45rem 1.1rem;
        border: none;
        font-weight: 500;
    }
    .stButton>button:hover {
        background-color: #5849BE !important;
    }

    .stTextInput input, .stTextArea textarea {
        background-color: #FFFFFF !important;
        color: #1A1333 !important;
        -webkit-text-fill-color: #1A1333 !important;
        border: 1px solid #D5CBEB !important;
        border-radius: 6px !important;
        caret-color: #6C5CE7 !important;
        font-family: 'Poppins', sans-serif !important;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. DATABASE ARCHITECTURE
# ==========================================
DB_NAME = "outreach_pro_v3.db"


def get_connection():
    return sqlite3.connect(DB_NAME, check_same_thread=False)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            email TEXT UNIQUE,
            first_name TEXT,
            status TEXT DEFAULT 'Pending',
            added_date TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS persistent_templates (
            step_key TEXT PRIMARY KEY,
            subject TEXT,
            body TEXT,
            updated_at TEXT
        )
    ''')

    default_steps = [
        ('Initial', 'Quick question regarding {URL}', 'Hi {First_Name},\n\nI came across {URL} and wanted to reach out...'),
        ('Followup_1', '', 'Hi {First_Name},\n\nJust floating this to the top of your inbox regarding {URL}.'),
        ('Followup_2', '', 'Hi {First_Name},\n\nOne last follow-up before I close my file on {URL}. Let me know your thoughts!')
    ]
    for step_key, def_sub, def_body in default_steps:
        cursor.execute('''
            INSERT OR IGNORE INTO persistent_templates (step_key, subject, body, updated_at)
            VALUES (?, ?, ?, ?)
        ''', (step_key, def_sub, def_body, datetime.datetime.now().strftime("%Y-%m-%d %H:%M")))

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sender_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            app_password TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS warmup_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            app_password TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS campaign_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT,
            day_of_week TEXT,
            run_date TEXT,
            run_time TEXT,
            senders_used TEXT,
            leads_targeted INTEGER,
            sent_count INTEGER,
            reply_count INTEGER DEFAULT 0,
            bounce_count INTEGER DEFAULT 0,
            status TEXT
        )
    ''')

    conn.commit()
    conn.close()


init_db()


def load_template(step_key):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT subject, body, updated_at FROM persistent_templates WHERE step_key = ?", (step_key,))
    row = cursor.fetchone()
    conn.close()
    return row if row else ("", "", "")


def save_template(step_key, subject, body):
    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute('''
        UPDATE persistent_templates
        SET subject = ?, body = ?, updated_at = ?
        WHERE step_key = ?
    ''', (subject, body, now_str, step_key))
    conn.commit()
    conn.close()


def render_draggable_chips():
    html_code = (
        '<div style="background-color: #F7F5FC; border: 1px solid #D5CBEB; border-radius: 10px; padding: 12px 16px; font-family: Poppins, sans-serif;">'
        '<div style="font-size: 13px; font-weight: 600; color: #3B2E5A; margin-bottom: 8px;">'
        'Drag & Drop Variables (In tags ko pakar kar neeche kisi bhi text box mein drop karein):'
        '</div>'
        '<div style="display: flex; gap: 10px; flex-wrap: wrap;">'
        '<span draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\', \'{First_Name}\')" '
        'style="background: #6C5CE7; color: white; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500; cursor: grab; user-select: none;">'
        '{First_Name}'
        '</span>'
        '<span draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\', \'{URL}\')" '
        'style="background: #6C5CE7; color: white; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500; cursor: grab; user-select: none;">'
        '{URL}'
        '</span>'
        '<span draggable="true" ondragstart="event.dataTransfer.setData(\'text/plain\', \'{Email}\')" '
        'style="background: #6C5CE7; color: white; padding: 6px 14px; border-radius: 20px; font-size: 13px; font-weight: 500; cursor: grab; user-select: none;">'
        '{Email}'
        '</span>'
        '</div>'
        '</div>'
    )
    components.html(html_code, height=95)


# ==========================================
# 3. SIDEBAR NAVIGATION
# ==========================================
st.sidebar.title("💜 Free Snov.io Pro")
st.sidebar.markdown("---")
menu = st.sidebar.radio("Navigation Menu", [
    "Dashboard & Overview",
    "Upload Leads (Smart Filter)",
    "Persistent Templates (Drag & Drop)",
    "Campaign Launcher & Senders",
    "Campaign History & Dataset",
    "Campaign Tracker (Stage & Replies)",
    "Email Warm-up Manager"
])

# ==========================================
# 4. PAGE 1: DASHBOARD & OVERVIEW
# ==========================================
if menu == "Dashboard & Overview":
    st.title("📊 Outreach & Campaign Control Center")
    st.markdown("Yahan aap apni tamam leads, campaigns aur daily performance ka live overview dekh sakte hain.")

    conn = get_connection()
    total_leads = pd.read_sql("SELECT COUNT(*) as c FROM leads", conn)['c'][0]
    pending_leads = pd.read_sql("SELECT COUNT(*) as c FROM leads WHERE status='Pending'", conn)['c'][0]
    total_campaigns = pd.read_sql("SELECT COUNT(*) as c FROM campaign_logs", conn)['c'][0]
    total_sent = pd.read_sql("SELECT COALESCE(SUM(sent_count), 0) as s FROM campaign_logs", conn)['s'][0]
    conn.close()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Leads in DB", total_leads)
    with col2:
        st.metric("Pending Leads", pending_leads)
    with col3:
        st.metric("Campaigns Run", total_campaigns)
    with col4:
        st.metric("Total Emails Dispatched", int(total_sent))

    st.divider()
    st.subheader("🟢 Active Template Preview (Currently Saved in Database)")
    init_sub, init_body, init_upd = load_template("Initial")
    st.info(f"**Active Initial Subject:** {init_sub}  |  *(Last Updated: {init_upd})*")

# ==========================================
# 5. PAGE 2: UPLOAD LEADS (SMART MULTI-COLUMN FILTER)
# ==========================================
elif menu == "Upload Leads (Smart Filter)":
    st.title("📁 Smart Lead Importer (Excel / CSV)")
    st.markdown("Aap ki Excel sheet mein chahe jitne marzi extra columns hon, yeh system khud ba khud sirf **URL**, **Email**, aur **First_Name** ko filter kar ke uthayega.")

    uploaded_file = st.file_uploader("Upload Excel (.xlsx) or CSV (.csv) File", type=["csv", "xlsx"])

    if uploaded_file is not None:
        try:
            if uploaded_file.name.endswith('.csv'):
                raw_df = pd.read_csv(uploaded_file)
            else:
                raw_df = pd.read_excel(uploaded_file)

            st.write(f"**Original File Columns Detected ({len(raw_df.columns)} columns):**", list(raw_df.columns))

            col_mapping = {}
            for col in raw_df.columns:
                clean_col = col.strip().lower()
                if clean_col == 'url':
                    col_mapping[col] = 'URL'
                elif clean_col == 'email':
                    col_mapping[col] = 'Email'
                elif clean_col in ['first_name', 'firstname', 'first name']:
                    col_mapping[col] = 'First_Name'

            required_targets = {'URL', 'Email', 'First_Name'}
            if required_targets.issubset(set(col_mapping.values())):
                filtered_df = raw_df.rename(columns=col_mapping)[['URL', 'Email', 'First_Name']].copy()
                filtered_df.dropna(subset=['Email'], inplace=True)

                st.success("✅ Smart Filter Applied! Sirf yeh 3 zaroori columns extract kiye gaye hain:")
                st.dataframe(filtered_df.head(10), use_container_width=True)

                if st.button("💾 Save Filtered Leads to Database"):
                    conn = get_connection()
                    cursor = conn.cursor()
                    added_count = 0
                    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
                    for _, row in filtered_df.iterrows():
                        try:
                            cursor.execute('''
                                INSERT OR IGNORE INTO leads (url, email, first_name, status, added_date)
                                VALUES (?, ?, ?, 'Pending', ?)
                            ''', (str(row['URL']).strip(), str(row['Email']).strip(), str(row['First_Name']).strip(), today_str))
                            if cursor.rowcount > 0:
                                added_count += 1
                        except Exception:
                            pass
                    conn.commit()
                    conn.close()
                    st.success(f"🎉 {added_count} nayi leads database mein save ho gayi hain! (Duplicates automatically skip kar diye gaye)")
            else:
                st.error("❌ Ghalati: Aap ki file mein kam az kam 'URL', 'Email', aur 'First_Name' ke columns mojood hone chahiye.")
        except Exception as e:
            st.error(f"File read karne mein masla aaya: {e}")

# ==========================================
# 6. PAGE 3: PERSISTENT TEMPLATES WITH DRAG & DROP
# ==========================================
elif menu == "Persistent Templates (Drag & Drop)":
    st.title("📝 Permanent Email Sequence Builder")
    st.markdown("Yahan aap apna content **ek baar set karke save** kar dein. Yeh database mein hamesha mehfooz rahega aur har campaign mein khud use hoga jab tak aap khud isay update na karein.")

    render_draggable_chips()

    init_sub, init_body, init_upd = load_template("Initial")
    f1_sub, f1_body, f1_upd = load_template("Followup_1")
    f2_sub, f2_body, f2_upd = load_template("Followup_2")

    if "init_body_state" not in st.session_state:
        st.session_state.init_body_state = init_body
    if "f1_body_state" not in st.session_state:
        st.session_state.f1_body_state = f1_body
    if "f2_body_state" not in st.session_state:
        st.session_state.f2_body_state = f2_body

    tab1, tab2, tab3 = st.tabs(["✉️ Step 1: Initial Email", "⏳ Step 2: Follow-up 1 (Reply)", "🔄 Step 3: Follow-up 2 (Reply)"])

    with tab1:
        st.caption(f"Last Saved in Database: {init_upd}")
        new_init_sub = st.text_input("Subject Line (Initial Email)", value=init_sub, key="ui_init_sub")

        st.write("**Quick Insert Variable into Body:**")
        c1, c2, c3, _ = st.columns([1, 1, 1, 4])
        if c1.button("+ {First_Name}", key="btn_i_fn"):
            st.session_state.init_body_state += " {First_Name}"
        if c2.button("+ {URL}", key="btn_i_url"):
            st.session_state.init_body_state += " {URL}"
        if c3.button("+ {Email}", key="btn_i_em"):
            st.session_state.init_body_state += " {Email}"

        new_init_body = st.text_area("Email Body (Initial)", value=st.session_state.init_body_state, height=180, key="ui_init_body")
        st.session_state.init_body_state = new_init_body

        if st.button("💾 Save / Update Initial Template"):
            save_template("Initial", new_init_sub, new_init_body)
            st.success("✅ Initial Template hamesha ke liye database mein save/update ho gaya hai!")
            st.rerun()

    with tab2:
        st.caption(f"Last Saved in Database: {f1_upd} | Note: Yeh email pehli email ke reply thread mein jayegi.")

        st.write("**Quick Insert Variable into Follow-up 1:**")
        c1, c2, c3, _ = st.columns([1, 1, 1, 4])
        if c1.button("+ {First_Name}", key="btn_f1_fn"):
            st.session_state.f1_body_state += " {First_Name}"
        if c2.button("+ {URL}", key="btn_f1_url"):
            st.session_state.f1_body_state += " {URL}"
        if c3.button("+ {Email}", key="btn_f1_em"):
            st.session_state.f1_body_state += " {Email}"

        new_f1_body = st.text_area("Follow-up 1 Body (Thread Reply)", value=st.session_state.f1_body_state, height=180, key="ui_f1_body")
        st.session_state.f1_body_state = new_f1_body

        if st.button("💾 Save / Update Follow-up 1"):
            save_template("Followup_1", "", new_f1_body)
            st.success("✅ Follow-up 1 hamesha ke liye database mein save/update ho gaya hai!")
            st.rerun()

    with tab3:
        st.caption(f"Last Saved in Database: {f2_upd} | Note: Yeh email bhi usi thread mein final reply ke taur par jayegi.")

        st.write("**Quick Insert Variable into Follow-up 2:**")
        c1, c2, c3, _ = st.columns([1, 1, 1, 4])
        if c1.button("+ {First_Name}", key="btn_f2_fn"):
            st.session_state.f2_body_state += " {First_Name}"
        if c2.button("+ {URL}", key="btn_f2_url"):
            st.session_state.f2_body_state += " {URL}"
        if c3.button("+ {Email}", key="btn_f2_em"):
            st.session_state.f2_body_state += " {Email}"

        new_f2_body = st.text_area("Follow-up 2 Body (Thread Reply)", value=st.session_state.f2_body_state, height=180, key="ui_f2_body")
        st.session_state.f2_body_state = new_f2_body

        if st.button("💾 Save / Update Follow-up 2"):
            save_template("Followup_2", "", new_f2_body)
            st.success("✅ Follow-up 2 hamesha ke liye database mein save/update ho gaya hai!")
            st.rerun()

# ==========================================
# 7. PAGE 4: CAMPAIGN LAUNCHER & MULTIPLE SENDERS
# ==========================================
elif menu == "Campaign Launcher & Senders":
    st.title("🚀 Campaign Launcher & Sender Accounts")
    st.markdown("Yahan se aap apne saved templates aur Gmail App Passwords ke zariye **real emails** dispatch kar sakte hain.")

    with st.expander("➕ Add New Sender Gmail Account", expanded=False):
        with st.form("add_sender_form"):
            s_email = st.text_input("Sender Gmail Address")
            s_pass = st.text_input("16-Digit Google App Password", type="password")
            if st.form_submit_button("Save Sender Account"):
                if s_email and s_pass:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "INSERT OR IGNORE INTO sender_accounts (email, app_password) VALUES (?, ?)",
                        (s_email.strip(), s_pass.replace(" ", "").strip())
                    )
                    conn.commit()
                    conn.close()
                    st.success(f"Account '{s_email}' save ho gaya!")
                    st.rerun()
                else:
                    st.error("Email aur App Password dono darj karein.")

    st.divider()

    st.subheader("🎯 Configure & Run Real Email Campaign")
    camp_name = st.text_input(
        "Campaign Name (e.g., SaaS Outreach Monday Batch)",
        value=f"Outreach_{datetime.datetime.now().strftime('%d_%b')}"
    )

    conn = get_connection()
    df_senders = pd.read_sql("SELECT id, email, app_password FROM sender_accounts", conn)
    pending_count = pd.read_sql("SELECT COUNT(*) as c FROM leads WHERE status='Pending'", conn)['c'][0]
    conn.close()

    if not df_senders.empty:
        st.write("**Select Sender Accounts (Checkboxes):**")
        selected_senders = []
        cols = st.columns(2)
        for idx, row in df_senders.iterrows():
            if cols[idx % 2].checkbox(f"📧 {row['email']}", value=True, key=f"chk_s_{row['id']}"):
                selected_senders.append((row['email'], row['app_password']))

        c_set1, c_set2 = st.columns(2)
        with c_set1:
            followup_days = st.slider("Follow-up Gap (Days)", 1, 10, 3)
        with c_set2:
            delay_seconds = st.slider("Delay Between Emails (Seconds - Spam Safety)", 2, 30, 5)

        st.info(f"📊 **Ready to Dispatch:** Database mein **{pending_count} Pending Leads** hain aur aap ka saved **Initial Template** real Gmail SMTP se send hoga.")

        if st.button("🚀 Launch Campaign Now"):
            if not selected_senders:
                st.warning("⚠️ Kam az kam ek sender account select karein.")
            elif pending_count == 0:
                st.warning("⚠️ Database mein koi Pending Lead mojood nahi hai. Pehle Excel file upload karein.")
            else:
                init_sub, init_body, _ = load_template("Initial")
                if not init_sub.strip() or not init_body.strip():
                    st.error("❌ Pehle 'Persistent Templates' mein ja kar Initial Subject aur Body save karein.")
                else:
                    conn = get_connection()
                    ensure_tracker_schema(conn)
                    pending_rows = conn.execute(
                        "SELECT id, url, email, first_name FROM leads WHERE status = 'Pending'"
                    ).fetchall()

                    progress_bar = st.progress(0)
                    status_box = st.empty()

                    sent_success = 0
                    bounce_failed = 0
                    total_leads_to_send = len(pending_rows)

                    for idx, (lead_id, p_url, p_email, p_fn) in enumerate(pending_rows):
                        sender_email, sender_pass = selected_senders[idx % len(selected_senders)]

                        safe_fn = (p_fn or "").strip() or "there"
                        safe_url = (p_url or "").strip()
                        safe_email = (p_email or "").strip()

                        final_sub = (
                            init_sub.replace("{First_Name}", safe_fn)
                            .replace("{URL}", safe_url)
                            .replace("{Email}", safe_email)
                        )
                        final_body = (
                            init_body.replace("{First_Name}", safe_fn)
                            .replace("{URL}", safe_url)
                            .replace("{Email}", safe_email)
                        )

                        status_box.info(f"📤 Sending ({idx + 1}/{total_leads_to_send}): **{safe_email}** via `{sender_email}`...")

                        try:
                            msg = MIMEMultipart()
                            msg["From"] = sender_email
                            msg["To"] = safe_email
                            msg["Subject"] = final_sub
                            msg.attach(MIMEText(final_body, "plain", "utf-8"))

                            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                                server.login(sender_email, sender_pass.replace(" ", ""))
                                server.sendmail(sender_email, safe_email, msg.as_string())

                            sent_success += 1
                            conn.execute("UPDATE leads SET status = 'Sent' WHERE id = ?", (lead_id,))
                            conn.execute(
                                '''
                                INSERT INTO campaign_tracking
                                (campaign_name, lead_email, lead_url, first_name, sent_status, stage_status, reply_status)
                                VALUES (?, ?, ?, ?, 'Sent', 'Initial Sent', 'No Reply')
                                ''',
                                (camp_name.strip(), safe_email, safe_url, safe_fn)
                            )
                        except Exception as send_err:
                            bounce_failed += 1
                            conn.execute("UPDATE leads SET status = 'Failed' WHERE id = ?", (lead_id,))
                            conn.execute(
                                '''
                                INSERT INTO campaign_tracking
                                (campaign_name, lead_email, lead_url, first_name, sent_status, stage_status, reply_status)
                                VALUES (?, ?, ?, ?, 'Bounced', 'Bounced', 'Bounced (No Reply)')
                                ''',
                                (camp_name.strip(), safe_email, safe_url, safe_fn)
                            )
                            st.warning(f"⚠️ `{safe_email}` par send fail hua ({sender_email}): {send_err}")

                        conn.commit()
                        progress_bar.progress((idx + 1) / total_leads_to_send)

                        if idx < total_leads_to_send - 1:
                            time.sleep(delay_seconds)

                    now = datetime.datetime.now()
                    day_name = now.strftime("%A")
                    date_str = now.strftime("%Y-%m-%d")
                    time_str = now.strftime("%I:%M %p")
                    used_emails_str = ", ".join([s[0] for s in selected_senders])

                    conn.execute(
                        '''
                        INSERT INTO campaign_logs
                        (campaign_name, day_of_week, run_date, run_time, senders_used, leads_targeted, sent_count, reply_count, bounce_count, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, 'Completed')
                        ''',
                        (camp_name.strip(), day_name, date_str, time_str, used_emails_str, total_leads_to_send, sent_success, bounce_failed)
                    )
                    conn.commit()
                    conn.close()

                    status_box.empty()
                    st.success(
                        f"✅ Campaign '{camp_name}' mukammal! **{sent_success}** real emails send ho gayin aur **{bounce_failed}** bounce/fail huin. "
                        f"Aap apne Gmail ke **Sent** folder aur **Campaign Tracker** mein verify kar sakte hain!"
                    )
    else:
        st.info("Upar diye gaye 'Add New Sender Gmail Account' section se pehle apna sender account add karein.")

# ==========================================
# 8. PAGE 5: CAMPAIGN HISTORY & ANALYTICS DATASET
# ==========================================
elif menu == "Campaign History & Dataset":
    st.title("📅 Day-by-Day Campaign Dataset & Results")
    st.markdown("Yahan aap apni tamam pichli campaigns ka mukammal record dekh sakte hain—kis din (Peer, Mangal, Budh) kaun si campaign chali aur uska kya result aaya.")

    conn = get_connection()
    df_logs = pd.read_sql("SELECT * FROM campaign_logs ORDER BY id DESC", conn)
    conn.close()

    if not df_logs.empty:
        days_list = ["All Days"] + list(df_logs['day_of_week'].unique())
        selected_day = st.selectbox("Filter by Day (Din ke hisab se filter karein):", days_list)

        if selected_day != "All Days":
            display_df = df_logs[df_logs['day_of_week'] == selected_day]
        else:
            display_df = df_logs

        st.dataframe(display_df, use_container_width=True)

        csv_data = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Campaign Dataset (CSV)",
            data=csv_data,
            file_name=f"campaign_dataset_{datetime.datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
    else:
        st.info("Abhi tak koi campaign run nahi ki gayi. Jab aap 'Campaign Launcher' se campaign chalayenge, toh uska din-ba-din record yahan khud ba khud banta jayega.")

# ==========================================
# PAGE: CAMPAIGN TRACKER (STAGE & REPLIES)
# ==========================================
elif menu == "Campaign Tracker (Stage & Replies)":
    render_campaign_tracker_tab(DB_NAME)

# ==========================================
# 9. PAGE 6: EMAIL WARM-UP MANAGER
# ==========================================
elif menu == "Email Warm-up Manager":
    render_warmup_manager_page(DB_NAME)