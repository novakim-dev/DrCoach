"""
[역할]
eTL(Canvas LMS)에서 과제/퀴즈/공지 기반 할 일을 수집하고
“지금 당장 해야 할 일”을 통합 JSON으로 생성하는 에이전트

[입력]
- eTL 웹 페이지 (Playwright 크롤링)
- 로그인 세션 (etl_auth.json 필요)

[처리 흐름]
1. 각 과목(course_id) 순회
2. modules / assignments / 공지 / 토론 진입점 탐색
3. 과제 상세 페이지에서 마감일 + 제출 상태 추출
4. 제출 안 했고 마감 있는 항목만 필터링
5. 동일 과제 그룹핑 후 과목별 결과 생성
6. 전체 과목 통합하여 today_tasks.json 생성

[출력]
- today_tasks.json (과목별 pending task 목록)
- 콘솔 브리프 출력

[주의]
- etl_auth.json 세션 파일은 Git에 포함하면 안 됨
- 세션 만료 시 etl_login_save_session.py로 재생성 필요
"""

import json
import os
import re
from playwright.sync_api import sync_playwright
from datetime import datetime

# 설정값
ETL_BASE = "https://myetl.snu.ac.kr"
# eTL 과목 ID는 myetl.snu.ac.kr/courses/<ID> URL에서 확인하세요.
# 본인 수강 과목으로 교체 후 사용하세요.
COURSE_IDS = ["294226", "294222", "299870", "294224"]
COURSE_NAMES = {
    "294226": "인간-AI 상호작용 이론 및 실습",
    "294222": "디지털음향의 이해",
    "299870": "인간생명과학개론",
    "294224": "정보문화세미나"
}

AUTH_FILE = "etl_auth.json"
ASSIGNMENT_KEYWORDS = ["과제", "assignment", "제출", "HW", "homework"]
TEST_MODE = False
DEBUG = False

# 마감일 필터링 설정 (일 단위)
NEAR_TERM_THRESHOLD_DAYS = 7   # 7일 이내면 '예정(Upcoming)'으로 분류
UPCOMING_WINDOW_DAYS = 90     # 90일 이내의 일감을 수집 대상으로 유지 (학기 말 일정 포함용)
RECENTLY_OVERDUE_DAYS = 7      # 최근 1주일 이내 마감된 미제출 과제만 표시

# 학기 정보 설정 (주차 기반 추론용)
SEMESTER_START_DATE = "2026-03-02" # 2026년 1학기 개강일 기준

def get_week_number_from_title(title):
    """
    모듈 제목(예: '9주차', 'Week 10')에서 주차 숫자를 추출합니다.
    """
    match = re.search(r"(\d+)주차|Week\s*(\d+)", title, re.IGNORECASE)
    if match:
        return int(match.group(1) or match.group(2))
    return None

def scan_course_entry_points(page):

    """
    과목 사이드바에서 접근 가능한 메뉴와 URL 패턴을 수집하여 확장 가능성을 확인합니다.
    (리팩토링 전 진입점 조사를 위한 최소 기능)
    """
    print("\n[Exploration] 사이드바 메뉴 분석 중...")
    entry_points = []

    # Canvas/eTL 표준 사이드바 셀렉터
    selectors = ["#section-tabs a"]

    found = []

    for selector in selectors:
        items = page.query_selector_all(selector)
        if DEBUG:
            print(f"[debug] selector='{selector}' -> {len(items)}개")

        for item in items:
            try:
                title = item.inner_text().strip()
                href = item.get_attribute("href")

                if href and href.startswith("/"):
                    href = ETL_BASE + href

                if title and href:
                    found.append((selector, title, href))
            except:
                pass

        if DEBUG:
            print("\n[debug] 발견된 링크 일부:")
            for row in found[:50]:
                print("[found]", row)

    keywords = ["공지", "게시판", "과제", "토론", "파일", "페이지", "퀴즈"]

    seen = set()

    for selector, title, href in found:
        if title == "모듈":
            continue

        if any(kw.lower() in title.lower() for kw in keywords):
            key = (title, href)
            if key not in seen:
                seen.add(key)
                entry_points.append(
                    {"title": title, "url": href, "source_selector": selector}
                )

    return entry_points

def classify_event_type(title):
    t = title.lower()

    # 과제
    if any(k in t for k in ["과제", "assignment", "제출"]):
        return "assignment"

    # 시험
    if any(k in t for k in ["시험", "중간", "기말", "quiz"]):
        return "exam"

    # 일정 (🔥 확장)
    if any(k in t for k in [
        "출석", "수업", "시간",
        "발표", "일정", "장소", "특강",
        "운영", "변경"
    ]):
        return "schedule"

    return "general"

def scan_entry_point_for_clues(context, entry_point):
    """
    특정 진입점(공지, 게시판 등)에 접속하여
    링크/항목 제목 단위로 과제 관련 단서가 있는지 확인합니다.
    """
    keywords = ASSIGNMENT_KEYWORDS + ["마감", "deadline"]

    result = {
        "title": entry_point["title"],
        "url": entry_point["url"],
        "has_clue": False,
        "matched_keywords": [],
        "matched_items": []
    }

    page = context.new_page()
    try:
        page.goto(entry_point["url"], wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(1000)

        # 페이지 안의 링크/항목 텍스트를 개별적으로 수집
        candidates = []

        # 1) 링크 텍스트
        for el in page.query_selector_all("a"):
            try:
                text = el.inner_text().strip()
                href = el.get_attribute("href")

                if not text or len(text) < 2:
                    continue

                # 상대경로를 절대경로처럼 비교하기 쉽게 유지
                href_str = href or ""

                # 공통 내비게이션/메뉴 링크 제거
                if "/assignments" in href_str and text.strip() == "과제":
                    continue

                candidates.append({
                    "text": text,
                    "href": href
                })
            except:
                pass

        matched_keywords = set()
        matched_items = []

        for item in candidates:
            text_lower = item["text"].lower()
            item_matched = [kw for kw in keywords if kw.lower() in text_lower]

            if item_matched:
                matched_keywords.update(item_matched)
                matched_items.append({
                    "text": item["text"],
                    "href": item["href"],
                    "matched_keywords": item_matched
                })

        if matched_items:
            result["has_clue"] = True
            result["matched_keywords"] = sorted(list(matched_keywords))
            result["matched_items"] = matched_items[:10]  # 너무 길어지지 않게 상위 10개만

    except Exception as e:
        print(f"  [Error] {entry_point['title']} 스캔 실패: {e}")
    finally:
        page.close()

    return result


def check_page_for_assignment_clues(page, url):
    """
    특정 페이지(예: 공지사항 리스트)의 본문을 훑어 과제 관련 키워드가 있는지 확인합니다.
    (실제 모든 상세 페이지를 클릭하지 않고, 리스트 상태의 텍스트만 확인하는 용도)
    """
    # 탐색 단계에서 선별적으로 호출하도록 설계됨
    content = page.locator("body").inner_text()
    found_keywords = [kw for kw in ASSIGNMENT_KEYWORDS if kw in content]
    return {"url": url, "clues": found_keywords, "has_clue": len(found_keywords) > 0}


def extract_due_date_string(text, body_text=""):
    """
    eTL의 마감일 텍스트에서 날짜와 시간 부분만 추출합니다.
    특정 영역(text)에서 실패하면 전체 본문(body_text)에서 다시 시도합니다.
    """
    if not text and not body_text:
        return "정보 없음"

    # 1) 우선 특정 영역(student-assignment-overview 등)에서 추출
    if text:
        match = re.search(r"(\d+월\s*\d+일).*?((?:오전|오후)\s*\d+:\d+)", text)
        if match: return f"{match.group(1)} {match.group(2)}"

        match_date_only = re.search(r"(\d+월\s*\d+일)", text)
        if match_date_only: return match_date_only.group(1)

    # 2) 본문 전체에서 "마감: 4월 23일 23:59" 같은 패턴 찾기 (퀴즈 대응)
    if body_text:
        # 다양한 마감일 표기 패턴
        patterns = [
            r"(?:마감|기한|due|until).*?(\d{1,2}월\s*\d{1,2}일).*?((?:오전|오후)\s*\d{1,2}:\d{2})",
            r"(?:마감|기한|due|until).*?(\d{1,2}월\s*\d{1,2}일)\s*(\d{1,2}:\d{2})",
            r"(?:마감|기한|due|until).*?(\d{1,2}월\s*\d{1,2}일)",
            r"(\d{1,2}월\s*\d{1,2}일).*?(?:까지|마감)"
        ]
        for p in patterns:
            m = re.search(p, body_text, re.IGNORECASE)
            if m:
                if len(m.groups()) >= 2 and m.group(2):
                    return f"{m.group(1)} {m.group(2)}"
                return m.group(1)

    if text:
        clean_text = text.replace("마감", "", 1).split("점수")[0].strip()
        return clean_text if clean_text else "날짜 형식 미지원"
    return "정보 없음"


def extract_submission_debug_text(body_text):
    """
    제출 관련 키워드가 들어간 줄만 모아서 디버깅용으로 반환
    """
    keywords = ["제출", "미제출", "다시 제출", "재제출", "시도", "채점", "점수"]
    lines = body_text.splitlines()

    matched = []
    for line in lines:
        clean = line.strip()
        if not clean:
            continue
        if any(kw in clean for kw in keywords):
            matched.append(clean)

    return matched[:15]


def classify_submission_status(body_text):
    """
    과제 상세 페이지 전체 텍스트를 바탕으로 제출 상태를 판별합니다.
    """
    if not body_text:
        return "확인 필요"

    text = body_text.strip()
    if "제출됨!" in text:
        if "과제 다시 제출" in text:
            return "제출 완료(재제출 가능)"
        return "제출 완료"

    if "미제출" in text:
        return "미제출"

    if "과제 다시 제출" in text:
        return "재제출 가능"

    return "확인 필요"


def resolve_day_of_week_to_date(day_name, hour=23, minute=59):
    """
    '목요일' 등의 텍스트를 현재 날짜 기준 가장 가까운 미래의 해당 요일 날짜로 변환
    """
    days_map = {"월": 0, "화": 1, "수": 2, "목": 3, "금": 4, "토": 5, "일": 6}
    target_day = -1
    for k, v in days_map.items():
        if k in day_name:
            target_day = v
            break
    
    if target_day == -1:
        return None
    
    now = datetime.now()
    current_day = now.weekday()
    
    # 오늘이 월(0)이고 타겟이 목(3)이면 3일 뒤
    # 오늘이 금(4)이고 타겟이 목(3)이면 6일 뒤 (다음주 목요일)
    days_ahead = target_day - current_day
    if days_ahead < 0:
        days_ahead += 7
    elif days_ahead == 0 and now.hour > hour: # 오늘인데 이미 시간이 지났으면 다음주
        days_ahead = 7
        
    from datetime import timedelta
    target_dt = now + timedelta(days=days_ahead)
    return target_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)


def parse_due_date_to_datetime(due_str, year=None):
    """
    다양한 날짜 형식을 지원하는 통합 마감일 분석 함수
    """
    if not due_str or due_str in ["정보 없음", "날짜 형식 미지원", "마감일 없음"]:
        return None

    if year is None:
        year = datetime.now().year

    s = due_str.strip()

    # 1) 표준 eTL 형식: 4월 21일 오후 8시
    m = re.search(
        r"(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?\s*(오전|오후)\s*(\d{1,2})(?::(\d{2}))?\s*시?",
        s
    )
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        ampm = m.group(3)
        hour = int(m.group(4))
        minute = int(m.group(5)) if m.group(5) else 0

        if ampm == "오후" and hour != 12:
            hour += 12
        elif ampm == "오전" and hour == 12:
            hour = 0

        return datetime(year, month, day, hour, minute)

    # 2) 슬래시 형식: 4/30 23:59 또는 4/30
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if m:
        month = int(m.group(1))
        day = int(m.group(2))
        hour = int(m.group(3)) if m.group(3) else 23
        minute = int(m.group(4)) if m.group(4) else 59
        return datetime(year, month, day, hour, minute)

    # 3) 요일 기반 상대 날짜 (목요일 기준 23:59)
    m = re.search(r"([월화수목금토일])요일.*?(\d{1,2}):(\d{2})", s)
    if m:
        return resolve_day_of_week_to_date(m.group(1), int(m.group(2)), int(m.group(3)))

    # 4) 대시 형식: 2026-04-30
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 23, 59)

    # 5) 한글 월/일만 있는 경우: 4월 23일
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일", s)
    if m:
        return datetime(year, int(m.group(1)), int(m.group(2)), 23, 59)

    return None


def classify_due_status(due_str):
    due_dt = parse_due_date_to_datetime(due_str)
    if due_dt is None:
        return "no_due_date"

    now = datetime.now()
    diff_days = (due_dt - now).days

    if due_dt < now:
        if abs(diff_days) <= RECENTLY_OVERDUE_DAYS:
            return "urgent"  # 연체된 것도 긴급으로 분류
        return "past_overdue"
    elif due_dt.date() == now.date():
        return "urgent"      # 오늘 마감은 긴급
    elif diff_days <= NEAR_TERM_THRESHOLD_DAYS:
        return "upcoming"    # 7일 이내는 예정
    elif diff_days <= UPCOMING_WINDOW_DAYS:
        return "future"      # 그 이상은 나중 일
    else:
        return "far_future"

def classify_announcement_time_status(dt):
    """
    공지의 normalized datetime을 기준으로
    past / urgent / upcoming / future / far_future / unknown 판정
    """
    if dt is None:
        return "unknown"

    now = datetime.now()
    diff_days = (dt - now).days

    if dt.date() < now.date():
        return "past"
    elif dt.date() == now.date():
        return "urgent"
    elif diff_days <= NEAR_TERM_THRESHOLD_DAYS:
        return "upcoming"
    elif diff_days <= UPCOMING_WINDOW_DAYS:
        return "future"
    else:
        return "far_future"


def inspect_assignment_detail(page, candidate):
    """
    과제 상세 페이지에 접속하여 정보를 추출합니다.
    """
    if DEBUG:
        print(f"상세 페이지 분석 중: {candidate['title']}")
    page.goto(candidate["url"], wait_until="networkidle")

    due_info_el = page.query_selector(".student-assignment-overview")
    raw_text = due_info_el.inner_text() if due_info_el else ""

    body_text = page.locator("body").inner_text()
    submission_debug = extract_submission_debug_text(body_text)
    submission_status = classify_submission_status(body_text)

    due_date_str = extract_due_date_string(raw_text)
    due_date_obj = parse_due_date_to_datetime(due_date_str)
    
    # 만약 상세 페이지에서 날짜 추출이 안 됐는데 목록 페이지 정보가 있다면 fallback
    if (not due_date_obj or due_date_obj is None) and candidate.get("list_due_date"):
        if DEBUG:
            print(f"상세 페이지 날짜 추출 실패, 목록 페이지 정보 사용: {candidate['list_due_date']}")
        due_date_str = candidate["list_due_date"]
        due_date_obj = parse_due_date_to_datetime(due_date_str)

    due_status = classify_due_status(due_date_str)

    result = {
        "module_title": candidate.get("module_title"),
        "title": candidate.get("title"),
        "source_url": candidate.get("url"),
        "final_url": page.url,
        "due_date_parsed": due_date_str,
        "due_datetime": (
            due_date_obj.strftime("%Y-%m-%d %H:%M") if due_date_obj else None
        ),
        "due_status": due_status,
        "submission_status": submission_status,
        "page_type": "assignment",
    }

    return result


def get_module_data(page):
    """페이지에서 모듈 정보를 추출합니다."""
    results = []
    modules = page.query_selector_all("div.context_module")

    for module in modules:
        title_el = module.query_selector(".ig-header-title")
        module_title = title_el.inner_text().strip() if title_el else "Unknown Module"
        
        # 주차 정보 추출
        week_num = get_week_number_from_title(module_title)

        item_elements = module.query_selector_all("li.context_module_item")
        items = []

        for el in item_elements:
            link_el = el.query_selector("a.ig-title")
            item_class = el.get_attribute("class") or ""

            # 목록 페이지에 표시된 마감일 수집 시도
            list_due_date = None
            due_el = el.query_selector(".due_date_display")
            if due_el:
                list_due_date = due_el.inner_text().strip()

            if link_el:
                href = link_el.get_attribute("href")
                if not href:
                    continue

                if href.startswith("/"):
                    href = ETL_BASE + href

                items.append(
                    {
                        "title": link_el.inner_text().strip(),
                        "url": href,
                        "type": item_class,
                        "module_title": module_title,
                        "module_week": week_num, # 추가
                        "list_due_date": list_due_date
                    }
                )

        results.append({
            "module_title": module_title, 
            "module_week": week_num, # 추가
            "items": items
        })
    return results


def scan_assignments_page(context, assignments_url):
    print(f"\n[Assignments] 페이지 분석 중: {assignments_url}")

    temp_page = context.new_page()
    temp_page.goto(assignments_url, wait_until="networkidle")

    items = temp_page.query_selector_all("a")
    results = []

    for item in items:
        try:
            title = item.inner_text().strip()
            href = item.get_attribute("href")

            if not title or not href:
                continue

            if "/assignments/" in href and "courses" in href:
                if href and href.startswith("/"):
                    href = ETL_BASE + href

                results.append({"title": title, "url": href})
        except:
            pass

    unique = {}
    for r in results:
        unique[r["url"]] = r

    results = list(unique.values())

    temp_page.close()

    print(f"[Assignments] 발견된 과제 링크 수: {len(results)}")
    return results


def scan_quizzes_page(context, quizzes_url):
    print(f"\n[Quizzes] 페이지 분석 중: {quizzes_url}")
    temp_page = context.new_page()
    try:
        temp_page.goto(quizzes_url, wait_until="networkidle", timeout=15000)
        
        # 퀴즈 목록의 각 행(row)을 순회하며 제목, 링크, 마감일을 수집
        rows = temp_page.query_selector_all("tr.quiz")
        results = []
        
        for row in rows:
            try:
                title_el = row.query_selector("a.quiz-title")
                due_el = row.query_selector("td.due_date")
                
                if title_el:
                    title = title_el.inner_text().strip()
                    href = title_el.get_attribute("href")
                    list_due_date = due_el.inner_text().strip() if due_el else None
                    
                    if href.startswith("/"):
                        href = ETL_BASE + href
                        
                    results.append({
                        "title": title,
                        "url": href,
                        "list_due_date": list_due_date
                    })
            except:
                pass
                
        # 행 기반 수집 실패 시 링크 기반으로 폴백
        if not results:
            items = temp_page.query_selector_all("a")
            for item in items:
                try:
                    title = item.inner_text().strip()
                    href = item.get_attribute("href")
                    if not title or not href: continue
                    if "/quizzes/" in href and "courses" in href:
                        if href.startswith("/"): href = ETL_BASE + href
                        results.append({"title": title, "url": href})
                except: pass
        
        unique = {r["url"]: r for r in results}
        return list(unique.values())
    finally:
        temp_page.close()


def merge_assignment_sources(module_results, assignment_page_results, quiz_page_results=[]):
    """
    modules, assignments, quizzes 결과를 URL 기준으로 통합합니다.
    """
    merged = {}
    for item in module_results:
        key = item.get("final_url") or item.get("source_url") or item.get("url")
        if key:
            key = key.split("?")[0]
            merged[key] = item

    for item in assignment_page_results + quiz_page_results:
        key = item.get("url")
        if not key: continue
        key = key.split("?")[0]
        if "/assignments/syllabus" in key: continue
        if key not in merged:
            merged[key] = {
                "title": item.get("title"),
                "final_url": item.get("url"),
                "page_type": "assignment_from_index",
                "source": "index_page",
            }
    return list(merged.values())


def inspect_assignment_detail_by_url(page, url, title=None, list_due_date=None):
    """
    URL로 직접 접근하여 상세 정보를 추출합니다. (퀴즈/과제 공용)
    """
    page.goto(url, wait_until="networkidle", timeout=20000)

    try:
        page_title = page.locator("h1").inner_text().strip()
    except:
        page_title = title

    # 퀴즈와 과제 모두 대응 가능한 셀렉터들
    due_info_el = page.query_selector(".student-assignment-overview, .quiz-sidebar-info, .quiz-deadlines")
    raw_text = due_info_el.inner_text() if due_info_el else ""
    body_text = page.locator("body").inner_text()
    
    submission_status = classify_submission_status(body_text)

    # 본문 기반 추출까지 포함하여 시도
    due_date_parsed = extract_due_date_string(raw_text, body_text)
    due_date_obj = parse_due_date_to_datetime(due_date_parsed)
    
    # 목록 페이지 마감일 정보가 있다면 폴백으로 사용
    if (not due_date_obj or due_date_obj is None) and list_due_date:
        due_date_parsed = list_due_date
        due_date_obj = parse_due_date_to_datetime(due_date_parsed)

    due_status = classify_due_status(due_date_parsed)

    return {
        "title": title or page_title,
        "page_title": page_title,
        "final_url": page.url,
        "due_date_parsed": due_date_parsed,
        "due_datetime": (
            due_date_obj.strftime("%Y-%m-%d %H:%M") if due_date_obj else None
        ),
        "due_status": due_status,
        "submission_status": submission_status,
        "page_type": "assignment",
    }

def inspect_discussion_candidate_detail(page, candidate):
    """
    토론 상세 페이지에서 마감일과 제출 상태를 텍스트 기반으로 추출합니다.
    """
    print(f"토론 상세 분석 중: {candidate['title']}")

    try:
        page.goto(candidate["url"], wait_until="networkidle", timeout=15000)

        try:
            page_title = page.locator("h1").inner_text().strip()
        except:
            page_title = candidate["title"]

        body_text = page.locator("body").inner_text()

        # 1. 마감일 추출 시도
        due_date_parsed = "정보 없음"
        due_datetime = None
        due_status = "unknown"

        # 마감/기한/제출/언제까지 같은 표현 근처의 날짜를 찾음
        due_match = re.search(
            r"(마감|기한|제출|언제까지).*?(\d+월\s*\d+일\s*(?:오전|오후)\s*\d+:\d+)",
            body_text
        )

        if due_match:
            due_date_parsed = due_match.group(2).strip()
            dt = parse_due_date_to_datetime(due_date_parsed)
            if dt:
                due_datetime = dt.strftime("%Y-%m-%d %H:%M")
                due_status = classify_due_status(due_date_parsed)

        # 2. 제출 상태
        submission_status = classify_submission_status(body_text)

        return {
            "title": candidate["title"],
            "page_title": page_title,
            "final_url": page.url,
            "source": "discussion",
            "page_type": "discussion_assignment",
            "due_date_parsed": due_date_parsed,
            "due_datetime": due_datetime,
            "due_status": due_status,
            "submission_status": submission_status
        }

    except Exception as e:
        print(f"  [Error] 토론 상세 분석 실패 ({candidate['title']}): {e}")
        return {
            "title": candidate["title"],
            "final_url": candidate["url"],
            "source": "discussion",
            "page_type": "discussion_assignment_error",
            "due_date_parsed": "정보 없음",
            "due_datetime": None,
            "due_status": "unknown",
            "submission_status": "확인 필요",
            "error": str(e)
        }

def collect_discussion_assignment_candidates(context, discussion_entry):
    """
    토론 목록 페이지에서 '과제함' 키워드가 포함된 항목들을 수집합니다.
    """
    print(f"\n[Collection] 토론 페이지에서 과제 후보 수집 중: {discussion_entry['url']}")
    candidates = {}

    page = context.new_page()
    try:
        page.goto(discussion_entry["url"], wait_until="networkidle", timeout=15000)

        links = page.query_selector_all("a")

        for link in links:
            try:
                raw_text = link.inner_text().strip()
                href = link.get_attribute("href")

                if not raw_text or not href:
                    continue

                # 제목이 여러 줄이면 첫 줄만 사용
                title = raw_text.splitlines()[0].strip()

                if "과제함" not in title:
                    continue

                if href and href.startswith("/"):
                    href = ETL_BASE + href

                if href not in candidates:
                    candidates[href] = {
                        "title": title,
                        "url": href,
                        "source": "discussion",
                        "page_type": "discussion_assignment_candidate"
                    }
            except:
                pass

    except Exception as e:
        print(f"  [Error] 토론 후보 수집 실패: {e}")
    finally:
        page.close()

    return list(candidates.values())

def collect_announcement_candidates(context, announcement_entry):
    """
    공지사항 목록 페이지에서 과제/시험/일정 관련 공지 링크들을 수집합니다.
    """
    print(f"\n[Collection] 공지 페이지에서 이벤트 후보 수집 중: {announcement_entry['url']}")

    EVENT_KEYWORDS = [
        "과제", "assignment", "제출", "마감", "deadline",
        "시험", "중간", "기말", "quiz", "응시",
        "일정", "시간", "출석", "변경",
        "확인", "공지"
    ]

    candidates = {}

    page = context.new_page()
    try:
        page.goto(announcement_entry["url"], wait_until="networkidle", timeout=15000)

        links = page.query_selector_all("a")

        for link in links:
            try:
                raw_text = link.inner_text().strip()
                href = link.get_attribute("href")

                if not raw_text or not href:
                    continue

                title = raw_text.splitlines()[0].strip()

                if title in ["공지", "과제", "Assignments"]:
                    continue

                has_keyword = any(kw.lower() in title.lower() for kw in EVENT_KEYWORDS)
                if not has_keyword:
                    continue

                if href.startswith("/"):
                    href = ETL_BASE + href

                if href not in candidates:
                    candidates[href] = {
                        "title": title,
                        "url": href,
                        "source": "announcement",
                        "page_type": "announcement_candidate",
                        "event_type": classify_event_type(title)
                    }
            except:
                pass

    except Exception as e:
        print(f"  [Error] 공지 후보 수집 실패: {e}")
    finally:
        page.close()

    return list(candidates.values())

def extract_announcement_main_text(page):
    """
    공지 상세 페이지에서 '본문 영역'만 우선적으로 추출합니다.
    body 전체를 읽지 않고, 공지 내용이 들어 있을 가능성이 높은 selector를 순서대로 시도합니다.
    """
    candidate_selectors = [
        "main .message.user_content",   # Canvas 계열 공지 본문에서 자주 보이는 패턴
        "main .user_content",
        "main .message",
        "main .ic-Layout-contentMain .content",
        "main .show-content",
        "main .announcement",
        "article",
        "main"
    ]

    for selector in candidate_selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() == 0:
                continue

            text = loc.inner_text(timeout=3000).strip()
            if text and len(text) >= 30:
                return {
                    "body_text": text,
                    "body_selector": selector
                }
        except Exception:
            continue

    # 최후 fallback
    try:
        fallback_text = page.locator("main").inner_text(timeout=3000).strip()
        if fallback_text:
            return {
                "body_text": fallback_text,
                "body_selector": "main(fallback)"
            }
    except Exception:
        pass

    return {
        "body_text": "",
        "body_selector": None
    }


def inspect_announcement_detail(page, candidate):
    """
    공지 상세 페이지에 들어가 제목/본문을 읽고 이벤트 성격을 다시 판별합니다.
    """
    print(f"[DEBUG] 공지 상세 분석 시도: {candidate['title']}")

    try:
        page.goto(candidate["url"], wait_until="domcontentloaded", timeout=15000)
        page.wait_for_load_state("networkidle", timeout=10000)
        page.wait_for_timeout(1200)

        title_selectors = ["main h1", "h1.title", "h1"]
        page_title = candidate["title"]

        for selector in title_selectors:
            try:
                loc = page.locator(selector).first
                if loc.count() == 0:
                    continue

                text = loc.inner_text(timeout=2000).strip()
                if text:
                    page_title = text
                    break
            except Exception:
                continue

        body_info = extract_announcement_main_text(page)
        body_text = body_info["body_text"]
        body_selector = body_info["body_selector"]

        print("[DEBUG] body selector:", body_selector)
        print("[DEBUG] body length:", len(body_text))
        print("[DEBUG] preview:", body_text[:200])

        detected_event_type = classify_announcement_by_content(page_title, body_text)
        detected_date = extract_announcement_date(f"{page_title}\n{body_text}")

        detected_dt = parse_announcement_date_to_datetime(detected_date)
        time_status = classify_announcement_time_status(detected_dt)

        # 기본 confidence
        confidence = "low"
        if body_selector and detected_event_type != "general":
            confidence = "medium"
        if body_selector == "article" and detected_event_type != "general" and detected_date:
            confidence = "high"

        # 실행 필요 여부
        is_actionable = False
        if detected_event_type in ["assignment", "exam"] and time_status in ["urgent", "upcoming", "future", "unknown"]:
            is_actionable = True
        elif detected_event_type == "schedule" and time_status in ["urgent", "upcoming", "future"]:
            is_actionable = True

        return {
            "title": candidate["title"],
            "page_title": page_title,
            "url": page.url,
            "source": "announcement",
            "page_type": "announcement_detail",
            "event_type": detected_event_type,
            "detected_date": detected_date,
            "normalized_datetime": (
                detected_dt.strftime("%Y-%m-%d %H:%M") if detected_dt else None
            ),
            "time_status": time_status,
            "is_actionable": is_actionable,
            "confidence": confidence,
            "body_selector": body_selector,
            "body_preview": body_text[:300]
        }

    except Exception as e:
        print(f"  [Error] 공지 상세 분석 실패 ({candidate['title']}): {e}")
        print(f"[DEBUG] 실패한 공지: {candidate['title']}")
        return {
            "title": candidate["title"],
            "url": candidate["url"],
            "source": "announcement",
            "page_type": "announcement_detail_error",
            "event_type": candidate.get("event_type", "general"),
            "detected_date": None,
            "normalized_datetime": None,
            "time_status": "unknown",
            "is_actionable": False,
            "confidence": "low",
            "body_selector": None,
            "error": str(e)
        }

def classify_announcement_by_content(title, body_text):
    combined = f"{title}\n{body_text}".lower()

    exam_keywords = ["중간고사", "기말고사", "시험", "응시", "고사"]
    schedule_keywords = ["발표", "일정", "장소", "강의실", "특강", "운영", "변경", "출석", "수업", "시간"]
    assignment_keywords = ["과제", "제출", "제출함", "마감", "deadline", "assignment"]

    if any(k in combined for k in exam_keywords):
        return "exam"

    if any(k in combined for k in schedule_keywords):
        return "schedule"

    if any(k in combined for k in assignment_keywords):
        return "assignment"

    return "general"


def extract_announcement_date(text):
    """
    공지 본문/제목에서 날짜/시간 문자열을 우선순위 기반으로 추출합니다.
    """
    if not text:
        return None

    clean = " ".join(text.split())

    patterns = [
        # 1) 한글 월/일 + 오전/오후 + 시/분
        r"\d{1,2}월\s*\d{1,2}일\s*(?:\([^)]*\))?\s*(?:오전|오후)\s*\d{1,2}(?::\d{2})?\s*시?",
        
        # 2) 한글 월/일 + 24시간 표기 (13:00)
        r"\d{1,2}월\s*\d{1,2}일\s*(?:\([^)]*\))?\s*\d{1,2}:\d{2}",
        
        # 3) 슬래시 형식 (4/28 13:00)
        r"\d{1,2}/\d{1,2}\s*(?:\([^)]*\))?\s*\d{1,2}:\d{2}",
        
        # 4) 슬래시 날짜 + PM/AM
        r"\d{2,4}/\d{1,2}/\d{1,2}\s*(?:AM|PM|am|pm)\s*\d{1,2}(?::\d{2})?",
        
        # 5) 한글 월/일만
        r"\d{1,2}월\s*\d{1,2}일",
        
        # 6) 슬래시 날짜만
        r"\d{1,2}/\d{1,2}",
    ]

    for pattern in patterns:
        match = re.search(pattern, clean)
        if match:
            return match.group(0).strip()

    return None

def group_pending_tasks(tasks):
    """
    동일 제목 + 동일 마감인 과제를 하나로 묶습니다.
    URL은 links 리스트로 보존합니다.
    """
    grouped = {}

    for item in tasks:
        title = (item.get("title") or "").strip()
        due_date = item.get("due_date_parsed") or "정보 없음"
        key = (title, due_date)

        if key not in grouped:
            grouped[key] = {
                "title": title,
                "due_date_parsed": due_date,
                "due_datetime": item.get("due_datetime"),
                "due_status": item.get("due_status"),
                "submission_status": item.get("submission_status"),
                "links": [item.get("final_url")],
                "count": 1,
            }
        else:
            grouped[key]["count"] += 1
            link = item.get("final_url")
            if link and link not in grouped[key]["links"]:
                grouped[key]["links"].append(link)

    return list(grouped.values())

def normalize_korean_text_for_match(text):
    """
    제목 유사도 비교를 위한 단순 정규화
    중요 키워드(중간, 기말, 시험)는 보존합니다.
    """
    if not text:
        return ""

    text = text.lower().strip()
    # 특수문자 제거
    text = re.sub(r"[\s\(\)\[\]\{\},.!?~\-_/]+", "", text)

    # 단순 안내성 단어만 제거
    removable = ["안내", "게시", "공지", "관련", "알림"]
    for word in removable:
        text = text.replace(word, "")

    return text


def is_probably_same_assignment(announcement_item, confirmed_assignment):
    """
    공지 기반 후보와 확정 과제가 같은 할 일인지 판정
    """
    a_title = announcement_item.get("title", "")
    c_title = confirmed_assignment.get("title", "")

    a_date = announcement_item.get("detected_date")
    c_date = confirmed_assignment.get("due_date_parsed")

    norm_a = normalize_korean_text_for_match(a_title)
    norm_c = normalize_korean_text_for_match(c_title)

    # 1) 제목 핵심 단어 겹침 (시험 관련은 더 가중치)
    keyword_pairs = [
        ("중간", "중간"),
        ("기말", "기말"),
        ("시험", "시험"),
        ("고사", "고사"),
        ("exam", "exam"),
        ("퀴즈", "퀴즈"),
        ("보고서", "보고서"),
        ("과제", "과제"),
    ]
    
    keyword_match = False
    for k1, k2 in keyword_pairs:
        if k1 in a_title and k2 in c_title:
            keyword_match = True
            break

    # 2) 날짜 힌트 확인
    same_date_hint = False
    if a_date and c_date and c_date != "정보 없음":
        if any(token in c_date for token in re.findall(r"\d+월|\d+일|\d{1,2}:\d{2}", a_date)):
            same_date_hint = True
    
    # 3) 판정 로직
    # 시험/고사의 경우 제목 키워드만 맞아도 (날짜 정보가 한쪽에만 있어도) 병합 대상으로 고려
    is_exam = any(k in a_title or k in c_title for k in ["중간", "기말", "시험", "고사"])
    
    if is_exam and keyword_match:
        return True
        
    # 일반 과제는 날짜 힌트가 있거나 제목이 매우 유사해야 함
    if same_date_hint and keyword_match:
        return True
        
    if norm_a and norm_c and (norm_a in norm_c or norm_c in norm_a) and len(norm_a) > 2:
        return True

    return False

def parse_announcement_date_to_datetime(date_str, year=None):
    """
    공지에서 추출한 날짜 문자열을 datetime으로 변환합니다.
    '24:00' 표기를 '23:59'로 변환하는 예외 처리를 포함합니다.
    """
    if not date_str:
        return None

    if year is None:
        year = datetime.now().year

    # 24:00 -> 23:59 변환
    s = date_str.replace("24:00", "23:59").strip()

    # 1) 26/04/23 PM 2시 등
    m = re.search(r"(\d{2,4})/(\d{1,2})/(\d{1,2})\s*(AM|PM|am|pm)\s*(\d{1,2})(?::(\d{2}))?\s*시?", s)
    if m:
        y = int(m.group(1))
        if y < 100: y += 2000
        month, day = int(m.group(2)), int(m.group(3))
        ampm, hour = m.group(4).lower(), int(m.group(5))
        minute = int(m.group(6)) if m.group(6) else 0
        if ampm == "pm" and hour != 12: hour += 12
        elif ampm == "am" and hour == 12: hour = 0
        return datetime(y, month, day, hour, minute)

    # 2) 표준 형식
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?\s*(오전|오후)\s*(\d{1,2})(?::(\d{2}))?\s*시?", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        ampm, hour = m.group(3), int(m.group(4))
        minute = int(m.group(5)) if m.group(5) else 0
        if ampm == "오후" and hour != 12: hour += 12
        elif ampm == "오전" and hour == 12: hour = 0
        return datetime(year, month, day, hour, minute)

    # 3) 24시간 형식
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?\s*(\d{1,2}):(\d{2})", s)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        hour, minute = int(m.group(3)), int(m.group(4))
        return datetime(year, month, day, hour, minute)

    # 4) 월/일만
    m = re.search(r"(\d{1,2})월\s*(\d{1,2})일(?:\([^)]*\))?", s)
    if m:
        return datetime(year, int(m.group(1)), int(m.group(2)), 12, 0)

    return None


def run_for_course(course_id):
    base_course_url = f"{ETL_BASE}/courses/{course_id}"
    target_url = f"{base_course_url}/modules"

    if not os.path.exists(AUTH_FILE):
        print(f"Error: {AUTH_FILE} 파일이 없습니다.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=AUTH_FILE)
        page = context.new_page()

        print(f"\n==============================")
        print(f"과목 실행 시작: {course_id}")
        print(f"접속 중: {target_url}...")
        page.goto(target_url, wait_until="networkidle")

        if "nsso.snu.ac.kr" in page.url or "login" in page.url:
            print("\n[AUTH ERROR] eTL 세션 만료 감지")
            print("[AUTH ERROR] etl_auth.json이 더 이상 유효하지 않습니다.")
            print("[ACTION REQUIRED] etl_login_save_session.py를 다시 실행하세요.")
            print(f"[DEBUG] 현재 URL: {page.url}")

            browser.close()

            return {
                "course_id": course_id,
                "error": "SESSION_EXPIRED",
                "message": "eTL session expired - re-login required",
                "action_required": True
            }

        if DEBUG:
            print(f"[debug] 현재 URL: {page.url}")
            print(f"[debug] 현재 제목: {page.title()}")

            body_text = page.locator("main").inner_text().strip()
            print(f"[debug] body 길이: {len(body_text)}")
            print("[debug] body 앞부분:")
            print(body_text[:1000])

        # 0. 확장 가능성 탐색 (추가)
        entry_points = scan_course_entry_points(page)
        print(f"발견된 잠재적 진입점: {[e['title'] for e in entry_points]}")

                # 0.5 과제 단서 추가 탐색
        clue_results = []
        exclude_titles = ["모듈", "과제", "Modules", "Assignments"]

        print("\n[Exploration] 진입점별 과제 단서 스캔 시작...")
        for ep in entry_points:
            # 이미 분석 중인 '모듈'과 '과제' 페이지는 제외
            if ep["title"].strip() in exclude_titles:
                continue

            print(f"  스캔 중: {ep['title']}...")
            clue_info = scan_entry_point_for_clues(context, ep)
            clue_results.append(clue_info)

        discussion_candidates = []
        discussion_entry = next(
            (ep for ep in entry_points if "토론" in ep["title"] or "Discussions" in ep["title"]),
            None
        )

        if discussion_entry:
            discussion_candidates = collect_discussion_assignment_candidates(context, discussion_entry)

        print("\n--- 토론 기반 과제 후보 ---")
        print(json.dumps(discussion_candidates, indent=2, ensure_ascii=False))

        discussion_detailed_results = []
        if discussion_candidates:
            disc_page = context.new_page()
            try:
                for candidate in discussion_candidates:
                    detail = inspect_discussion_candidate_detail(disc_page, candidate)
                    if detail:
                        discussion_detailed_results.append(detail)
            finally:
                disc_page.close()

        print("\n--- 토론 상세 분석 결과 ---")
        print(json.dumps(discussion_detailed_results, indent=2, ensure_ascii=False))

                # 0.9 공지 기반 후보 수집
        announcement_candidates = []
        announcement_entry = next(
            (ep for ep in entry_points if "공지" in ep["title"] or "Announcements" in ep["title"]),
            None
        )

        if announcement_entry:
            announcement_candidates = collect_announcement_candidates(context, announcement_entry)

        announcement_detailed_results = []

        if announcement_candidates:
            announcement_page = context.new_page()
            try:
                for candidate in announcement_candidates:
                    detail = inspect_announcement_detail(announcement_page, candidate)
                    announcement_detailed_results.append(detail)
            finally:
                announcement_page.close()

        print("\n--- 공지 기반 후보 ---")
        print(json.dumps(announcement_candidates, indent=2, ensure_ascii=False))

        important_announcement_tasks = []
        important_schedule_tasks = []

        for item in announcement_detailed_results:
            event_type = item.get("event_type")
            time_status = item.get("time_status", "unknown")
            is_actionable = item.get("is_actionable", False)

            if event_type == "exam":
                # 시험은 오늘/예정/나중/모름 상태일 때만 중요 공지로 분류
                if time_status in ["urgent", "upcoming", "future", "unknown"]:
                    important_announcement_tasks.append(item)

            elif event_type == "schedule":
                # 일정은 actionable한 것만 남김 (urgent, upcoming, future)
                if is_actionable:
                    important_schedule_tasks.append(item)

        # (기존의 무조건적인 exam fallback 루프 제거 - 상세 분석 결과를 따르도록 함)

        print("\n--- 추가 탐색 (과제 단서) ---")
        print(json.dumps(clue_results, indent=2, ensure_ascii=False))

        assignments_entry = None

        for ep in entry_points:
            if ep["title"] == "과제":
                assignments_entry = ep
                break

        assignment_page_results = []
        if assignments_entry:
            assignment_page_results = scan_assignments_page(
                context, assignments_entry["url"]
            )

        # 0.95 퀴즈 전용 페이지 스캔
        quiz_page_results = []
        quizzes_entry = next((ep for ep in entry_points if ep["title"] == "퀴즈"), None)
        if quizzes_entry:
            quiz_page_results = scan_quizzes_page(context, quizzes_entry["url"])

        # 1. 모듈 목록 가져오기
        modules = get_module_data(page)

        if DEBUG:
            print(f"\n[debug] 모듈 개수: {len(modules)}")

            for m_idx, m in enumerate(modules[:10], start=1):
                print(f"\n[debug] 모듈 {m_idx}: {m['module_title']}")
                print(f"[debug] item 개수: {len(m['items'])}")

                for item in m["items"][:10]:
                    print(
                        f"  - title='{item['title']}' | type='{item['type']}' | url='{item['url']}'"
                    )

        # 2. 과제 상세 분석
        detailed_results = []
        assignment_found = False

        for m in modules:
            for item in m["items"]:
                item_type = item["type"].lower()
                if "assignment" in item_type or "quiz" in item_type:
                    if any(
                        kw.lower() in item["title"].lower()
                        for kw in ASSIGNMENT_KEYWORDS + ["퀴즈", "시험", "quiz", "test"]
                    ):
                        if TEST_MODE and assignment_found:
                            continue

                        detail = inspect_assignment_detail(page, item)
                        detailed_results.append(detail)
                        assignment_found = True

                        if TEST_MODE:
                            break
            if TEST_MODE and assignment_found:
                break

        merged_assignments = merge_assignment_sources(
            detailed_results, assignment_page_results, quiz_page_results
        )

        for item in merged_assignments:
            if item.get("page_type") == "assignment_from_index":
                try:
                    # 퀴즈/과제 목록에서 가져온 마감일 정보가 있다면 전달
                    list_due_date = None
                    # quiz_page_results나 assignment_page_results에서 해당 URL의 list_due_date 찾기
                    for orig in assignment_page_results + quiz_page_results:
                        if orig["url"].split("?")[0] == item["final_url"].split("?")[0]:
                            list_due_date = orig.get("list_due_date")
                            break

                    detail = inspect_assignment_detail_by_url(
                        page, item["final_url"], title=item.get("title"), list_due_date=list_due_date
                    )

                    if detail:
                        item.update(detail)

                except Exception as e:
                    if DEBUG:
                        print("[ERROR] 상세 분석 실패:", e)

        # [주차 기반 추론] 현재 학기 주차 계산
        now = datetime.now()
        start_dt = datetime.strptime(SEMESTER_START_DATE, "%Y-%m-%d")
        current_week = ((now - start_dt).days // 7) + 1
        
        for item in merged_assignments:
            # 마감일이 없는데 주차 정보가 있는 경우 추론 적용
            if item.get("due_status") == "no_due_date" and item.get("module_week"):
                mod_week = item["module_week"]
                # [필터링] 현재 주차 기준 ±1주 내의 항목만 '마감일 확인 필요'로 분류
                if current_week - 1 <= mod_week <= current_week + 1:
                    item["due_status"] = "no_due_date"
                    if mod_week == current_week:
                        item["due_date_parsed"] = f"{mod_week}주차 진행 중 (마감 미정)"
                    elif mod_week < current_week:
                        item["due_date_parsed"] = f"{mod_week}주차 미완료 (마감 미정)"
                    else:
                        item["due_date_parsed"] = f"{mod_week}주차 예정 (마감 미정)"
                else:
                    # 범위를 벗어난 마감일 없는 과제는 할 일 리스트에서 제외 (너무 과거/미래)
                    item["due_status"] = "ignored_due_to_week"

        pending_tasks = []
        for item in merged_assignments:
            due_status = item.get("due_status", "")
            submission_status = item.get("submission_status", "")
            title = item.get("title", "").lower()
            
            is_submitted = submission_status.startswith("제출 완료")
            is_quiz = any(kw in title for kw in ["퀴즈", "시험", "quiz", "test", "고사"])
            
            # 마감 상태 분석
            is_past = due_status in ["past_overdue", "far_future_past"] # 사실상 지난 일
            # parse_due_date_to_datetime을 통해 현재 시점보다 이전인지 다시 한 번 체크
            due_dt_str = item.get("due_datetime")
            due_dt = datetime.strptime(due_dt_str, "%Y-%m-%d %H:%M") if due_dt_str else None
            now = datetime.now()
            
            is_overdue = due_dt and due_dt < now

            # [필터링 핵심 로직]
            actionable = False
            
            if not is_submitted:
                if is_quiz:
                    # 시험/퀴즈는 마감이 지나지 않은 경우에만 '할 일'로 인정
                    if not is_overdue and due_status in ["urgent", "upcoming", "future", "no_due_date"]:
                        actionable = True
                else:
                    # 일반 과제는 마감이 지났더라도 최근(Urgent 상태)이면 '할 일'로 표시 (미제출 경고)
                    if due_status in ["urgent", "upcoming", "future", "no_due_date"]:
                        actionable = True

            if actionable:
                pending_tasks.append(item)

        # 0. URL-주차 매핑 생성
        url_to_week = {}
        for m in modules:
            m_week = m.get("module_week")
            if m_week:
                for mi in m["items"]:
                    u = mi.get("url")
                    if u:
                        url_to_week[u.split("?")[0]] = m_week

        # 3. 토론 및 공지 기반 과제를 pending_tasks에 통합
        for item in discussion_detailed_results:
            # 토론 항목 중 '과제함' 키워드가 있거나 과제형태인 경우 메인 리스트로 승격
            if "과제함" in item.get("title", "") or item.get("page_type") == "discussion_assignment":
                
                # 주차 정보 매핑 시도
                item_url = item.get("final_url") or item.get("url")
                mod_week = None
                if item_url:
                    norm_url = item_url.split("?")[0]
                    if norm_url in url_to_week:
                        mod_week = url_to_week[norm_url]
                        item["module_week"] = mod_week

                # [필터링] 주차 기반으로 현재 날짜와 가까운 것만 추림 (최근 1주 ~ 다음 1주)
                if mod_week:
                    if not (current_week - 1 <= mod_week <= current_week + 1):
                        continue
                elif "1주차" in item.get("title", "") or "2주차" in item.get("title", "") or "3주차" in item.get("title", ""):
                    # 제목에 너무 이른 주차가 명시된 경우 제외
                    continue

                # 중복 체크 (URL 기준)
                if not any(t.get("final_url") == item.get("final_url") for t in pending_tasks):
                    # 마감일이 없는 토론 항목은 '마감일 확인 필요(no_due_date)'로 분류
                    d_status = item.get("due_status", "unknown")
                    if d_status == "unknown" or d_status == "no_due_date":
                        item["due_status"] = "no_due_date"
                        if mod_week:
                            item["due_date_parsed"] = f"{mod_week}주차 활동 (마감 미정)"
                        else:
                            item["due_date_parsed"] = "토론 참여/제출 (마감 미정)"
                    
                    pending_tasks.append({
                        "title": item.get("title"),
                        "due_date_parsed": item.get("due_date_parsed") or "마감일 미정",
                        "due_datetime": item.get("due_datetime"),
                        "due_status": item["due_status"],
                        "submission_status": item.get("submission_status") or "확인 필요",
                        "final_url": item.get("final_url"),
                        "source": "discussion",
                        "module_week": mod_week
                    })

        grouped_pending_tasks = group_pending_tasks(pending_tasks)

        supplementary_candidates = []
        supplementary_urls = set()
        supplementary_titles = set()

        # 확정 과제 URL 집합
        merged_urls = set()
        for item in merged_assignments:
            url = item.get("final_url") or item.get("url")
            if url:
                merged_urls.add(url.split("?")[0])

        # 토론 + 공지 후보를 한 번에 처리
        for item in discussion_detailed_results + announcement_detailed_results:
            if item.get("event_type") in ["exam", "schedule"]:
                continue

            # 마감일/시간 상태가 past나 far_future면 보조 후보에서도 제외
            time_status = item.get("time_status", "unknown")
            if time_status in ["past", "far_future"]:
                continue

            # 공지 기반 assignment가 이미 확정 과제와 같은지 검사
            duplicate_assignment = False
            for confirmed in merged_assignments:
                if is_probably_same_assignment(item, confirmed):
                    duplicate_assignment = True
                    # 만약 확정 과제에 날짜 정보가 없는데 공지에 있다면 업데이트 시도
                    if not confirmed.get("due_datetime") and item.get("normalized_datetime"):
                        confirmed["due_date_parsed"] = item.get("detected_date")
                        confirmed["due_datetime"] = item.get("normalized_datetime")
                        confirmed["due_status"] = classify_due_status(item.get("detected_date"))
                    break

            if duplicate_assignment:
                continue

            url = item.get("url")
            title = item.get("title")

            if not url or not title:
                continue

            normalized = url.split("?")[0]
            normalized_title = title.strip().lower()

            if normalized in merged_urls:
                continue

            if normalized in supplementary_urls:
                continue

            if normalized_title in supplementary_titles:
                continue

            supplementary_candidates.append(item)
            supplementary_urls.add(normalized)
            supplementary_titles.add(normalized_title)

        print("\n--- 확장 가능성 탐색 결과 ---")
        print(json.dumps(entry_points, indent=2, ensure_ascii=False))

        print("\n--- 기존 과제 분석 결과 (JSON) ---")
        print(json.dumps(detailed_results, indent=2, ensure_ascii=False))

        print("\n--- Assignments 페이지 결과 ---")
        print(json.dumps(assignment_page_results, indent=2, ensure_ascii=False))

        print("\n--- 통합 과제 결과 (중복 제거 후) ---")
        print(json.dumps(merged_assignments, indent=2, ensure_ascii=False))

        print("\n--- 지금 당장 해야 할 일 후보 (통합 기준) ---")
        print(json.dumps(pending_tasks, indent=2, ensure_ascii=False))

        print("\n--- 보조 후보 (토론+공지 기반) ---")
        print(json.dumps(supplementary_candidates, indent=2, ensure_ascii=False))

        print("\n--- 공지 상세 분석 결과 ---")
        print(json.dumps(announcement_detailed_results, indent=2, ensure_ascii=False))

        print("\n==============================")
        print("eTL 과제 브리프")
        print("==============================")
        print(f"COURSE_ID: {course_id}")
        print(f"확정 과제 수: {len(merged_assignments)}")
        print(f"지금 당장 해야 할 일 수: {len(grouped_pending_tasks)}")
        print(f"추가 확인 필요 후보 수: {len(supplementary_candidates)}")

        print("\n[지금 당장 해야 할 일]")
        if grouped_pending_tasks:
            for idx, item in enumerate(grouped_pending_tasks, start=1):
                print(f"{idx}. {item['title']}")
                print(f"   - 마감: {item.get('due_date_parsed')}")
                print(f"   - 제출 상태: {item.get('submission_status')}")

                if item.get("count", 1) > 1:
                    print(f"   - 묶인 항목 수: {item['count']}")

                for link_idx, link in enumerate(item.get("links", []), start=1):
                    print(f"   - 링크 {link_idx}: {link}")
        else:
            print("없음")

        print("\n[중요 공지 / 시험 일정]")
        if important_announcement_tasks:
            for idx, item in enumerate(important_announcement_tasks, start=1):
                print(f"{idx}. {item['title']}")
                print(f"   - 출처: {item.get('source')}")
                print(f"   - 링크: {item.get('url')}")
        else:
            print("없음")

        print("\n[추가 확인 필요 후보]")
        if supplementary_candidates:
            for idx, item in enumerate(supplementary_candidates, start=1):
                print(f"{idx}. {item['title']}")
                print(f"   - 출처: {item.get('source')}")
                print(f"   - 링크: {item.get('url')}")
        else:
            print("없음")
        
        result = {
            "course_id": course_id,
            "grouped_pending_tasks": grouped_pending_tasks,
            "important_announcement_tasks": important_announcement_tasks,
            "important_schedule_tasks": important_schedule_tasks,
            "supplementary_candidates": supplementary_candidates,
            "merged_assignments_count": len(merged_assignments),
        }

        browser.close()
        return result


def run():
    all_course_results = []
    auth_error_detected = False

    for course_id in COURSE_IDS:
        try:
            result = run_for_course(course_id)

            if result and result.get("error") == "SESSION_EXPIRED":
                print("\n==============================")
                print("❌ 세션 만료 감지")
                print("👉 etl_login_save_session.py 실행 후 다시 시도하세요")
                print("==============================")

                auth_error_detected = True
                all_course_results.append(result)
                break  # 이후 과목은 중단, 하지만 JSON 저장은 계속 진행

            if result:
                all_course_results.append(result)

        except Exception as e:
            print(f"\n[ERROR] 과목 실행 실패: {course_id}")
            print(str(e))

    print("\n========================================")
    print("전체 과목 통합 오늘 할 일 브리프")
    print("========================================")

    total_pending_count = 0

    for course_result in all_course_results:
        if course_result.get("error") == "SESSION_EXPIRED":
            continue
        total_pending_count += (
            len(course_result.get("grouped_pending_tasks", [])) +
            len(course_result.get("important_announcement_tasks", [])) +
            len(course_result.get("important_schedule_tasks", []))
        )

    integrated_brief = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_course_count": len(all_course_results),
        "total_pending_count": total_pending_count,
        "has_pending": total_pending_count > 0,
        "auth_error": auth_error_detected,
        "error_type": "SESSION_EXPIRED" if auth_error_detected else None,
        "courses": []
    }

    for course_result in all_course_results:
        if course_result.get("error") == "SESSION_EXPIRED":
            continue

        course_id = course_result["course_id"]
        integrated_brief["courses"].append({
            "course_id": course_id,
            "course_name": COURSE_NAMES.get(course_id, "알 수 없는 과목"),
            "pending_count": (
                len(course_result.get("grouped_pending_tasks", [])) +
                len(course_result.get("important_announcement_tasks", [])) +
                len(course_result.get("important_schedule_tasks", []))
            ),
            "pending_tasks": course_result.get("grouped_pending_tasks", []),
            "important_announcement_tasks": course_result.get("important_announcement_tasks", []),
            "important_schedule_tasks": course_result.get("important_schedule_tasks", []),
            "supplementary_candidates": course_result.get("supplementary_candidates", [])
        })

    output_path = "today_tasks.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(integrated_brief, f, ensure_ascii=False, indent=2)

    print(f"\n[Saved] 통합 오늘 할 일 브리프 저장 완료: {output_path}")
    print(f"전체 과목 수: {len(integrated_brief['courses'])}")
    print(f"전체 지금 당장 해야 할 일 수: {total_pending_count}")

    if auth_error_detected:
        print("\n[경고] eTL 세션이 만료된 상태입니다.")
        print("[경고] today_tasks.json 상단에 auth_error=true 로 기록되었습니다.")

    if total_pending_count == 0:
        print("\n지금 당장 해야 할 일 없음")
        return

    for course_result in all_course_results:
        if course_result.get("error") == "SESSION_EXPIRED":
            continue

        course_id = course_result["course_id"]
        grouped_pending_tasks = course_result.get("grouped_pending_tasks", [])
        important_announcements = course_result.get("important_announcement_tasks", [])
        important_schedules = course_result.get("important_schedule_tasks", [])

        if not grouped_pending_tasks and not important_announcements and not important_schedules:
            continue

        course_name = COURSE_NAMES.get(course_id, course_id)
        print(f"\n[과목: {course_name}]")

        # 1단계: 오늘 당장 해야 할 일 (Urgent)
        urgent_tasks = [t for t in grouped_pending_tasks if t.get("due_status") == "urgent"]
        if urgent_tasks:
            print("  🔴 [오늘 당장 해야 할 일]")
            for idx, item in enumerate(urgent_tasks, start=1):
                print(f"     {idx}. {item['title']} (마감: {item.get('due_date_parsed')})")

        # 2단계: 곧 다가올 일 (Upcoming)
        upcoming_tasks = [t for t in grouped_pending_tasks if t.get("due_status") == "upcoming"]
        if upcoming_tasks:
            print("  🟠 [곧 다가올 일 (7일 이내)]")
            for idx, item in enumerate(upcoming_tasks, start=1):
                print(f"     {idx}. {item['title']} (마감: {item.get('due_date_parsed')})")

        # 3단계: 여유 있는 일 (Future)
        future_tasks = [t for t in grouped_pending_tasks if t.get("due_status") == "future"]
        if future_tasks:
            print("  🟢 [여유 있는 일 (나중 마감)]")
            for idx, item in enumerate(future_tasks, start=1):
                print(f"     {idx}. {item['title']} (마감: {item.get('due_date_parsed')})")

        # 4단계: 마감일 확인 필요 (No Due Date)
        no_due_tasks = [t for t in grouped_pending_tasks if t.get("due_status") == "no_due_date"]
        if no_due_tasks:
            print("  ⚪ [마감일 확인 필요 (미제출)]")
            for idx, item in enumerate(no_due_tasks, start=1):
                print(f"     {idx}. {item['title']} (마감 정보 없음)")

        # 중요 공지 / 시험 / 일정 (별도 섹션 유지하되 카테고리 힌트 추가)
        if important_announcements:
            print("  📢 [중요 공지 / 시험 일정]")
            for idx, item in enumerate(important_announcements, start=1):
                status_map = {"urgent": "🔴", "upcoming": "🟠", "future": "🟢"}
                icon = status_map.get(item.get("time_status"), "⚪")
                print(f"     {icon} {item['title']} (링크: {item.get('url')})")


if __name__ == "__main__":
    run()
