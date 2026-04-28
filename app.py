import streamlit as st
import json
import os
import sys
import subprocess
import time
import hashlib
from datetime import datetime
from dotenv import load_dotenv, set_key

try:
    from gmail_draft_agent import generate_draft_text, save_draft, get_gmail_service as _get_draft_service
    DRAFT_AVAILABLE = True
except Exception:
    DRAFT_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable
CHECKED_FILE = os.path.join(BASE_DIR, "checked_tasks.json")

st.set_page_config(page_title="DrCoach 🥼", page_icon="🥼", layout="wide")

# ── 세션 상태 초기화 ─────────────────────────────────────────
for key in ["etl_proc", "gmail_proc", "fetch_gmail_proc", "fetch_etl_proc"]:
    if key not in st.session_state:
        st.session_state[key] = None

if "checked_tasks" not in st.session_state:
    if os.path.exists(CHECKED_FILE):
        with open(CHECKED_FILE, encoding="utf-8") as f:
            st.session_state.checked_tasks = set(json.load(f))
    else:
        st.session_state.checked_tasks = set()


# ── 헬퍼 함수 ────────────────────────────────────────────────
def reload_env():
    load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)


def get_status():
    reload_env()
    api_key = os.getenv("GEMINI_API_KEY", "")
    return {
        "gemini": bool(api_key and api_key != "your_api_key_here" and api_key.startswith("AIza")),
        "gmail_creds": os.path.exists(os.path.join(BASE_DIR, "credentials.json")),
        "gmail": os.path.exists(os.path.join(BASE_DIR, "token.json")),
        "etl": os.path.exists(os.path.join(BASE_DIR, "etl_auth.json")),
        "gmail_data": os.path.exists(os.path.join(BASE_DIR, "gmail_tasks.json")),
        "etl_data": os.path.exists(os.path.join(BASE_DIR, "today_tasks.json")),
    }


def load_json(filename):
    path = os.path.join(BASE_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading {filename}: {e}")
        return None


def save_api_key(key_value):
    env_path = os.path.join(BASE_DIR, ".env")
    set_key(env_path, "GEMINI_API_KEY", key_value)


def proc_running(key):
    proc = st.session_state.get(key)
    return proc is not None and proc.poll() is None


def read_log(log_filename, last_n=20):
    path = os.path.join(BASE_DIR, log_filename)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return "".join(lines[-last_n:])
    except Exception:
        return ""


def start_proc(key, cmd, log_filename):
    log_path = os.path.join(BASE_DIR, log_filename)
    proc = subprocess.Popen(
        cmd, cwd=BASE_DIR,
        stdout=open(log_path, "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    st.session_state[key] = proc


def save_checked_tasks():
    with open(CHECKED_FILE, "w", encoding="utf-8") as f:
        json.dump(list(st.session_state.checked_tasks), f, ensure_ascii=False)


def make_task_id(title, course_id=""):
    return hashlib.md5(f"{course_id}__{title}".encode()).hexdigest()[:12]


def status_badge(status):
    cfg = {
        "제출 완료":              ("✅", "#166534", "#dcfce7", "#bbf7d0"),
        "제출 완료(재제출 가능)": ("↩️", "#9a3412", "#fff7ed", "#fed7aa"),
        "미제출":                 ("❌", "#991b1b", "#fef2f2", "#fecaca"),
        "확인 필요":              ("⏳", "#92400e", "#fefce8", "#fde68a"),
        "재제출 가능":            ("🔄", "#c2410c", "#fff7ed", "#fdba74"),
    }
    icon, tc, bg, bd = cfg.get(status, ("•", "#374151", "#f9fafb", "#e5e7eb"))
    return (f'<span style="background:{bg};color:{tc};border:1px solid {bd};'
            f'border-radius:6px;padding:3px 10px;font-size:12px;font-weight:600;'
            f'white-space:nowrap;">{icon} {status}</span>')


# ── 코칭 메시지 (과학적 근거 기반) ─────────────────────────
def get_coaching_data(etl_count, gmail_today):
    weekday = datetime.now().weekday()
    techniques = [
        {  # 월
            "label": "실행 의도 (Implementation Intention)",
            "ref": "Gollwitzer, 1999 · 목표 달성률 2~3배 향상",
            "body": "'언제, 어디서, 어떻게'를 구체적으로 정하면 실행 가능성이 <b>2~3배</b> 높아집니다. 막연한 '해야지'를 지금 당장 시간과 장소가 붙은 계획으로 바꿔보세요.",
            "q": "오늘 첫 번째 과제를 <em>정확히 몇 시에 어디서</em> 시작할 건가요?",
        },
        {  # 화
            "label": "인지 재구성 (Cognitive Restructuring)",
            "ref": "Beck, 1979 · CBT · 부정적 예측의 79%는 실현되지 않음",
            "body": "미루고 있는 과제가 있다면, '<b>이 과제가 어렵다</b>'는 사실과 '<b>어려울 것 같다</b>'는 생각을 구분해보세요. 불안의 79%는 실제 위협이 아닌 과대평가입니다.",
            "q": "지금 가장 두려운 과제는 무엇이고, 그 두려움의 근거는 사실인가요 아니면 예측인가요?",
        },
        {  # 수
            "label": "마음챙김 호흡 (MBSR · 4-7-8)",
            "ref": "Kabat-Zinn, 1990 · Jerath et al., 2006 · 코르티솔 감소 임상 확인",
            "body": "지금 바로 시도해보세요 — <b>4초 들이쉬고, 7초 참고, 8초 내쉬기</b>를 3회. 교감신경계가 진정되고 집중력이 회복됩니다. 공부 시작 전 루틴으로 쓰면 효과적입니다.",
            "q": "지금 몸에서 긴장이 느껴지는 부위가 있나요? 그 긴장은 무엇을 신호하고 있나요?",
        },
        {  # 목
            "label": "최우선 과제 먼저 (Eat the Frog)",
            "ref": "Steel, 2007 메타분석 + 오전 코르티솔 각성 반응(CAR)",
            "body": "오전에는 <b>코르티솔 각성 반응</b>으로 의지력이 하루 중 최고조입니다. 가장 피하고 싶은 과제를 오전에 끝내면, 완료 후 도파민 분비로 오후 전체 생산성이 따라 올라갑니다.",
            "q": "오늘 가장 미루고 싶은 과제는 무엇인가요? 그것을 오전에 끝낸 나는 어떤 기분일까요?",
        },
        {  # 금
            "label": "감사 연습 + 주간 회고",
            "ref": "Emmons & McCullough, 2003 · 삶의 만족도 23%↑, 스트레스 17%↓",
            "body": "한 주를 닫기 전, <b>이번 주 잘 해낸 것 3가지</b>를 먼저 떠올려보세요. 성취를 의식적으로 인식하는 것이 자기효능감을 높이고 번아웃을 예방합니다.",
            "q": "이번 주 스스로를 가장 자랑스럽게 만든 순간은 언제였나요?",
        },
        {  # 토
            "label": "가치 명료화 (ACT · Acceptance & Commitment Therapy)",
            "ref": "Hayes et al., 2006 · 가치 일치 행동이 내재적 동기와 번아웃 예방",
            "body": "지금 하는 공부가 <b>어떤 사람이 되고 싶다는 장기 가치</b>와 연결될 때, 단기 불편함을 더 잘 견딥니다. 오늘 잠시 멈추고 '왜 이걸 하는가'를 되새겨보세요.",
            "q": "5년 후 어떤 사람이 되어 있고 싶나요? 오늘의 공부는 그 모습과 어떻게 연결되나요?",
        },
        {  # 일
            "label": "전략적 휴식 (Ultradian Rhythm)",
            "ref": "Kleitman, 1963 + Ericsson, 1993 · 최고 수행자는 회복을 설계함",
            "body": "뇌는 <b>90분 집중 → 20분 회복</b>의 울트라디안 리듬으로 작동합니다. 일요일인 오늘, 억지로 앉아있는 것보다 <b>질 높은 회복</b>이 다음 주 수행을 더 높입니다.",
            "q": "지금 내게 필요한 것은 더 많은 노력인가요, 아니면 더 나은 회복인가요?",
        },
    ]
    t = techniques[weekday]
    total = etl_count + gmail_today
    if total >= 6:
        intro = f"오늘 <b>{total}개의 할 일</b>이 있습니다. 멀티태스킹보다 한 번에 하나씩 집중하는 것이 오류율을 40% 낮춥니다(Ophir et al., 2009). "
    elif total > 0:
        intro = f"오늘 <b>{total}개의 할 일</b>이 있습니다. 충분히 관리 가능한 양입니다. "
    else:
        intro = "오늘 당장 처리할 일이 많지 않습니다. 선행 학습이나 복습에 쓰기 좋은 날입니다. "
    return {**t, "intro": intro}


def render_coaching_card(data):
    weekday_kr = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    today = datetime.now()
    day_label = weekday_kr[today.weekday()]
    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#f0f4ff,#e8f0fe);
                border-left:5px solid #4f46e5;border-radius:14px;
                padding:22px 26px;margin-bottom:20px;">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;">
        <span style="font-size:28px;">🧠</span>
        <div>
          <div style="font-size:11px;color:#4f46e5;font-weight:700;letter-spacing:2px;">
            {day_label.upper()} · AI 주치의 코치
          </div>
          <div style="font-size:12px;color:#6b7280;margin-top:2px;">
            근거: {data['ref']}
          </div>
        </div>
      </div>
      <div style="color:#1e293b;line-height:1.85;font-size:15px;">
        {data['intro']}<br><br>
        <b>[ {data['label']} ]</b><br>
        {data['body']}
      </div>
      <div style="background:white;border:1px solid #c7d2fe;border-radius:10px;
                  padding:14px 18px;margin-top:16px;color:#3730a3;font-size:14px;line-height:1.7;">
        <span style="font-weight:700;">💭 오늘의 성찰 질문</span><br>
        <span style="font-style:italic;">{data['q']}</span>
      </div>
    </div>
    """, unsafe_allow_html=True)


# ── 사이드바 ─────────────────────────────────────────────────
with st.sidebar:
    st.title("⚙️ 연결 설정")
    status = get_status()
    badges = {"Gemini": "🟢" if status["gemini"] else "🔴",
              "Gmail":  "🟢" if status["gmail"]  else "🔴",
              "eTL":    "🟢" if status["etl"]    else "🔴"}
    st.markdown("  ".join(f"{v} {k}" for k, v in badges.items()))
    st.divider()

    # Gemini
    st.subheader("🤖 Gemini API")
    st.markdown("✅ 연결됨" if status["gemini"] else "❌ 키 없음")
    with st.expander("API 키 설정", expanded=not status["gemini"]):
        api_input = st.text_input("Gemini API Key", type="password",
                                   placeholder="AIzaSy...", key="api_key_input")
        if st.button("저장", key="btn_save_api"):
            if api_input and api_input.startswith("AIza"):
                save_api_key(api_input)
                st.success("저장 완료!")
                st.rerun()
            else:
                st.error("'AIza'로 시작하는 키를 입력하세요")
    st.divider()

    # Gmail
    st.subheader("📧 Gmail")
    if status["gmail"]:          st.markdown("✅ 연결됨")
    elif status["gmail_creds"]:  st.markdown("⚠️ OAuth 인증 필요")
    else:                        st.markdown("❌ credentials.json 없음")

    with st.expander("Gmail 설정", expanded=not status["gmail"]):
        if not status["gmail_creds"]:
            st.markdown("**설정 방법:**\n1. Google Cloud Console 접속\n2. Gmail API 활성화\n3. OAuth 클라이언트 ID 생성 (데스크톱)\n4. JSON 다운로드 후 업로드")
            uploaded = st.file_uploader("credentials.json 업로드", type=["json"], key="creds_uploader")
            if uploaded:
                with open(os.path.join(BASE_DIR, "credentials.json"), "wb") as f:
                    f.write(uploaded.read())
                st.success("업로드 완료!")
                st.rerun()
        else:
            st.success("credentials.json ✅")

        if status["gmail_creds"] and not status["gmail"]:
            if not proc_running("gmail_proc"):
                if st.button("Gmail 연결하기 (브라우저 열림)", key="btn_gmail_auth"):
                    inline = f"""
import sys, os
os.chdir(r'{BASE_DIR}')
sys.path.insert(0, r'{BASE_DIR}')
from gmail_task_agent import get_gmail_service
get_gmail_service()
print("[DONE]")
"""
                    start_proc("gmail_proc", [PYTHON, "-c", inline], "_gmail_auth.log")
                    st.rerun()
            else:
                st.info("⏳ 브라우저에서 Gmail 계정으로 로그인해주세요...")
                time.sleep(2)
                if os.path.exists(os.path.join(BASE_DIR, "token.json")):
                    st.session_state.gmail_proc = None
                    st.success("연결 완료!")
                st.rerun()

        if status["gmail"]:
            if st.button("재인증", key="btn_gmail_reauth"):
                token_path = os.path.join(BASE_DIR, "token.json")
                if os.path.exists(token_path):
                    os.remove(token_path)
                st.rerun()
    st.divider()

    # eTL
    st.subheader("🎓 eTL")
    st.markdown("✅ 연결됨" if status["etl"] else "❌ 미연결")
    with st.expander("eTL 설정", expanded=not status["etl"]):
        if not proc_running("etl_proc"):
            btn_label = "eTL 재로그인" if status["etl"] else "eTL 로그인 (브라우저 열림)"
            if st.button(btn_label, key="btn_etl_login"):
                auth_path = os.path.join(BASE_DIR, "etl_auth.json")
                if os.path.exists(auth_path):
                    os.remove(auth_path)
                start_proc("etl_proc",
                           [PYTHON, os.path.join(BASE_DIR, "etl_login_save_session.py")],
                           "_etl_login.log")
                st.rerun()
        else:
            st.info("⏳ 열린 브라우저에서 SNU 계정으로 로그인해주세요.")
            st.warning("로그인 완료 후 브라우저를 닫지 말고 아래 버튼을 클릭하세요.")
            if st.button("✅ 로그인 완료 (세션 저장)", key="btn_etl_done"):
                signal_path = os.path.join(BASE_DIR, "_etl_save_now.txt")
                with open(signal_path, "w") as f:
                    f.write("save")
                st.info("세션 저장 중...")
                time.sleep(4)
                st.rerun()
            time.sleep(2)
            if os.path.exists(os.path.join(BASE_DIR, "etl_auth.json")):
                st.session_state.etl_proc = None
                st.success("eTL 연결 완료!")
            st.rerun()
    st.divider()

    # 데이터 갱신
    st.subheader("🔄 데이터 갱신")
    gmail_ready = status["gemini"] and status["gmail"]
    etl_ready   = status["etl"]
    fetching_gmail = proc_running("fetch_gmail_proc")
    fetching_etl   = proc_running("fetch_etl_proc")

    col1, col2 = st.columns(2)
    with col1:
        if fetching_gmail:
            st.info("⏳ Gmail...")
        elif st.button("📧 Gmail", disabled=not gmail_ready, key="btn_fetch_gmail"):
            start_proc("fetch_gmail_proc",
                       [PYTHON, os.path.join(BASE_DIR, "gmail_task_agent.py")],
                       "_gmail_fetch.log")
            st.rerun()
    with col2:
        if fetching_etl:
            st.info("⏳ eTL...")
        elif st.button("🎓 eTL", disabled=not etl_ready, key="btn_fetch_etl"):
            start_proc("fetch_etl_proc",
                       [PYTHON, os.path.join(BASE_DIR, "analyze_etl_modules.py")],
                       "_etl_fetch.log")
            st.rerun()

    if not fetching_gmail and not fetching_etl:
        if st.button("🔄 전체 갱신", disabled=not (gmail_ready and etl_ready), key="btn_all"):
            start_proc("fetch_gmail_proc",
                       [PYTHON, os.path.join(BASE_DIR, "gmail_task_agent.py")],
                       "_gmail_fetch.log")
            start_proc("fetch_etl_proc",
                       [PYTHON, os.path.join(BASE_DIR, "analyze_etl_modules.py")],
                       "_etl_fetch.log")
            st.rerun()

    if fetching_gmail and not proc_running("fetch_gmail_proc"):
        st.session_state.fetch_gmail_proc = None
        st.success("Gmail 수집 완료!")
    if fetching_etl and not proc_running("fetch_etl_proc"):
        st.session_state.fetch_etl_proc = None
        st.success("eTL 수집 완료!")
    if fetching_gmail or fetching_etl:
        time.sleep(3)
        st.rerun()

    if status["gmail_data"]:
        gd = load_json("gmail_tasks.json")
        if gd and gd.get("generated_at"):
            st.caption(f"Gmail: {gd['generated_at']}")
    if status["etl_data"]:
        ed = load_json("today_tasks.json")
        if ed and ed.get("generated_at"):
            st.caption(f"eTL: {ed['generated_at']}")

    with st.expander("🔍 로그 보기"):
        log_choice = st.selectbox("로그 선택",
            ["gmail_fetch", "etl_fetch", "gmail_auth", "etl_login"], key="log_select")
        log_map = {"gmail_fetch": "_gmail_fetch.log", "etl_fetch": "_etl_fetch.log",
                   "gmail_auth": "_gmail_auth.log",   "etl_login": "_etl_login.log"}
        log_text = read_log(log_map[log_choice])
        st.code(log_text if log_text else "(없음)", language=None)


# ── 메인 대시보드 ──────────────────────────────────────────
def render_morning_header(etl_data, gmail_data):
    today = datetime.now()
    weekday_kr = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
    greetings  = [
        "새로운 한 주, 오늘 무엇을 이루겠습니까",
        "탄력받을 시간입니다",
        "한 주의 중간, 페이스를 확인하세요",
        "주말이 보이기 시작합니다",
        "한 주를 마무리할 시간입니다",
        "충분히 쉬어도 됩니다",
        "내일을 위해 준비하세요",
    ]
    date_str = f"{today.year}. {today.month}. {today.day}. {weekday_kr[today.weekday()]}"

    # 데이터 생성 시각
    etl_at = etl_data.get("generated_at", "") if etl_data else ""
    gm_at  = gmail_data.get("generated_at", "") if gmail_data else ""
    data_info = ""
    if etl_at or gm_at:
        data_info = f"<div style='font-size:11px;opacity:0.55;margin-top:4px;'>데이터 기준 — eTL: {etl_at or '-'}  /  Gmail: {gm_at or '-'}</div>"

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 100%);
                border-radius:16px;padding:28px 32px;margin-bottom:24px;color:white;">
      <div style="font-size:11px;letter-spacing:3px;opacity:0.5;text-transform:uppercase;
                  margin-bottom:8px;">MORNING BRIEFING</div>
      <div style="font-size:26px;font-weight:700;margin-bottom:6px;">📋 오늘의 할 일 브리핑</div>
      <div style="font-size:15px;opacity:0.85;">{date_str} · {greetings[today.weekday()]}</div>
      {data_info}
    </div>
    """, unsafe_allow_html=True)


def render_pending_task(task, course_id=""):
    task_id  = make_task_id(task.get("title", ""), course_id)
    is_done  = task_id in st.session_state.checked_tasks

    cols = st.columns([0.3, 4.0, 2.5, 2.4, 0.7])

    new_val = cols[0].checkbox("완료", value=is_done, key=f"c_{task_id}",
                                label_visibility="collapsed")
    if new_val != is_done:
        if new_val:
            st.session_state.checked_tasks.add(task_id)
        else:
            st.session_state.checked_tasks.discard(task_id)
        save_checked_tasks()

    title  = task.get("title", "Untitled")
    due    = task.get("due_date_parsed", "N/A")
    status = task.get("submission_status", "N/A")

    if is_done:
        cols[1].markdown(
            f"<span style='color:#9ca3af;text-decoration:line-through;'>{title}</span>",
            unsafe_allow_html=True)
    else:
        cols[1].markdown(f"**{title}**")

    cols[2].markdown(f"📅 `{due}`")
    cols[3].markdown(status_badge(status), unsafe_allow_html=True)

    links = task.get("links", [])
    if links:
        cols[4].link_button("↗", links[0])


def render_notice(item):
    cols = st.columns([5, 2, 1])
    cols[0].write(f"**{item.get('title', 'Untitled')}**")
    cols[1].write(f"📌 {item.get('source', 'announcement')}")
    if item.get("url"):
        cols[2].link_button("Open", item["url"])


def render_gmail_task_card(task):
    """Gmail 할 일 카드 — 체크박스 완료 처리 포함"""
    ttype  = task.get("task_type", "required")
    title  = task.get("task", "")
    due    = task.get("due_date", "")
    reason = task.get("reason", "")

    # message_id + task 내용 조합으로 완전 유니크 키 생성
    msg_id  = task.get("source_message_id") or ""
    task_id = make_task_id(f"{msg_id}__{title}", "gmail")
    is_done = task_id in st.session_state.checked_tasks

    if ttype == "required":
        border, bg, label_bg, label_tc = "#dc2626", "#fff5f5", "#dc2626", "white"
        icon, label = "🔴", "필수"
    else:
        border, bg, label_bg, label_tc = "#d97706", "#fffbeb", "#d97706", "white"
        icon, label = "🟡", "선택"

    # 완료 처리 시 시각 약화
    if is_done:
        bg, border = "#f3f4f6", "#d1d5db"
        label_bg, label_tc = "#d1d5db", "#9ca3af"

    due_html = (f'<span style="font-size:12px;color:{border};font-weight:600;'
                f'background:#fff;border:1px solid {border};border-radius:4px;'
                f'padding:1px 7px;">{due}</span>' if due and not is_done else "")
    reason_html = (f'<div style="color:#6b7280;font-size:12px;margin-top:5px;">{reason}</div>'
                   if reason and not is_done else "")

    title_style = "color:#9ca3af;text-decoration:line-through;" if is_done else "font-weight:600;font-size:15px;"

    col_cb, col_card = st.columns([0.3, 9.7])
    new_val = col_cb.checkbox("완료", value=is_done, key=f"g_{task_id}",
                               label_visibility="collapsed")
    if new_val != is_done:
        if new_val:
            st.session_state.checked_tasks.add(task_id)
        else:
            st.session_state.checked_tasks.discard(task_id)
        save_checked_tasks()

    col_card.markdown(f"""
    <div style="background:{bg};border-left:4px solid {border};border-radius:8px;
                padding:10px 14px;margin-bottom:6px;">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:10px;">
        <div>
          <span style="background:{label_bg};color:{label_tc};border-radius:4px;
                       padding:1px 7px;font-size:11px;font-weight:700;">{icon} {label}</span>
          <span style="{title_style};margin-left:8px;">{title}</span>
        </div>
        {due_html}
      </div>
      {reason_html}
    </div>
    """, unsafe_allow_html=True)

    # ── 초안 작성 UI (reply 가능한 메일만)
    has_source = bool(task.get("source_message_id") and task.get("source_from"))
    if DRAFT_AVAILABLE and has_source and not is_done:
        draft_key  = f"draft_{task_id}"
        status_key = f"draft_status_{task_id}"

        if draft_key not in st.session_state:
            st.session_state[draft_key] = ""
        if status_key not in st.session_state:
            st.session_state[status_key] = ""

        btn_col, _ = col_card.columns([2, 8])
        if btn_col.button("✍️ 초안 작성", key=f"gen_{task_id}"):
            with st.spinner("Gemini가 초안을 작성 중..."):
                try:
                    st.session_state[draft_key] = generate_draft_text(task)
                    st.session_state[status_key] = ""
                except Exception as e:
                    st.session_state[status_key] = f"오류: {e}"

        if st.session_state[draft_key]:
            edited = col_card.text_area(
                "초안 (수정 가능)",
                value=st.session_state[draft_key],
                height=160,
                key=f"ta_{task_id}"
            )
            save_col, _ = col_card.columns([3, 7])
            if save_col.button("📨 Gmail 임시보관함에 저장", key=f"save_{task_id}"):
                with st.spinner("저장 중..."):
                    try:
                        svc = _get_draft_service()
                        draft_id = save_draft(svc, task, edited)
                        st.session_state[status_key] = f"✅ 저장 완료 (draft_id: {draft_id[:8]}...)"
                        st.session_state[draft_key] = ""
                    except Exception as e:
                        st.session_state[status_key] = f"❌ 저장 실패: {e}"

        if st.session_state[status_key]:
            col_card.caption(st.session_state[status_key])


def main():
    status   = get_status()
    etl_data = load_json("today_tasks.json")
    gmail_data = load_json("gmail_tasks.json")

    # ── 아침 보고서 헤더
    render_morning_header(etl_data, gmail_data)

    # ── 연결 안내
    missing = [k for k, ok in [("Gemini", status["gemini"]),
                                ("Gmail",  status["gmail"]),
                                ("eTL",    status["etl"])] if not ok]
    if missing:
        st.warning(f"왼쪽 사이드바에서 연결이 필요합니다: **{', '.join(missing)}**")

    if etl_data and etl_data.get("auth_error"):
        st.error("eTL 세션이 만료되었습니다. 사이드바에서 eTL 재로그인을 해주세요.")

    if not etl_data and not gmail_data:
        st.info("사이드바에서 연결을 완료한 뒤 **데이터 갱신** 버튼을 눌러주세요.")
        return

    # ── 코칭 메시지
    etl_cnt  = sum(len(c.get("pending_tasks", [])) for c in (etl_data or {}).get("courses", []))
    gm_today = (gmail_data or {}).get("today_task_count", 0)
    coaching = get_coaching_data(etl_cnt, gm_today)
    render_coaching_card(coaching)

    # ── 전체 요약
    st.subheader("📊 전체 요약")
    etl_notice = sum(
        len(c.get("important_announcement_tasks", [])) + len(c.get("important_schedule_tasks", []))
        for c in (etl_data or {}).get("courses", [])
    )
    gm_total  = (gmail_data or {}).get("total_task_count", 0)
    done_cnt  = len(st.session_state.checked_tasks)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("eTL 과제",       etl_cnt)
    c2.metric("중요 공지",       etl_notice)
    c3.metric("Gmail 전체",     gm_total)
    c4.metric("Gmail 오늘",     gm_today)
    c5.metric("✅ 완료 처리",    done_cnt)

    st.divider()

    # ── eTL 과제 (체크아웃 포함)
    st.subheader("🎓 지금 실행할 eTL 과제")
    if etl_data and "courses" in etl_data:
        has_pending = False
        for course in etl_data["courses"]:
            pending = course.get("pending_tasks", [])
            cname   = course.get("course_name", course.get("course_id", "Unknown"))
            cid     = course.get("course_id", "")
            if not pending:
                continue
            has_pending = True

            # 완료/미완료 분리
            done_tasks   = [t for t in pending if make_task_id(t.get("title",""), cid) in st.session_state.checked_tasks]
            active_tasks = [t for t in pending if make_task_id(t.get("title",""), cid) not in st.session_state.checked_tasks]

            with st.expander(
                f"📚 {cname}  ·  남은 {len(active_tasks)}개"
                + (f"  /  ✅ 완료 {len(done_tasks)}개" if done_tasks else ""),
                expanded=len(active_tasks) > 0
            ):
                # 헤더 행
                hcols = st.columns([0.3, 4.0, 2.5, 2.4, 0.7])
                hcols[1].markdown("<small style='color:#9ca3af;'>과제명</small>", unsafe_allow_html=True)
                hcols[2].markdown("<small style='color:#9ca3af;'>마감일</small>", unsafe_allow_html=True)
                hcols[3].markdown("<small style='color:#9ca3af;'>제출 상태</small>", unsafe_allow_html=True)

                for t in active_tasks:
                    render_pending_task(t, cid)

                if done_tasks:
                    st.markdown("---")
                    st.markdown("<small style='color:#9ca3af;'>✅ 완료 처리된 항목</small>", unsafe_allow_html=True)
                    for t in done_tasks:
                        render_pending_task(t, cid)

        if not has_pending:
            st.success("현재 eTL 기준으로 실행할 과제가 없습니다.")
    else:
        st.warning("eTL 데이터를 불러올 수 없습니다.")

    st.divider()

    # ── 중요 공지
    st.subheader("📢 먼저 확인할 중요 공지")
    if etl_data and "courses" in etl_data:
        has_notice = False
        for course in etl_data["courses"]:
            notices = (course.get("important_announcement_tasks", []) +
                       course.get("important_schedule_tasks", []))
            cname   = course.get("course_name", course.get("course_id", "Unknown"))
            if not notices:
                continue
            has_notice = True
            with st.expander(f"📘 {cname} · {len(notices)}건", expanded=False):
                for item in notices:
                    render_notice(item)
        if not has_notice:
            st.write("현재 중요한 공지가 없습니다.")
    else:
        st.warning("eTL 공지 데이터를 불러올 수 없습니다.")

    st.divider()

    # ── Gmail 할 일
    st.subheader("📧 Gmail 할 일")
    if gmail_data:
        today_tasks = gmail_data.get("today_tasks", [])
        all_tasks   = gmail_data.get("all_tasks", [])
        req_tasks   = gmail_data.get("required_tasks", [t for t in all_tasks if t.get("task_type") == "required"])
        opt_tasks   = gmail_data.get("optional_tasks", [t for t in all_tasks if t.get("task_type") == "optional"])

        # 오늘 마감
        if today_tasks:
            st.markdown("#### 🔴 오늘 마감")
            for t in today_tasks:
                render_gmail_task_card(t)
        else:
            st.success("오늘 마감인 Gmail 할 일이 없습니다.")

        # 필수 항목 (오늘 제외)
        req_not_today = [t for t in req_tasks if t not in today_tasks]
        if req_not_today:
            st.markdown("#### 🟠 필수 확인 · 대응 필요")
            for t in req_not_today:
                render_gmail_task_card(t)

        # 선택 항목
        if opt_tasks:
            with st.expander(f"🟡 선택 항목 ({len(opt_tasks)}개)", expanded=False):
                for t in opt_tasks:
                    render_gmail_task_card(t)
    else:
        st.warning("Gmail 데이터를 불러올 수 없습니다.")

    st.caption(f"페이지 로드: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")


if __name__ == "__main__":
    main()
