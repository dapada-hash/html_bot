import os
import re
import json
import random
import time
import threading
from datetime import datetime

import streamlit as st
from streamlit_autorefresh import st_autorefresh
from google import genai
import gspread
from google.oauth2.service_account import Credentials

# =================================================
# PAGE CONFIG
# =================================================
st.set_page_config(
    page_title="Certiport HTML & CSS Arena 2026",
    page_icon="🌐",
    layout="wide"
)
st.title("Certiport HTML & CSS Arena 🌐")
st.caption("Practice like a game: podiums, XP, streaks, challenges, and live competition.")

# =================================================
# SAFE SECRETS / ENV
# =================================================
def read_secret(key: str, default=None):
    try:
        return st.secrets.get(key, default)
    except Exception:
        return default

def read_env(key: str, default=None):
    return os.getenv(key, default)

# =================================================
# KEYS / SETTINGS
# =================================================
API_KEY = (
    read_secret("GEMINI_API_KEY")
    or read_secret("GOOGLE_API_KEY")
    or read_env("GEMINI_API_KEY")
    or read_env("GOOGLE_API_KEY")
    or ""
)

TEACHER_PIN = (
    read_secret("TEACHER_PIN")
    or read_env("TEACHER_PIN")
    or "1234"
)

LEADERBOARD_SHEET_ID = read_secret("LEADERBOARD_SHEET_ID", None)
GOOGLE_SHEETS_CREDS_JSON = read_secret("GOOGLE_SHEETS_CREDS_JSON", None)

MODEL = "gemini-2.5-flash"

BATCH_SIZE = 25
BANK_TARGET = 100
BANK_CALLS = max(1, BANK_TARGET // BATCH_SIZE)
ALL_DOMAINS_TARGET = 25
ALL_DOMAINS_BATCH_SIZE = 25

CHALLENGE_QUESTIONS = 5
XP_CORRECT = 10
XP_WRONG = 0
XP_WIN = 50
XP_LOSS = 0
XP_DRAW = 30

STREAK_BONUS_EVERY = 5
STREAK_BONUS_XP = 20

COOLDOWN_SECONDS = 1

# =================================================
# DOMAINS
# =================================================
DOMAINS = [
    "1. script, noscript, style, link, meta tags (encoding, keywords, viewport, description)",
    "2. DOCTYPE, html, head, body, proper syntax, closing tags, commonly used symbols",
    "3. Inline vs internal vs external CSS; precedence; browser default style",
    "4. CSS rule set syntax; selectors: class, id, element, pseudo-class, descendant",
    "5. Common tags: table/tr/th/td, h1-h6, p, br, hr, div, span, ul/ol/li",
    "6. Semantic tags: header, nav, section, article, aside, footer, details/summary, figure/caption",
    "7. Links: target, a href, bookmark, relative vs absolute, folder hierarchies, map/area",
    "8. Forms: attributes, action/method, submission, input types & restrictions, select/textarea/button/option/label",
    "9. Images: img and picture elements and attributes",
    "10. Media: video, audio, track, source, iframe",
    "11. Layout: float/relative/absolute/static/fixed; max-width/overflow/height/width/align/display; inline vs block; visibility; box model; margins",
    "12. Typography: font-family/color/style/size/weight/variant; link colors; text formatting/alignment/decoration/indentation/line-height/word-wrap/letter-spacing; padding",
    "13. Borders & backgrounds: border-color/style/width; background properties; colors",
    "14. Responsive: units (% px em vw vh); viewport & media queries; frameworks/templates; breakpoints; grids",
    "15. CSS best practices: reuse rules, comments, web-safe fonts, cross-platform, usability, separation of HTML/CSS",
    "16. Accessibility: text alternatives, color contrast, legibility, tab order, resizing, hierarchy, translate",
    "17. Troubleshooting: syntax errors, tag mismatch, cascading issues",
]

# =================================================
# FALLBACK QUESTIONS
# =================================================
FALLBACK_QUESTIONS = [
    {
        "question": "Which tag is used to link an external CSS file?",
        "A": "`<style>`",
        "B": "`<link>`",
        "C": "`<meta>`",
        "D": "`<script>`",
        "correct": "B",
        "explanation": "`<link rel=\"stylesheet\" href=\"...\">` connects external CSS."
    },
    {
        "question": "Which selector targets an element with `id=\"main\"`?",
        "A": "`.main`",
        "B": "`#main`",
        "C": "`main`",
        "D": "`*main`",
        "correct": "B",
        "explanation": "`#main` selects an element by id."
    },
    {
        "question": "Which is the correct DOCTYPE for HTML5?",
        "A": "`<!DOCTYPE html>`",
        "B": "`<DOCTYPE html5>`",
        "C": "`<!HTML5>`",
        "D": "`<!DOCTYPE HTML PUBLIC>`",
        "correct": "A",
        "explanation": "HTML5 uses `<!DOCTYPE html>`."
    },
]

# =================================================
# HELPERS
# =================================================
def now_utc():
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def parse_google_sheets_creds(raw_value):
    if not raw_value:
        return None

    if isinstance(raw_value, dict):
        return raw_value

    if isinstance(raw_value, str):
        cleaned = raw_value.strip()
        if cleaned.startswith("'''") and cleaned.endswith("'''"):
            cleaned = cleaned[3:-3].strip()
        elif cleaned.startswith('"""') and cleaned.endswith('"""'):
            cleaned = cleaned[3:-3].strip()
        return json.loads(cleaned)

    raise ValueError("GOOGLE_SHEETS_CREDS_JSON must be a JSON string or dict.")

def sheets_config_present() -> bool:
    return bool(
        LEADERBOARD_SHEET_ID
        and GOOGLE_SHEETS_CREDS_JSON
        and str(LEADERBOARD_SHEET_ID).strip()
        and str(GOOGLE_SHEETS_CREDS_JSON).strip()
    )

def sheets_enabled() -> bool:
    return bool(
        sheets_config_present()
        and st.session_state.get("google_sheets_ok", False)
    )

# =================================================
# GOOGLE SHEETS
# =================================================
@st.cache_resource
def get_gsheet_client():
    creds_dict = parse_google_sheets_creds(GOOGLE_SHEETS_CREDS_JSON)
    if not creds_dict:
        raise ValueError("Google Sheets credentials are missing.")

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet():
    gc = get_gsheet_client()
    return gc.open_by_key(LEADERBOARD_SHEET_ID)

def get_ws(tab_name: str):
    sh = get_sheet()
    return sh.worksheet(tab_name)

LB_HEADER = ["name", "period", "xp", "wins", "losses", "streak", "best_streak", "last_seen_utc"]
CH_HEADER = [
    "challenge_id", "created_utc",
    "challenger", "opponent",
    "domain", "difficulty",
    "status",
    "challenger_score", "opponent_score"
]

def ensure_sheet_tabs_and_headers():
    if not sheets_enabled():
        return

    sh = get_sheet()

    try:
        ws1 = sh.worksheet("leaderboard")
    except Exception:
        ws1 = sh.add_worksheet(title="leaderboard", rows=5000, cols=12)
    if ws1.row_values(1) != LB_HEADER:
        ws1.update("A1:H1", [LB_HEADER])

    try:
        ws2 = sh.worksheet("challenges")
    except Exception:
        ws2 = sh.add_worksheet(title="challenges", rows=5000, cols=12)
    if ws2.row_values(1) != CH_HEADER:
        ws2.update("A1:I1", [CH_HEADER])

# =================================================
# LEADERBOARD / CHALLENGES - GOOGLE SHEETS ONLY
# =================================================
@st.cache_data(ttl=5)
def lb_read_all():
    if not sheets_enabled():
        return []
    ensure_sheet_tabs_and_headers()
    return get_ws("leaderboard").get_all_records()

def lb_upsert_user(name: str, period: str):
    name = name.strip()
    if not name or not sheets_enabled():
        return

    ensure_sheet_tabs_and_headers()
    ws = get_ws("leaderboard")
    rows = ws.get_all_records()

    matches = []
    for idx, r in enumerate(rows, start=2):
        if str(r.get("name", "")).strip().lower() == name.lower():
            matches.append((idx, r))

    if matches:
        first_idx, first_row = matches[0]
        ws.update(f"B{first_idx}:H{first_idx}", [[
            period,
            safe_int(first_row.get("xp", 0)),
            safe_int(first_row.get("wins", 0)),
            safe_int(first_row.get("losses", 0)),
            safe_int(first_row.get("streak", 0)),
            safe_int(first_row.get("best_streak", 0)),
            now_utc()
        ]])
    else:
        ws.append_row([name, period, 0, 0, 0, 0, 0, now_utc()], value_input_option="USER_ENTERED")

    lb_read_all.clear()

def lb_get_user(name: str):
    name = name.strip().lower()
    rows = lb_read_all()

    matches = []
    for r in rows:
        if str(r.get("name", "")).strip().lower() == name:
            matches.append(r)

    if not matches:
        return None

    matches.sort(
        key=lambda r: (
            safe_int(r.get("xp", 0)),
            str(r.get("last_seen_utc", ""))
        ),
        reverse=True
    )
    return matches[0]

def lb_add_xp_and_streak(name: str, delta_xp: int, streak_delta: int, win_delta=0, loss_delta=0):
    name = name.strip()
    if not name or not sheets_enabled():
        raise RuntimeError("Google Sheets is not available.")

    ensure_sheet_tabs_and_headers()
    ws = get_ws("leaderboard")
    rows = ws.get_all_records()

    matches = []
    for idx, r in enumerate(rows, start=2):
        if str(r.get("name", "")).strip().lower() == name.lower():
            matches.append((idx, r))

    if not matches:
        lb_upsert_user(name, "Other")
        rows = ws.get_all_records()
        for idx, r in enumerate(rows, start=2):
            if str(r.get("name", "")).strip().lower() == name.lower():
                matches.append((idx, r))
                break

    first_idx, r = matches[0]

    xp = safe_int(r.get("xp", 0)) + int(delta_xp)
    wins = safe_int(r.get("wins", 0)) + int(win_delta)
    losses = safe_int(r.get("losses", 0)) + int(loss_delta)

    streak = safe_int(r.get("streak", 0))
    best = safe_int(r.get("best_streak", 0))

    if streak_delta == -999:
        streak = 0
    else:
        streak = max(0, streak + streak_delta)
        best = max(best, streak)

    ws.update(f"C{first_idx}:H{first_idx}", [[xp, wins, losses, streak, best, now_utc()]])
    lb_read_all.clear()

@st.cache_data(ttl=5)
def ch_read_all():
    if not sheets_enabled():
        return []
    ensure_sheet_tabs_and_headers()
    return get_ws("challenges").get_all_records()

def ch_write_row(row: list):
    if not sheets_enabled():
        raise RuntimeError("Google Sheets is not available.")
    ensure_sheet_tabs_and_headers()
    get_ws("challenges").append_row(row, value_input_option="USER_ENTERED")
    ch_read_all.clear()

def ch_update(cid: str, updates: dict):
    if not sheets_enabled():
        raise RuntimeError("Google Sheets is not available.")

    rows = ch_read_all()
    ws = get_ws("challenges")

    for idx, r in enumerate(rows, start=2):
        if str(r.get("challenge_id", "")) == cid:
            new_row = [
                cid,
                r.get("created_utc", ""),
                updates.get("challenger", r.get("challenger", "")),
                updates.get("opponent", r.get("opponent", "")),
                updates.get("domain", r.get("domain", "")),
                updates.get("difficulty", r.get("difficulty", "")),
                updates.get("status", r.get("status", "")),
                updates.get("challenger_score", r.get("challenger_score", "")),
                updates.get("opponent_score", r.get("opponent_score", "")),
            ]
            ws.update(f"A{idx}:I{idx}", [new_row])
            ch_read_all.clear()
            return

def ch_create(challenger: str, opponent: str, domain: str, difficulty: str):
    cid = f"CH{int(time.time() * 1000)}"
    ch_write_row([cid, now_utc(), challenger, opponent, domain, difficulty, "pending", "", ""])
    return cid

def ch_finalize_if_done(cid: str):
    for r in ch_read_all():
        if str(r.get("challenge_id", "")) == cid and r.get("status") == "done":
            c = r["challenger"]
            o = r["opponent"]
            cs = safe_int(r.get("challenger_score", 0))
            os_ = safe_int(r.get("opponent_score", 0))

            if cs > os_:
                lb_add_xp_and_streak(c, XP_WIN, 0, win_delta=1)
                lb_add_xp_and_streak(o, XP_LOSS, 0, loss_delta=1)
                return f"🏆 {c} wins! ({cs} vs {os_})"
            if os_ > cs:
                lb_add_xp_and_streak(o, XP_WIN, 0, win_delta=1)
                lb_add_xp_and_streak(c, XP_LOSS, 0, loss_delta=1)
                return f"🏆 {o} wins! ({os_} vs {cs})"

            lb_add_xp_and_streak(c, XP_DRAW, 0)
            lb_add_xp_and_streak(o, XP_DRAW, 0)
            return f"🤝 Draw! ({cs} vs {os_})"
    return None

# =================================================
# SHARED QUESTION BANK - PER DOMAIN
# =================================================
@st.cache_resource
def get_shared_bank():
    return {"lock": threading.Lock(), "bank": {}, "updated": {}}

QB = get_shared_bank()

def qkey(topic: str, difficulty: str):
    return (topic, difficulty)

def bank_size(topic: str, difficulty: str) -> int:
    with QB["lock"]:
        return len(QB["bank"].get(qkey(topic, difficulty), []))

def bank_last_updated(topic: str, difficulty: str):
    with QB["lock"]:
        return QB["updated"].get(qkey(topic, difficulty))

def add_to_bank(topic: str, difficulty: str, questions: list):
    with QB["lock"]:
        QB["bank"].setdefault(qkey(topic, difficulty), [])
        QB["bank"][qkey(topic, difficulty)].extend(questions)
        QB["updated"][qkey(topic, difficulty)] = now_utc()

def get_bank(topic: str, difficulty: str):
    with QB["lock"]:
        QB["bank"].setdefault(qkey(topic, difficulty), [])
        return QB["bank"][qkey(topic, difficulty)]

# =================================================
# GEMINI
# =================================================
def parse_batch(raw: str):
    questions = []
    chunks = raw.split("###")
    for chunk in chunks:
        try:
            q = re.search(r"QUESTION:\s*(.*?)(?=\nA\))", chunk, re.S).group(1)
            A = re.search(r"\nA\)\s*(.*)", chunk).group(1)
            B = re.search(r"\nB\)\s*(.*)", chunk).group(1)
            C = re.search(r"\nC\)\s*(.*)", chunk).group(1)
            D = re.search(r"\nD\)\s*(.*)", chunk).group(1)
            correct = re.search(r"CORRECT:\s*([ABCD])", chunk).group(1)
            explanation = re.search(r"EXPLANATION:\s*(.*)", chunk, re.S).group(1)
            questions.append({
                "question": q.strip(),
                "A": A.strip(),
                "B": B.strip(),
                "C": C.strip(),
                "D": D.strip(),
                "correct": correct.strip().upper(),
                "explanation": explanation.strip(),
            })
        except Exception:
            pass
    return questions

def fetch_questions_from_gemini(topic: str, difficulty: str, count: int):
    prompt = f"""
You are a Certiport HTML/CSS certification exam writer.
Create exactly {count} multiple choice questions.

DOMAIN: {topic}
DIFFICULTY: {difficulty}

Requirements:
- Focus strictly on this domain.
- Focus on Certiport-style HTML/CSS exam prep.
- Use realistic distractors.
- Include short HTML/CSS snippets when helpful.
- Return only multiple-choice questions.
- Use backticks around code when useful.

FORMAT (MUST MATCH EXACTLY):
- Each question separated by a line containing ONLY: ###
- Each question uses EXACT labels:

QUESTION: ...
A) ...
B) ...
C) ...
D) ...
CORRECT: A/B/C/D
EXPLANATION: ...

No extra text before the first QUESTION:
""".strip()

    if not API_KEY.strip():
        return [], "Gemini API key not set."

    last_err = None

    for _ in range(2):
        try:
            client = genai.Client(api_key=API_KEY)
            resp = client.models.generate_content(
                model=MODEL,
                contents=prompt
            )
            raw_text = getattr(resp, "text", "") or ""
            qs = parse_batch(raw_text)

            if qs:
                return qs, None

            last_err = "AI format error or empty response."
        except Exception as e:
            last_err = str(e)
            time.sleep(1)

    return [], last_err

# =================================================
# SESSION STATE
# =================================================
st.session_state.setdefault("score", 0)
st.session_state.setdefault("total_answered", 0)
st.session_state.setdefault("answered", False)
st.session_state.setdefault("question", None)
st.session_state.setdefault("answer_choice", None)
st.session_state.setdefault("next_allowed_time", 0.0)
st.session_state.setdefault("seen_by_domain", {})

st.session_state.setdefault("first_name", "")
st.session_state.setdefault("student_id", "")
st.session_state.setdefault("player_id", "")
st.session_state.setdefault("student_period", "Period 1")
st.session_state.setdefault("id_locked", False)

st.session_state.setdefault("challenge_mode", False)
st.session_state.setdefault("challenge_id", None)
st.session_state.setdefault("challenge_count", 0)
st.session_state.setdefault("challenge_correct", 0)
st.session_state.setdefault("active_domain", None)
st.session_state.setdefault("active_difficulty", None)

st.session_state.setdefault("is_teacher", False)
st.session_state.setdefault("is_generating", False)
st.session_state.setdefault("google_sheets_ok", False)
st.session_state.setdefault("google_sheets_error", "")

# =================================================
# GOOGLE SHEETS STATUS CHECK
# =================================================
try:
    if sheets_config_present():
        gc = get_gsheet_client()
        gc.open_by_key(LEADERBOARD_SHEET_ID)
        st.session_state["google_sheets_ok"] = True
        st.session_state["google_sheets_error"] = ""
    else:
        st.session_state["google_sheets_ok"] = False
        st.session_state["google_sheets_error"] = "Missing LEADERBOARD_SHEET_ID or GOOGLE_SHEETS_CREDS_JSON in secrets."
except Exception as e:
    st.session_state["google_sheets_ok"] = False
    st.session_state["google_sheets_error"] = str(e)

# =================================================
# AUTO REFRESH
# =================================================
live_refresh = st.sidebar.checkbox("Live leaderboard refresh", value=True)
refresh_seconds = st.sidebar.selectbox("Refresh speed", [5, 10, 15], index=1)

if live_refresh and not st.session_state.get("is_generating", False) and not st.session_state.get("is_teacher", False):
    st_autorefresh(interval=refresh_seconds * 1000, limit=None, key="live_refresh")
    st.sidebar.caption(f"🔄 Refreshing every {refresh_seconds} seconds")
elif st.session_state.get("is_generating", False):
    st.sidebar.caption("⏸ Auto-refresh paused during question generation")
elif st.session_state.get("is_teacher", False):
    st.sidebar.caption("⏸ Auto-refresh paused in Teacher mode")

# =================================================
# LOGIN
# =================================================
st.sidebar.header("Student Login (FirstName-ID)")

st.session_state.first_name = st.sidebar.text_input(
    "First Name",
    value=st.session_state.first_name,
    disabled=st.session_state.id_locked
)
st.session_state.student_id = st.sidebar.text_input(
    "Student ID (numbers only)",
    value=st.session_state.student_id,
    disabled=st.session_state.id_locked
)

player_id = ""
if st.session_state.first_name.strip() and st.session_state.student_id.strip():
    if not st.session_state.student_id.strip().isdigit():
        st.sidebar.error("Student ID must be numbers only.")
    else:
        player_id = f"{st.session_state.first_name.strip()}-{st.session_state.student_id.strip()}"
        st.sidebar.success(f"✅ Player ID: {player_id}")

st.session_state.player_id = player_id

st.session_state.student_period = st.sidebar.selectbox(
    "Class / Period",
    ["Period 1", "Period 2", "Period 3", "Period 4", "Period 5", "Period 6", "Other"],
    index=["Period 1", "Period 2", "Period 3", "Period 4", "Period 5", "Period 6", "Other"].index(st.session_state.student_period)
    if st.session_state.student_period in ["Period 1", "Period 2", "Period 3", "Period 4", "Period 5", "Period 6", "Other"]
    else 0
)

if not st.session_state.player_id:
    st.warning("Enter First Name + numeric Student ID to start.")
    st.stop()

if not sheets_enabled():
    st.warning("Google Sheets is not available. Fix the sheet connection first.")
    st.code(st.session_state.get("google_sheets_error", "Unknown Google Sheets error"))
    st.stop()

try:
    lb_upsert_user(st.session_state.player_id, st.session_state.student_period)
except Exception as e:
    st.warning("Google Sheets is temporarily unavailable.")
    st.code(str(e))
    st.stop()

st.sidebar.divider()
st.sidebar.header("Quiz Settings")
topic = st.sidebar.selectbox("Domain", DOMAINS)
difficulty = st.sidebar.selectbox("Difficulty", ["Easy", "Medium", "Hard"])
st.sidebar.caption(f"Shared bank for this domain: {bank_size(topic, difficulty)}")

lu = bank_last_updated(topic, difficulty)
if lu:
    st.sidebar.caption(f"Last teacher refill (UTC): {lu}")

st.sidebar.success("✅ Persistent mode: Google Sheets")

# =================================================
# LEADERBOARD
# =================================================
lb = lb_read_all()
lb_sorted = sorted(lb, key=lambda r: safe_int(r.get("xp", 0)), reverse=True)

st.markdown("## 🏆 Live Classroom Leaderboard")
st.caption("Global leaderboard across all domains.")

pod = lb_sorted[:3] + [{}] * max(0, 3 - len(lb_sorted))

col_left, col_mid, col_right = st.columns([1, 1.2, 1])

with col_left:
    if pod[1].get("name"):
        st.markdown(
            f"""
            <div style="text-align:center;background: linear-gradient(180deg, #e5e7eb, #cbd5e1);padding: 18px;border-radius: 18px;border: 2px solid #94a3b8;box-shadow: 0 6px 14px rgba(0,0,0,0.12);min-height: 220px;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:48px;">🥈</div>
                <div style="font-size:26px; font-weight:800; margin-top:4px;">#2</div>
                <div style="font-size:22px; font-weight:700; margin-top:8px;">{pod[1]["name"]}</div>
                <div style="font-size:20px; margin-top:8px;">{safe_int(pod[1].get("xp"))} XP</div>
                <div style="font-size:16px; margin-top:8px;">🔥 Best streak: {safe_int(pod[1].get("best_streak"))}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

with col_mid:
    if pod[0].get("name"):
        st.markdown(
            f"""
            <div style="text-align:center;background: linear-gradient(180deg, #fde68a, #fbbf24);padding: 22px;border-radius: 20px;border: 3px solid #d97706;box-shadow: 0 10px 24px rgba(0,0,0,0.18);min-height: 260px;display:flex;flex-direction:column;justify-content:center;transform: scale(1.03);">
                <div style="font-size:60px;">🥇</div>
                <div style="font-size:30px; font-weight:900; margin-top:4px;">#1</div>
                <div style="font-size:26px; font-weight:800; margin-top:10px;">{pod[0]["name"]}</div>
                <div style="font-size:24px; font-weight:700; margin-top:10px;">{safe_int(pod[0].get("xp"))} XP</div>
                <div style="font-size:18px; margin-top:10px;">🔥 Best streak: {safe_int(pod[0].get("best_streak"))}</div>
                <div style="font-size:16px; margin-top:10px;">👑 Current leader</div>
            </div>
            """,
            unsafe_allow_html=True
        )

with col_right:
    if pod[2].get("name"):
        st.markdown(
            f"""
            <div style="text-align:center;background: linear-gradient(180deg, #d6a779, #b87333);padding: 18px;border-radius: 18px;border: 2px solid #92400e;box-shadow: 0 6px 14px rgba(0,0,0,0.12);min-height: 220px;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:48px;">🥉</div>
                <div style="font-size:26px; font-weight:800; margin-top:4px;">#3</div>
                <div style="font-size:22px; font-weight:700; margin-top:8px;">{pod[2]["name"]}</div>
                <div style="font-size:20px; margin-top:8px;">{safe_int(pod[2].get("xp"))} XP</div>
                <div style="font-size:16px; margin-top:8px;">🔥 Best streak: {safe_int(pod[2].get("best_streak"))}</div>
            </div>
            """,
            unsafe_allow_html=True
        )

st.markdown("<br>", unsafe_allow_html=True)

top_rows = []
for i, r in enumerate(lb_sorted[:25], start=1):
    top_rows.append({
        "Rank": i,
        "Name": r.get("name", ""),
        "Period": r.get("period", ""),
        "XP": safe_int(r.get("xp", 0)),
        "🔥 Streak": safe_int(r.get("streak", 0)),
        "🏅 Best": safe_int(r.get("best_streak", 0)),
        "W": safe_int(r.get("wins", 0)),
        "L": safe_int(r.get("losses", 0)),
    })

st.dataframe(top_rows, use_container_width=True, height=340)

# =================================================
# CHALLENGE DIRECTLY FROM LEADERBOARD
# =================================================
st.markdown("### ⚔️ Challenge Directly From the Leaderboard")

for i, r in enumerate(lb_sorted[:10], start=1):
    medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
    row_cols = st.columns([1, 4, 2, 2, 2])

    with row_cols[0]:
        st.markdown(f"**{medal}**")
    with row_cols[1]:
        st.markdown(f"**{r.get('name', '-')}**")
    with row_cols[2]:
        st.markdown(f"{safe_int(r.get('xp', 0))} XP")
    with row_cols[3]:
        st.markdown(f"🔥 {safe_int(r.get('streak', 0))}")
    with row_cols[4]:
        opp_name = r.get("name", "")
        if opp_name and opp_name.lower() != st.session_state.player_id.lower():
            if st.button("⚔️ Challenge", key=f"challenge_{opp_name}_{i}"):
                ch_create(st.session_state.player_id, opp_name, topic, difficulty)
                st.success(f"Challenge sent to {opp_name}!")

# =================================================
# PERIOD VS PERIOD
# =================================================
st.markdown("## 🏫 Period vs Period Competition")

period_totals = {}
for r in lb:
    p = r.get("period", "Other")
    period_totals[p] = period_totals.get(p, 0) + safe_int(r.get("xp", 0))

period_rows = [{"Period": k, "Total XP": v} for k, v in sorted(period_totals.items(), key=lambda x: x[1], reverse=True)]
st.dataframe(period_rows, use_container_width=True, height=220)

st.divider()

# =================================================
# STUDENT STATUS
# =================================================
me = lb_get_user(st.session_state.player_id) or {}

my_xp = safe_int(me.get("xp", 0))
my_streak = safe_int(me.get("streak", 0))
my_best = safe_int(me.get("best_streak", 0))

st.markdown("## 🎮 Your Progress")
c1, c2, c3 = st.columns(3)
c1.metric("XP", my_xp)
c2.metric("🔥 Current Streak", my_streak)
c3.metric("🏅 Best Streak", my_best)

goal = 1000
st.progress(min(1.0, my_xp / goal))
st.caption(f"Race to {goal} XP")

st.divider()

# =================================================
# CHALLENGE INBOX / OUTBOX
# =================================================
st.markdown("## 📩 Challenges")

ch_all = ch_read_all()

incoming = [
    c for c in ch_all
    if str(c.get("opponent", "")).lower() == st.session_state.player_id.lower()
    and c.get("status") in ("pending", "accepted")
]

outgoing = [
    c for c in ch_all
    if str(c.get("challenger", "")).lower() == st.session_state.player_id.lower()
    and c.get("status") in ("pending", "accepted")
]

left, right = st.columns(2)

with left:
    st.markdown("### Incoming")
    if not incoming:
        st.caption("No incoming challenges.")
    else:
        for c in incoming[:10]:
            st.write(f"**{c['challenger']}** challenged you • **{c['domain']}** ({c['difficulty']}) • `{c['status']}`")
            if c["status"] == "pending":
                if st.button(f"Accept {c['challenge_id']}"):
                    ch_update(c["challenge_id"], {"status": "accepted"})
                    st.session_state.challenge_mode = True
                    st.session_state.challenge_id = c["challenge_id"]
                    st.session_state.challenge_count = 0
                    st.session_state.challenge_correct = 0
                    st.session_state.active_domain = c["domain"]
                    st.session_state.active_difficulty = c["difficulty"]
                    st.success("Challenge accepted!")

with right:
    st.markdown("### Sent")
    if not outgoing:
        st.caption("No active sent challenges.")
    else:
        for c in outgoing[:10]:
            st.write(f"To **{c['opponent']}** • **{c['domain']}** ({c['difficulty']}) • `{c['status']}`")
            if st.button(f"Start {c['challenge_id']}"):
                st.session_state.challenge_mode = True
                st.session_state.challenge_id = c["challenge_id"]
                st.session_state.challenge_count = 0
                st.session_state.challenge_correct = 0
                st.session_state.active_domain = c["domain"]
                st.session_state.active_difficulty = c["difficulty"]
                st.success("Challenge attempt started!")

st.divider()

# =================================================
# TEACHER PANEL
# =================================================
with st.sidebar.expander("🔒 Teacher Panel"):
    pin_input = st.text_input("Teacher PIN", type="password")
    tc1, tc2 = st.columns(2)

    with tc1:
        if st.button("Unlock Teacher"):
            st.session_state.is_teacher = (pin_input == str(TEACHER_PIN))
            st.success("Teacher mode ON ✅" if st.session_state.is_teacher else "Wrong PIN ❌")

    with tc2:
        if st.button("Lock Teacher"):
            st.session_state.is_teacher = False
            st.info("Teacher mode OFF")

    if st.session_state.is_teacher:
        status_box = st.empty()
        progress_box = st.empty()
        result_box = st.empty()

        if st.button(f"✅ Refill {topic} ({difficulty}) +{BATCH_SIZE}"):
            st.session_state.is_generating = True
            status_box.info("Generating AI questions...")
            progress = progress_box.progress(0)

            qs, err = fetch_questions_from_gemini(topic, difficulty, BATCH_SIZE)
            progress.progress(100)

            if qs:
                add_to_bank(topic, difficulty, qs)
                result_box.success(f"Added {len(qs)} AI questions to shared bank.")
            else:
                result_box.warning("Gemini unavailable. No AI questions were added.")
                if err:
                    with result_box.container():
                        st.error(err)

            st.session_state.is_generating = False

        if st.button(f"🚀 Build {topic} ({difficulty}) bank (~100 questions)"):
            st.session_state.is_generating = True
            added = 0
            failures = []
            progress = progress_box.progress(0)

            for i in range(BANK_CALLS):
                status_box.info(f"Building bank... batch {i+1}/{BANK_CALLS}")
                qs, err = fetch_questions_from_gemini(topic, difficulty, BATCH_SIZE)

                if qs:
                    add_to_bank(topic, difficulty, qs)
                    added += len(qs)
                else:
                    failures.append(err or "Unknown Gemini error")
                    break

                progress.progress(int(((i + 1) / BANK_CALLS) * 100))
                time.sleep(1.0)

            if added:
                result_box.success(f"Done ✅ Added {added} AI questions.")
            else:
                result_box.warning("No AI questions were added.")

            if failures:
                with st.expander("Show AI generation errors"):
                    for f in failures:
                        st.write(f)

            st.session_state.is_generating = False

        if st.button(f"🚀 Generate {ALL_DOMAINS_TARGET} for EVERY domain ({difficulty})"):
            st.session_state.is_generating = True
            total = 0
            failures = []
            progress = progress_box.progress(0)

            for i, dom in enumerate(DOMAINS, start=1):
                status_box.info(f"Generating domain {i}/{len(DOMAINS)}")
                qs, err = fetch_questions_from_gemini(dom, difficulty, ALL_DOMAINS_BATCH_SIZE)

                if qs:
                    add_to_bank(dom, difficulty, qs)
                    total += len(qs)
                else:
                    failures.append(f"{dom} -> {err or 'Unknown Gemini error'}")

                progress.progress(int((i / len(DOMAINS)) * 100))
                time.sleep(1.2)

            if total:
                result_box.success(f"Done ✅ Added {total} AI questions across domains.")
            else:
                result_box.warning("No AI questions were added across domains.")

            if failures:
                result_box.warning(f"{len(failures)} domain(s) failed.")
                with st.expander("Show failed domains"):
                    for f in failures:
                        st.write(f)

            st.session_state.is_generating = False

        st.markdown("### Teacher View")
        st.dataframe(lb_sorted[:50], use_container_width=True, height=240)

# =================================================
# QUESTION PICKER
# =================================================
def pick_question(topic_: str, difficulty_: str):
    bank = get_bank(topic_, difficulty_)
    if not bank:
        return random.choice(FALLBACK_QUESTIONS)

    key = (topic_, difficulty_)
    seen = st.session_state.seen_by_domain.setdefault(key, set())

    if len(seen) >= len(bank):
        seen.clear()

    for _ in range(100):
        idx = random.randrange(len(bank))
        if idx not in seen:
            seen.add(idx)
            return bank[idx]

    return random.choice(bank)

active_topic = topic
active_diff = difficulty

if st.session_state.challenge_mode and st.session_state.active_domain and st.session_state.active_difficulty:
    active_topic = st.session_state.active_domain
    active_diff = st.session_state.active_difficulty
    st.info(f"⚔️ Challenge Mode: {active_topic} ({active_diff}) — Question {st.session_state.challenge_count + 1}/{CHALLENGE_QUESTIONS}")

cooldown = int(max(0, st.session_state.next_allowed_time - time.time()))
if cooldown > 0:
    st.caption(f"Cooldown: {cooldown}s")

if st.button("Next Question", disabled=cooldown > 0):
    st.session_state.next_allowed_time = time.time() + COOLDOWN_SECONDS
    st.session_state.question = pick_question(active_topic, active_diff)
    st.session_state.answered = False
    st.session_state.answer_choice = None

# =================================================
# QUESTION DISPLAY
# =================================================
q = st.session_state.get("question")
if not q:
    st.info("Click **Next Question** to begin.")
    st.stop()

st.markdown("## 🧠 Question")
st.markdown(q["question"])
st.markdown(f"**A)** {q['A']}")
st.markdown(f"**B)** {q['B']}")
st.markdown(f"**C)** {q['C']}")
st.markdown(f"**D)** {q['D']}")

st.radio(
    "Answer",
    ["A", "B", "C", "D"],
    index=None,
    horizontal=True,
    key="answer_choice",
    disabled=st.session_state.answered
)

if st.button("Submit Answer"):
    if st.session_state.answer_choice is None:
        st.warning("Select an answer first.")
    elif st.session_state.answered:
        st.warning("Already submitted.")
    else:
        st.session_state.id_locked = True
        st.session_state.answered = True
        st.session_state.total_answered += 1

        correct = (st.session_state.answer_choice == q["correct"])

        if correct:
            me_before = lb_get_user(st.session_state.player_id) or {}

            streak_before = safe_int(me_before.get("streak", 0))
            streak_after = streak_before + 1
            bonus = STREAK_BONUS_XP if streak_after % STREAK_BONUS_EVERY == 0 else 0

            st.session_state.score += 1
            lb_add_xp_and_streak(st.session_state.player_id, XP_CORRECT + bonus, +1)

            if bonus:
                st.success(f"✅ Correct! +{XP_CORRECT} XP  🔥 Streak bonus +{bonus} XP!")
            else:
                st.success(f"✅ Correct! +{XP_CORRECT} XP")
        else:
            lb_add_xp_and_streak(st.session_state.player_id, XP_WRONG, -999)
            st.error(f"❌ Incorrect. Correct answer: {q['correct']}")

        st.info(q["explanation"])

        if st.session_state.challenge_mode and st.session_state.challenge_id:
            st.session_state.challenge_count += 1
            if correct:
                st.session_state.challenge_correct += 1

            if st.session_state.challenge_count >= CHALLENGE_QUESTIONS:
                cid = st.session_state.challenge_id
                challenge_row = None
                for row in ch_read_all():
                    if str(row.get("challenge_id", "")) == cid:
                        challenge_row = row
                        break

                if challenge_row:
                    if challenge_row["challenger"].lower() == st.session_state.player_id.lower():
                        ch_update(cid, {"challenger_score": str(st.session_state.challenge_correct)})
                    elif challenge_row["opponent"].lower() == st.session_state.player_id.lower():
                        ch_update(cid, {"opponent_score": str(st.session_state.challenge_correct)})

                    refreshed = None
                    for row in ch_read_all():
                        if str(row.get("challenge_id", "")) == cid:
                            refreshed = row
                            break

                    if refreshed and refreshed.get("challenger_score") != "" and refreshed.get("opponent_score") != "":
                        ch_update(cid, {"status": "done"})
                        result = ch_finalize_if_done(cid)
                        if result:
                            st.success(result)
                    else:
                        st.success("✅ Challenge attempt submitted! Waiting for the other student.")

                st.session_state.challenge_mode = False
                st.session_state.challenge_id = None
                st.session_state.challenge_count = 0
                st.session_state.challenge_correct = 0
                st.session_state.active_domain = None
                st.session_state.active_difficulty = None
                st.info("Challenge finished.")
