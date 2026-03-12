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


active_topic = st.session_state.get("active_domain") or st.session_state.get("selected_topic") or (DOMAINS[0] if DOMAINS else "")
active_diff = st.session_state.get("active_difficulty") or st.session_state.get("selected_difficulty") or "Easy"

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
