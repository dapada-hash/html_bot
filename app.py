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
import firebase_admin
from firebase_admin import credentials, firestore

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

FIREBASE_SERVICE_ACCOUNT_JSON = read_secret("FIREBASE_SERVICE_ACCOUNT_JSON", None)
MODEL = "gemini-2.5-flash"

BATCH_SIZE = 25
BANK_TARGET = 100
BANK_CALLS = max(1, BANK_TARGET // BATCH_SIZE)
ALL_DOMAINS_TARGET = 25
ALL_DOMAINS_BATCH_SIZE = 25

CHALLENGE_QUESTIONS = 5
CHALLENGE_TOTAL_SECONDS = None  # timer removed for challenges
QUESTION_TIMER_SECONDS = 15
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


def parse_service_account(raw_value):
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

    raise ValueError("FIREBASE_SERVICE_ACCOUNT_JSON must be a JSON string or dict.")


def firebase_config_present() -> bool:
    return bool(FIREBASE_SERVICE_ACCOUNT_JSON and str(FIREBASE_SERVICE_ACCOUNT_JSON).strip())


# =================================================
# FIREBASE / FIRESTORE
# =================================================
@st.cache_resource
def get_firestore_client():
    creds_dict = parse_service_account(FIREBASE_SERVICE_ACCOUNT_JSON)
    if not creds_dict:
        raise ValueError("Firebase service account credentials are missing.")

    if not firebase_admin._apps:
        cred = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(cred)

    return firestore.client()


def check_firestore():
    if not firebase_config_present():
        return False, "Missing FIREBASE_SERVICE_ACCOUNT_JSON in secrets."

    try:
        db = get_firestore_client()
        list(db.collection("players").limit(1).stream())
        return True, ""
    except Exception as e:
        return False, str(e)


def db():
    return get_firestore_client()


def player_ref(player_id: str):
    return db().collection("players").document(player_id)


def session_ref():
    return db().collection("sessions").document()


def challenge_ref(challenge_id: str):
    return db().collection("challenges").document(challenge_id)


def firestore_enabled():
    return st.session_state.get("firebase_ok", False)


# =================================================
# FIRESTORE READ LAYER
# =================================================
@st.cache_data(ttl=20)
def load_players():
    docs = db().collection("players").stream()
    rows = []
    for doc in docs:
        data = doc.to_dict() or {}
        if "name" not in data:
            data["name"] = doc.id
        rows.append(data)
    return rows


@st.cache_data(ttl=20)
def load_challenges():
    docs = db().collection("challenges").stream()
    rows = []
    for doc in docs:
        data = doc.to_dict() or {}
        if "challenge_id" not in data:
            data["challenge_id"] = doc.id
        rows.append(data)
    return rows


@st.cache_data(ttl=20)
def load_sessions():
    docs = db().collection("sessions").order_by("timestamp_utc", direction=firestore.Query.DESCENDING).limit(100).stream()
    return [doc.to_dict() or {} for doc in docs]


def clear_db_caches():
    load_players.clear()
    load_challenges.clear()
    load_sessions.clear()


def mark_db_data_stale():
    st.session_state.last_db_sync = 0


def get_app_data():
    now_ts = time.time()

    if (
        not st.session_state.leaderboard_cache
        or not st.session_state.challenge_cache
        or now_ts - st.session_state.last_db_sync > 20
    ):
        st.session_state.leaderboard_cache = load_players()
        st.session_state.challenge_cache = load_challenges()
        st.session_state.last_db_sync = now_ts

    return st.session_state.leaderboard_cache, st.session_state.challenge_cache


# =================================================
# FIRESTORE WRITE HELPERS
# =================================================
def upsert_player(name: str, period: str):
    name = name.strip()
    if not name:
        return

    ref = player_ref(name)
    snap = ref.get()

    if snap.exists:
        data = snap.to_dict() or {}
        ref.set({
            "name": name,
            "period": period,
            "xp": safe_int(data.get("xp", 0)),
            "wins": safe_int(data.get("wins", 0)),
            "losses": safe_int(data.get("losses", 0)),
            "streak": safe_int(data.get("streak", 0)),
            "best_streak": safe_int(data.get("best_streak", 0)),
            "last_seen_utc": now_utc(),
        }, merge=True)
    else:
        ref.set({
            "name": name,
            "period": period,
            "xp": 0,
            "wins": 0,
            "losses": 0,
            "streak": 0,
            "best_streak": 0,
            "last_seen_utc": now_utc(),
        })

    clear_db_caches()
    mark_db_data_stale()


def add_xp_and_streak(name: str, delta_xp: int, streak_delta: int, win_delta=0, loss_delta=0):
    name = name.strip()
    if not name:
        return

    ref = player_ref(name)
    snap = ref.get()

    if not snap.exists:
        upsert_player(name, "Other")
        snap = ref.get()

    data = snap.to_dict() or {}

    xp = safe_int(data.get("xp", 0)) + int(delta_xp)
    wins = safe_int(data.get("wins", 0)) + int(win_delta)
    losses = safe_int(data.get("losses", 0)) + int(loss_delta)

    streak = safe_int(data.get("streak", 0))
    best = safe_int(data.get("best_streak", 0))

    if streak_delta == -999:
        streak = 0
    else:
        streak = max(0, streak + int(streak_delta))
        best = max(best, streak)

    ref.set({
        "name": name,
        "period": data.get("period", "Other"),
        "xp": xp,
        "wins": wins,
        "losses": losses,
        "streak": streak,
        "best_streak": best,
        "last_seen_utc": now_utc(),
    }, merge=True)

    clear_db_caches()
    mark_db_data_stale()


def log_session(name: str, period: str, score: int, answered: int):
    accuracy = round((score / answered) * 100, 2) if answered else 0.0
    session_ref().set({
        "timestamp_utc": now_utc(),
        "name": name,
        "period": period,
        "score": int(score),
        "answered": int(answered),
        "accuracy": accuracy,
    })
    clear_db_caches()


def create_challenge(challenger: str, opponent: str, domain: str, difficulty: str):
    ref = db().collection("challenges").document()
    ref.set({
        "challenge_id": ref.id,
        "created_utc": now_utc(),
        "challenger": challenger,
        "opponent": opponent,
        "domain": domain,
        "difficulty": difficulty,
        "status": "pending",
        "challenger_score": None,
        "opponent_score": None,
    })
    clear_db_caches()
    mark_db_data_stale()
    return ref.id


def update_challenge(cid: str, updates: dict):
    challenge_ref(cid).set(updates, merge=True)
    clear_db_caches()
    mark_db_data_stale()


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
# XP POPUP
# =================================================
def show_xp_popup():
    popup_text = st.session_state.get("xp_popup_text", "").strip()
    popup_kind = st.session_state.get("xp_popup_kind", "good")
    popup_nonce = st.session_state.get("xp_popup_nonce", 0)

    if not popup_text:
        return

    bg = "linear-gradient(180deg, #22c55e, #16a34a)" if popup_kind == "good" else "linear-gradient(180deg, #f59e0b, #d97706)"
    border = "#166534" if popup_kind == "good" else "#92400e"

    st.markdown(
        f"""
        <style>
        @keyframes xpFloatFade-{popup_nonce} {{
            0% {{
                opacity: 0;
                transform: translate(-50%, 18px) scale(0.92);
            }}
            12% {{
                opacity: 1;
                transform: translate(-50%, 0px) scale(1.02);
            }}
            75% {{
                opacity: 1;
                transform: translate(-50%, -8px) scale(1.0);
            }}
            100% {{
                opacity: 0;
                transform: translate(-50%, -28px) scale(0.96);
            }}
        }}

        .xp-popup-{popup_nonce} {{
            position: fixed;
            left: 50%;
            top: 92px;
            transform: translateX(-50%);
            z-index: 9999;
            padding: 14px 22px;
            border-radius: 18px;
            color: white;
            font-weight: 800;
            font-size: 24px;
            letter-spacing: 0.3px;
            background: {bg};
            border: 3px solid {border};
            box-shadow: 0 14px 30px rgba(0,0,0,0.22);
            animation: xpFloatFade-{popup_nonce} 2.2s ease-out forwards;
            pointer-events: none;
            text-align: center;
            white-space: pre-line;
        }}
        </style>

        <div class="xp-popup-{popup_nonce}">
            {popup_text}
        </div>
        """,
        unsafe_allow_html=True,
    )


# =================================================
# COMBO METER
# =================================================
def render_combo_meter(streak_value: int):
    streak_value = max(0, int(streak_value))

    if streak_value >= 10:
        tier_label = "👑 Legendary Combo"
        glow = "#f59e0b"
        fill_pct = 100
    elif streak_value >= 5:
        tier_label = "⚡ Hot Streak"
        glow = "#22c55e"
        fill_pct = min(100, int((streak_value / 10) * 100))
    elif streak_value >= 3:
        tier_label = "🔥 Combo Active"
        glow = "#3b82f6"
        fill_pct = min(100, int((streak_value / 10) * 100))
    elif streak_value >= 1:
        tier_label = "✨ Building Combo"
        glow = "#a855f7"
        fill_pct = min(100, int((streak_value / 10) * 100))
    else:
        tier_label = "Start a combo"
        glow = "#64748b"
        fill_pct = 0

    st.markdown(
        f"""
        <style>
        .combo-wrap {{
            margin-top: 10px;
            margin-bottom: 8px;
            padding: 14px 16px;
            border-radius: 18px;
            background: linear-gradient(180deg, #0f172a, #1e293b);
            border: 2px solid {glow};
            box-shadow: 0 0 0 1px rgba(255,255,255,0.04), 0 10px 24px rgba(0,0,0,0.18);
        }}
        .combo-top {{
            display:flex;
            justify-content:space-between;
            align-items:center;
            margin-bottom:10px;
            color:white;
            font-weight:800;
            font-size:18px;
        }}
        .combo-badge {{
            padding: 6px 12px;
            border-radius: 999px;
            background: {glow};
            color: white;
            font-weight: 900;
            font-size: 15px;
            box-shadow: 0 0 18px {glow};
        }}
        .combo-track {{
            width:100%;
            height:16px;
            background:#334155;
            border-radius:999px;
            overflow:hidden;
        }}
        .combo-fill {{
            width:{fill_pct}%;
            height:100%;
            background: linear-gradient(90deg, {glow}, #ffffff);
            border-radius:999px;
            transition: width 0.4s ease;
        }}
        .combo-caption {{
            margin-top:8px;
            color:#cbd5e1;
            font-size:14px;
            font-weight:600;
        }}
        </style>

        <div class="combo-wrap">
            <div class="combo-top">
                <div>{tier_label}</div>
                <div class="combo-badge">Combo x{streak_value}</div>
            </div>
            <div class="combo-track">
                <div class="combo-fill"></div>
            </div>
            <div class="combo-caption">
                3 = 🔥 Combo • 5 = ⚡ Hot Streak • 10 = 👑 Legendary
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )


def prepare_question(topic_: str, difficulty_: str):
    st.session_state.question = pick_question(topic_, difficulty_)
    st.session_state.answered = False
    st.session_state.answer_choice = None
    st.session_state.submit_locked = False
    st.session_state.question_token = f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    st.session_state.question_deadline = 0.0
    st.session_state.time_up_handled = False


def complete_challenge_and_show_result(cid: str, player_id_lower: str):
    try:
        current_snap = challenge_ref(cid).get()
        challenge_row = current_snap.to_dict() if current_snap.exists else None

        if challenge_row:
            if str(challenge_row.get("challenger", "")).strip().lower() == player_id_lower:
                update_challenge(cid, {"challenger_score": st.session_state.challenge_correct})
            elif str(challenge_row.get("opponent", "")).strip().lower() == player_id_lower:
                update_challenge(cid, {"opponent_score": st.session_state.challenge_correct})

            refreshed_snap = challenge_ref(cid).get()
            refreshed = refreshed_snap.to_dict() if refreshed_snap.exists else None

            if refreshed and refreshed.get("challenger_score") is not None and refreshed.get("opponent_score") is not None:
                update_challenge(cid, {"status": "done"})
                final_snap = challenge_ref(cid).get()
                final_row = final_snap.to_dict() if final_snap.exists else None

                if final_row:
                    c = final_row["challenger"]
                    o = final_row["opponent"]
                    cs = safe_int(final_row.get("challenger_score", 0))
                    os_ = safe_int(final_row.get("opponent_score", 0))

                    if cs > os_:
                        add_xp_and_streak(c, XP_WIN, 0, win_delta=1)
                        add_xp_and_streak(o, XP_LOSS, 0, loss_delta=1)
                    elif os_ > cs:
                        add_xp_and_streak(o, XP_WIN, 0, win_delta=1)
                        add_xp_and_streak(c, XP_LOSS, 0, loss_delta=1)
                    else:
                        add_xp_and_streak(c, XP_DRAW, 0)
                        add_xp_and_streak(o, XP_DRAW, 0)

                    is_challenger = str(final_row.get("challenger", "")).strip().lower() == player_id_lower
                    my_score = cs if is_challenger else os_
                    opp_score = os_ if is_challenger else cs

                    if my_score > opp_score:
                        st.session_state.challenge_result_message = f"🏆 You won the challenge! ({my_score} vs {opp_score})"
                    elif my_score < opp_score:
                        st.session_state.challenge_result_message = f"😔 You lost the challenge. ({my_score} vs {opp_score})"
                    else:
                        st.session_state.challenge_result_message = f"🤝 Challenge tied! ({my_score} vs {opp_score})"
            else:
                st.session_state.challenge_result_message = "✅ Challenge submitted! Waiting for the other student."
    except Exception as e:
        st.warning("Could not update challenge.")
        st.code(str(e))

    st.session_state.challenge_mode = False
    st.session_state.challenge_id = None
    st.session_state.challenge_count = 0
    st.session_state.challenge_correct = 0
    st.session_state.active_domain = None
    st.session_state.active_difficulty = None
    st.session_state.question = None
    st.session_state.question_deadline = 0.0
    st.session_state.time_up_handled = False
    st.session_state.challenge_start_time = 0.0
    st.session_state.challenge_time_over = False


# =================================================
# SESSION STATE
# =================================================
st.session_state.setdefault("score", 0)
st.session_state.setdefault("total_answered", 0)
st.session_state.setdefault("answered", False)
st.session_state.setdefault("question", None)
st.session_state.setdefault("answer_choice", None)
st.session_state.setdefault("next_allowed_time", 0.0)
st.session_state.setdefault("submit_locked", False)
st.session_state.setdefault("question_token", "")
st.session_state.setdefault("answered_tokens", [])
st.session_state.setdefault("last_challenge_sent_at", 0.0)
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
st.session_state.setdefault("question_deadline", 0.0)
st.session_state.setdefault("time_up_handled", False)
st.session_state.setdefault("challenge_start_time", 0.0)
st.session_state.setdefault("challenge_time_over", False)
st.session_state.setdefault("challenge_result_message", "")

st.session_state.setdefault("is_teacher", False)
st.session_state.setdefault("is_generating", False)
st.session_state.setdefault("firebase_ok", False)
st.session_state.setdefault("firebase_error", "")
st.session_state.setdefault("leaderboard_cache", [])
st.session_state.setdefault("challenge_cache", [])
st.session_state.setdefault("last_db_sync", 0)
st.session_state.setdefault("session_logged", False)

st.session_state.setdefault("xp_popup_text", "")
st.session_state.setdefault("xp_popup_kind", "")
st.session_state.setdefault("xp_popup_nonce", 0)


# =================================================
# CHECK FIRESTORE
# =================================================
firebase_ok, firebase_err = check_firestore()
st.session_state["firebase_ok"] = firebase_ok
st.session_state["firebase_error"] = firebase_err


# =================================================
# LOGIN / TEACHER MODE FIRST
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


# =================================================
# AUTO REFRESH
# =================================================
if st.session_state.get("challenge_mode", False) and st.session_state.get("question") and not st.session_state.get("answered", False):
    st_autorefresh(interval=1000, limit=None, key="question_timer_tick")
    st.sidebar.caption("⏳ Challenge timer active")
elif st.session_state.get("is_teacher", False):
    live_refresh = st.sidebar.checkbox("Live leaderboard refresh", value=False)
    refresh_seconds = st.sidebar.selectbox("Refresh speed", [30, 60, 120], index=0)

    if live_refresh and not st.session_state.get("is_generating", False):
        st_autorefresh(interval=refresh_seconds * 1000, limit=None, key="teacher_live_refresh")
        st.sidebar.caption(f"🔄 Teacher refresh every {refresh_seconds} seconds")
    elif st.session_state.get("is_generating", False):
        st.sidebar.caption("⏸ Auto-refresh paused during question generation")
else:
    student_live_refresh = st.sidebar.checkbox("Auto-refresh challenges", value=True)
    student_refresh_seconds = st.sidebar.selectbox("Challenge refresh speed", [5, 8, 10, 15], index=1)

    if student_live_refresh and not st.session_state.get("challenge_mode", False):
        st_autorefresh(interval=student_refresh_seconds * 1000, limit=None, key="student_challenge_refresh")
        st.sidebar.caption(f"🔄 Student refresh every {student_refresh_seconds} seconds")
    else:
        st.sidebar.caption("Student auto-refresh paused during active challenge")

if not st.session_state.player_id:
    st.warning("Enter First Name + numeric Student ID to start.")
    st.stop()

if not firestore_enabled():
    st.warning("Firebase is not available.")
    st.code(st.session_state.get("firebase_error", "Unknown Firebase error"))
    st.stop()

try:
    upsert_player(st.session_state.player_id, st.session_state.student_period)
except Exception as e:
    st.warning("Could not sync your player record.")
    st.code(str(e))

st.sidebar.divider()
st.sidebar.header("Quiz Settings")
topic = st.sidebar.selectbox("Domain", DOMAINS)
difficulty = st.sidebar.selectbox("Difficulty", ["Easy", "Medium", "Hard"])
st.sidebar.caption(f"Shared bank for this domain: {bank_size(topic, difficulty)}")

lu = bank_last_updated(topic, difficulty)
if lu:
    st.sidebar.caption(f"Last teacher refill (UTC): {lu}")

st.sidebar.success("✅ Persistent mode: Firebase Firestore")


# =================================================
# SINGLE DATA FETCH
# =================================================
try:
    lb, ch_all = get_app_data()
except Exception as e:
    lb, ch_all = [], []
    st.warning("Could not load Firebase data.")
    st.code(str(e))

lb_sorted = sorted(lb, key=lambda r: safe_int(r.get("xp", 0)), reverse=True)

player_id_lower = st.session_state.player_id.strip().lower()
me = next(
    (r for r in lb if str(r.get("name", "")).strip().lower() == player_id_lower),
    {}
)

show_xp_popup()

if st.session_state.get("challenge_result_message"):
    result_msg = st.session_state.get("challenge_result_message", "")
    if "won" in result_msg.lower():
        st.success(result_msg)
    elif "lost" in result_msg.lower():
        st.error(result_msg)
    else:
        st.info(result_msg)



# =================================================
# LEADERBOARD
# =================================================
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
    else:
        st.markdown(
            """
            <div style="text-align:center;background:#f1f5f9;padding:18px;border-radius:18px;min-height:220px;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:48px;">🥈</div>
                <div style="font-size:22px; font-weight:700;">Open Spot</div>
                <div style="font-size:18px;">0 XP</div>
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
    else:
        st.markdown(
            """
            <div style="text-align:center;background:#fef3c7;padding:22px;border-radius:20px;min-height:260px;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:60px;">🥇</div>
                <div style="font-size:24px; font-weight:800;">Open Spot</div>
                <div style="font-size:18px;">0 XP</div>
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
    else:
        st.markdown(
            """
            <div style="text-align:center;background:#f5e1d1;padding:18px;border-radius:18px;min-height:220px;display:flex;flex-direction:column;justify-content:center;">
                <div style="font-size:48px;">🥉</div>
                <div style="font-size:22px; font-weight:700;">Open Spot</div>
                <div style="font-size:18px;">0 XP</div>
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
        if opp_name and opp_name.lower() != player_id_lower:
            if st.button("⚔️ Challenge", key=f"challenge_{opp_name}_{i}"):
                # Prevent more than one active challenge at a time
                active = any(
                    str(c.get("challenger","")).strip().lower()==player_id_lower
                    and c.get("status") in ("pending","accepted")
                    for c in ch_all
                )

                if active:
                    st.warning("You already have an active challenge. Finish it before sending another.")
                else:
                    try:
                        create_challenge(st.session_state.player_id, opp_name, topic, difficulty)
                        st.session_state.last_challenge_sent_at = time.time()
                        st.success(f"Challenge sent to {opp_name}!")
                    except Exception as e:
                        st.warning("Could not create challenge.")
                        st.code(str(e))


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

render_combo_meter(my_streak)

st.divider()


# =================================================
# CHALLENGE INBOX / OUTBOX
# =================================================
st.markdown("## 📩 Challenges")

incoming = [
    c for c in ch_all
    if str(c.get("opponent", "")).strip().lower() == player_id_lower
    and c.get("status") in ("pending", "accepted")
]

outgoing = [
    c for c in ch_all
    if str(c.get("challenger", "")).strip().lower() == player_id_lower
    and c.get("status") == "accepted"
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
                    try:
                        update_challenge(c["challenge_id"], {"status": "accepted"})
                        st.session_state.challenge_mode = True
                        st.session_state.challenge_id = c["challenge_id"]
                        st.session_state.challenge_count = 0
                        st.session_state.challenge_correct = 0
                        st.session_state.active_domain = c["domain"]
                        st.session_state.active_difficulty = c["difficulty"]
                        st.session_state.challenge_start_time = time.time()
                        st.session_state.challenge_time_over = False
                        st.session_state.challenge_result_message = ""
                        prepare_question(c["domain"], c["difficulty"])
                        st.success("⚔️ Challenge started!")
                    except Exception as e:
                        st.warning("Could not accept challenge.")
                        st.code(str(e))

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
                st.session_state.challenge_start_time = time.time()
                st.session_state.challenge_time_over = False
                st.session_state.challenge_result_message = ""
                prepare_question(c["domain"], c["difficulty"])
                st.success("⚔️ Challenge started!")

st.divider()


# =================================================
# TEACHER PANEL CONTENT
# =================================================
if st.session_state.is_teacher:
    st.markdown("## 🔒 Teacher View")

    status_box = st.empty()
    progress_box = st.empty()
    result_box = st.empty()

    t1, t2, t3 = st.columns(3)

    with t1:
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

    with t2:
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

    with t3:
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

    teacher_rows = []
    for i, r in enumerate(lb_sorted[:50], start=1):
        teacher_rows.append({
            "Rank": i,
            "name": r.get("name", ""),
            "period": r.get("period", ""),
            "xp": safe_int(r.get("xp", 0)),
            "wins": safe_int(r.get("wins", 0)),
            "losses": safe_int(r.get("losses", 0)),
            "streak": safe_int(r.get("streak", 0)),
            "best_streak": safe_int(r.get("best_streak", 0)),
        })
    st.dataframe(teacher_rows, use_container_width=True, height=240)


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
    # Challenge timer removed — students can take their time during challenge

cooldown = int(max(0, st.session_state.next_allowed_time - time.time()))
if cooldown > 0:
    st.caption(f"Cooldown: {cooldown}s")

if not st.session_state.challenge_mode and st.button("Next Question", disabled=cooldown > 0):
    st.session_state.next_allowed_time = time.time() + max(COOLDOWN_SECONDS, 2)
    st.session_state.question = pick_question(active_topic, active_diff)
    st.session_state.answered = False
    st.session_state.answer_choice = None
    st.session_state.submit_locked = False
    st.session_state.question_token = f"{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    st.session_state.question_deadline = 0.0
    st.session_state.time_up_handled = False
    st.rerun()


# =================================================
# QUESTION DISPLAY
# =================================================
q = st.session_state.get("question")
if not q:
    st.info("Click **Next Question** to begin.")
    st.stop()

if st.session_state.challenge_mode and st.session_state.challenge_time_over:
    complete_challenge_and_show_result(st.session_state.challenge_id, player_id_lower)
    st.rerun()

st.markdown("## 🧠 Question")
st.markdown(q["question"])
st.markdown(f"**A)** {q['A']}")
st.markdown(f"**B)** {q['B']}")
st.markdown(f"**C)** {q['C']}")
st.markdown(f"**D)** {q['D']}")

if (
    not st.session_state.answered
    and st.session_state.question_deadline > 0
    and time.time() >= st.session_state.question_deadline
    and not st.session_state.time_up_handled
):
    st.session_state.time_up_handled = True
    st.session_state.submit_locked = True
    st.session_state.id_locked = True
    st.session_state.answered = True
    st.session_state.total_answered += 1

    try:
        add_xp_and_streak(st.session_state.player_id, XP_WRONG, -999)
        mark_db_data_stale()
    except Exception as e:
        st.warning("Could not save timeout result to Firebase.")
        st.code(str(e))

    st.session_state.xp_popup_text = "⏰ Time Up\n❌ Streak Reset"
    st.session_state.xp_popup_kind = "warn"
    st.session_state.xp_popup_nonce += 1

    try:
        log_session(
            st.session_state.first_name.strip() or st.session_state.player_id,
            st.session_state.student_period,
            st.session_state.score,
            st.session_state.total_answered,
        )
    except Exception as e:
        st.warning("Could not save session log to Firebase.")
        st.code(str(e))

    if st.session_state.challenge_mode and st.session_state.challenge_id:
        cid = st.session_state.challenge_id
        st.session_state.challenge_count += 1

        if st.session_state.challenge_count >= CHALLENGE_QUESTIONS:
            complete_challenge_and_show_result(cid, player_id_lower)
            st.rerun()
        else:
            prepare_question(active_topic, active_diff)
            st.rerun()
