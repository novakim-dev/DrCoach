"""
[역할]
Gmail에서 읽지 않은 메일을 가져와, 할 일을 추출하고 JSON으로 저장하는 에이전트

[입력]
- Gmail API (unread 메일 20개)
- OAuth 인증 (token.json / credentials.json 필요)

[처리 흐름]
1. Gmail API로 메일 목록 조회
2. 각 메일 본문 추출
3. Gemini API로 할 일(task, due_date) 추출
4. 지난 일정 제거 + 중복 제거 + 날짜 기준 정렬
5. 오늘 할 일 필터링

[출력]
- gmail_tasks.json (전체 할 일 + 오늘 할 일)
- 콘솔 로그 출력

[주의]
- token.json / credentials.json / API KEY는 Git에 포함하면 안 됨
"""

import os
import json
import base64
import time
import random
from datetime import datetime, date
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google import genai
from dotenv import load_dotenv
load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

print("[DEBUG] 현재 키 앞 8글자:", GEMINI_API_KEY[:8] if GEMINI_API_KEY else None)

WORKING_MODEL = "gemini-2.5-flash"
FALLBACK_MODELS = ["gemini-2.5-flash-lite"]

if GEMINI_API_KEY and GEMINI_API_KEY.lower() != "your_api_key_here":
    if not GEMINI_API_KEY.startswith("AIza"):
        raise ValueError(f"Gemini API 키 시작 형식이 이상합니다: {repr(GEMINI_API_KEY[:12])}")

    print("[DEBUG] Gemini client 생성 시도")
    client = genai.Client(api_key=GEMINI_API_KEY)

    try:
        print("[시스템] 사용 가능한 모델 확인 중...")
        available_names = []

        for m in client.models.list():
            raw_name = m.name
            normalized = raw_name.replace("models/", "")
            available_names.append(normalized)

        print(f"[시스템] 감지된 모델 수: {len(available_names)}")

        priorities = [
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ]

        selected = None
        for target in priorities:
            if target in available_names:
                selected = target
                break

        if not selected and available_names:
            selected = available_names[0]

        WORKING_MODEL = selected or "gemini-2.5-flash"
        FALLBACK_MODELS = [
            m for m in priorities
            if m in available_names and m != WORKING_MODEL
        ]

        print(f"[시스템] 사용할 모델 확정: {WORKING_MODEL}")
        print(f"[시스템] fallback 후보: {FALLBACK_MODELS}")

    except Exception as e:
        print(f"[경고] 모델 리스트 확인 실패(기본값 사용): {e}")
        FALLBACK_MODELS = ["gemini-2.5-flash-lite"]

else:
    print("[DEBUG] Gemini client 생성 안 함")
    client = None
    FALLBACK_MODELS = []

# Gmail 권한 범위 (읽기 전용)
SCOPES = ['https://www.googleapis.com/auth/gmail.readonly']

from google.auth.exceptions import RefreshError

def get_gmail_service():
    """Gmail API 서비스 객체를 생성 및 반환합니다."""
    creds = None
    token_path = os.path.join(os.path.dirname(__file__), 'token.json')
    creds_path = os.path.join(os.path.dirname(__file__), 'credentials.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError:
                print("[경고] 기존 Gmail 토큰이 만료되었거나 취소되었습니다.")
                print("[조치] token.json을 삭제하고 다시 로그인합니다.")

                if os.path.exists(token_path):
                    os.remove(token_path)

                creds = None

        if not creds or not creds.valid:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(f"'{creds_path}' 파일이 필요합니다.")

            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)

            with open(token_path, 'w', encoding='utf-8') as token:
                token.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)

def debug_current_gmail_account(service):
    profile = service.users().getProfile(userId='me').execute()
    print("[DEBUG] 현재 접근 중인 Gmail 주소:", profile.get("emailAddress"))

def execute_with_retry(request, max_retries=5):
    """지수 백오프를 사용하여 Google API 요청을 재시도합니다."""
    for n in range(max_retries):
        try:
            return request.execute()
        except HttpError as error:
            # 429 (Too Many Requests) 또는 503 (Service Unavailable) 에러인 경우 재시도
            if error.resp.status in [429, 503] and n < max_retries - 1:
                sleep_time = (2 ** n) + (random.randint(0, 1000) / 1000)
                print(f"  [경고] API 오류 {error.resp.status} 발생. {sleep_time:.2f}초 후 재시도 중... ({n+1}/{max_retries})")
                time.sleep(sleep_time)
            else:
                raise
    return None

def fetch_emails():
    """실제 Gmail API로 읽지 않은 최근 메일 20개를 가져옵니다."""
    service = get_gmail_service()
    debug_current_gmail_account(service)
    try:
        # 1. 메일 목록 가져오기 (재시도 적용)
        results = execute_with_retry(service.users().messages().list(
            userId='me', q='is:unread', maxResults=20
        ))
        messages = results.get('messages', [])
        
        full_emails = []
        if not messages:
            print("읽지 않은 메일이 없습니다.")
            return []
            
        print(f"{len(messages)}개의 읽지 않은 메일을 가져오는 중...")
        for message in messages:
            # 2. 각 메일의 상세 정보 가져오기 (재시도 적용)
            msg = execute_with_retry(service.users().messages().get(userId='me', id=message['id']))
            full_emails.append(msg)
            # API 할당량 소모를 줄이기 위해 짧은 지연 추가
            time.sleep(0.5)
            
        return full_emails
    except HttpError as error:
        print(f'Gmail API 최종 오류 발생: {error}')
        return []
    
def extract_email_metadata(email):
    """검증용 메타데이터를 추출합니다."""
    headers = email.get("payload", {}).get("headers", [])

    subject = ""
    sender = ""

    for h in headers:
        name = h.get("name", "").lower()
        value = h.get("value", "")
        if name == "subject":
            subject = value
        elif name == "from":
            sender = value

    return {
        "message_id": email.get("id"),
        "thread_id": email.get("threadId"),
        "subject": subject,
        "from": sender,
        "snippet": email.get("snippet", "")
    }

def extract_body(email):
    """메일 페이로드에서 본문을 추출합니다."""
    plain_parts = []
    html_parts = []

    def walk_parts(payload):
        mime_type = payload.get('mimeType')
        body_data = payload.get('body', {}).get('data')

        if body_data:
            try:
                decoded = base64.urlsafe_b64decode(body_data).decode('utf-8', errors='replace')
                if mime_type == 'text/plain':
                    plain_parts.append(decoded)
                elif mime_type == 'text/html':
                    html_parts.append(decoded)
            except:
                pass

        if 'parts' in payload:
            for part in payload['parts']:
                walk_parts(part)

    walk_parts(email.get('payload', {}))
    return "\n".join(plain_parts) if plain_parts else "\n".join(html_parts)

def call_llm(prompt, max_retries=3):
    """Gemini API를 호출하며 429/503 에러 시 fallback 모델까지 시도합니다."""
    if not client:
        print("  [주의] GEMINI_API_KEY 설정 오류. 분석을 건너뜁니다.")
        return "[]"

    candidate_models = [WORKING_MODEL] + [m for m in FALLBACK_MODELS if m != WORKING_MODEL]

    for model_name in candidate_models:
        for n in range(max_retries):
            try:
                print(f"  [{model_name} 호출 중... (시도 {n+1}/{max_retries})]")
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt
                )
                text = response.text.strip()

                if text.startswith("```json"):
                    text = text[7:-3].strip()
                elif text.startswith("```"):
                    text = text[3:-3].strip()

                return text

            except Exception as e:
                error_str = str(e).lower()

                retryable = (
                    "429" in error_str
                    or "resource_exhausted" in error_str
                    or "503" in error_str
                    or "unavailable" in error_str
                )

                if retryable and n < max_retries - 1:
                    wait_time = min(10 * (2 ** n), 60) + random.uniform(0, 2)
                    print(f"  [경고] {model_name} 혼잡/할당량 문제. {wait_time:.1f}초 대기 후 재시도...")
                    time.sleep(wait_time)
                    continue

                print(f"  [경고] {model_name} 호출 실패: {e}")
                break  # 같은 모델 재시도 종료 후 다음 fallback 모델로 이동

    print("  [오류] 사용 가능한 fallback 모델까지 모두 실패했습니다.")
    return "[]"

def extract_tasks_batch(email_items):
    """
    여러 메일 내용을 한 번에 분석하여 할 일을 추출합니다.
    각 결과에 email_index를 붙여 어느 메일에서 나온 할 일인지 추적 가능하게 합니다.
    """
    if not email_items:
        return []

    combined_content = ""
    for i, item in enumerate(email_items, start=1):
        combined_content += (
            f"--- 메일 #{i} ---\n"
            f"[subject] {item['subject']}\n"
            f"[from] {item['from']}\n"
            f"[snippet] {item['snippet']}\n"
            f"[body]\n{item['body']}\n\n"
        )

    prompt = f"""
다음은 여러 개의 이메일 내용입니다. 각 메일에서 사용자가 해야 할 일을 추출해줘.

목표:
- "반드시 확인/대응해야 하는 일(required)"과
- "하면 좋지만 필수는 아닌 일(optional)"을 구분한다.

반드시 JSON 배열만 출력할 것.
각 객체는 아래 형식을 반드시 지킬 것:

{{
  "email_index": 1,
  "task": "할 일 내용",
  "task_type": "required",
  "due_date": "YYYY-MM-DD" 또는 null,
  "reason": "왜 required/optional로 분류했는지 한 줄 설명"
}}

분류 기준:
1. required
- 과제 제출
- 회신 요청 / 답장 요청
- 검토 요청 / 수정 요청
- 반드시 확인해야 하는 공지
- 계정/행정/수업 관련 실제 대응 필요
- 일정 확인을 하지 않으면 문제 생길 수 있는 메일

2. optional
- 행사 신청
- 특강 신청
- 세미나/포럼 참석 신청
- 전시 관람
- 프로그램 참가 권유
- 홍보성 신청 안내
- 안 해도 직접적인 불이익이 없는 항목

중요 규칙:
1. optional은 정말 행동 후보로 볼 만한 경우만 남겨라.
2. 단순 홍보, 뉴스레터, 일반 안내는 제외하라.
3. due_date를 명확히 알 수 없으면 추측하지 말고 null로 둘 것.
4. required는 due_date가 없어도 포함할 것.
5. optional은 due_date가 없고 긴급성도 없으면 가급적 제외할 것.
6. 같은 메일에서 여러 task가 가능하면 여러 개 추출 가능하다.
7. email_index는 반드시 해당 메일 번호와 일치해야 한다.
8. 결과가 없으면 []만 출력할 것.

메일 리스트:
\"\"\"
{combined_content}
\"\"\"
"""
    try:
        response_text = call_llm(prompt)

        print("\n[DEBUG] Gemini raw response:")
        print(response_text[:1000])

        parsed = json.loads(response_text)
        print(f"[DEBUG] parsed task count: {len(parsed)}")
        return parsed

    except Exception as e:
        print(f"  [오류] JSON 파싱 실패: {e}")
        print("[DEBUG] 파싱 실패 raw response:")
        print(response_text[:1000] if 'response_text' in locals() else "(응답 없음)")
        return []

def normalize_task_type(value):
    v = (value or "").strip().lower()
    if v == "required":
        return "required"
    if v == "optional":
        return "optional"
    return "required"  # 기본값은 보수적으로 required

def normalize_extracted_task(item):
    return {
        "task": (item.get("task") or "").strip(),
        "task_type": normalize_task_type(item.get("task_type")),
        "due_date": (item.get("due_date") or "").strip() or None,
        "reason": (item.get("reason") or "").strip()
    }

def filter_expired(tasks):
    """지난 일정 제거. 단, due_date 없는 required는 유지"""
    today = date.today()
    filtered = []

    for t in tasks:
        due_date_str = t.get("due_date")
        task_type = t.get("task_type", "required")

        if not due_date_str:
            if task_type == "required":
                filtered.append(t)
            continue

        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
            if due_date >= today:
                filtered.append(t)
        except:
            # 날짜 파싱 실패 시 required는 살리고, optional은 버림
            if task_type == "required":
                filtered.append(t)

    return filtered

def sort_tasks(tasks):
    """required 우선, 그다음 날짜 있는 항목 우선, 그다음 마감일 순"""
    def sort_key(x):
        task_type_priority = 0 if x.get("task_type") == "required" else 1
        due_date_str = x.get("due_date")

        if due_date_str:
            try:
                due_dt = datetime.strptime(due_date_str, "%Y-%m-%d").date()
                return (task_type_priority, 0, due_dt)
            except:
                pass

        return (task_type_priority, 1, date.max)

    return sorted(tasks, key=sort_key)

def get_today_tasks(tasks):
    """오늘 마감인 할 일만 반환"""
    today_str = date.today().strftime('%Y-%m-%d')
    return [t for t in tasks if t.get("due_date") == today_str]

def deduplicate_tasks(tasks):
    """같은 task + task_type + due_date 중복 제거"""
    seen = set()
    unique = []

    for t in tasks:
        task = (t.get("task") or "").strip()
        task_type = (t.get("task_type") or "required").strip()
        due_date = (t.get("due_date") or "").strip() or None

        if not task:
            continue

        key = (task, task_type, due_date)

        if key not in seen:
            seen.add(key)
            unique.append({
                "task": task,
                "task_type": task_type,
                "due_date": due_date,
                "reason": t.get("reason", ""),
                "source_message_id": t.get("source_message_id"),
                "source_thread_id": t.get("source_thread_id"),
                "source_subject": t.get("source_subject"),
                "source_from": t.get("source_from"),
                "source_snippet": t.get("source_snippet")
            })

    return unique

def save_tasks_json(all_tasks, today_tasks, output_path="gmail_tasks.json"):
    """Gmail 추출 결과를 JSON 파일로 저장"""
    required_tasks = [t for t in all_tasks if t.get("task_type") == "required"]
    optional_tasks = [t for t in all_tasks if t.get("task_type") == "optional"]

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "gmail",

        "total_task_count": len(all_tasks),
        "today_task_count": len(today_tasks),
        "has_today_tasks": len(today_tasks) > 0,

        # 기존 호환용
        "today_tasks": today_tasks,
        "all_tasks": all_tasks,

        # 신규 구조
        "required_task_count": len(required_tasks),
        "optional_task_count": len(optional_tasks),
        "required_tasks": required_tasks,
        "optional_tasks": optional_tasks
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[Saved] Gmail 할 일 저장 완료: {output_path}") 

def run_agent():
    """전체 파이프라인 실행 (배치 처리 방식)"""
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] 에이전트 실행 시작")

    emails = fetch_emails()
    if not emails:
        print("분석할 메일이 없습니다.")
        return

    all_tasks = []

    batch_size = 3
    for i in range(0, len(emails), batch_size):
        batch_emails = emails[i:i + batch_size]
        print(f"[{i+1}~{min(i+batch_size, len(emails))}/{len(emails)}] 배치 분석 중...")

        batch_items = []
        for email in batch_emails:
            meta = extract_email_metadata(email)
            batch_items.append({
                "message_id": meta["message_id"],
                "thread_id": meta["thread_id"],
                "subject": meta["subject"],
                "from": meta["from"],
                "snippet": meta["snippet"],
                "body": extract_body(email)
            })

        print("\n[DEBUG] 현재 배치 메일:")
        for idx, item in enumerate(batch_items, start=1):
            print(f"{idx}. {item['subject']} / {item['from']}")
            print(f"   snippet: {item['snippet'][:120]}")
        extracted = extract_tasks_batch(batch_items)

        # email_index를 원본 메타데이터와 다시 연결
        for item in extracted:
            try:
                idx = int(item.get("email_index")) - 1
                if idx < 0 or idx >= len(batch_items):
                    continue

                source = batch_items[idx]

                normalized = normalize_extracted_task(item)

                if not normalized["task"]:
                    continue

                all_tasks.append({
                    "task": normalized["task"],
                    "task_type": normalized["task_type"],
                    "due_date": normalized["due_date"],
                    "reason": normalized["reason"],
                    "source_message_id": source["message_id"],
                    "source_thread_id": source["thread_id"],
                    "source_subject": source["subject"],
                    "source_from": source["from"],
                    "source_snippet": source["snippet"]
                })
            except Exception:
                continue

        if i + batch_size < len(emails):
            print("  [알림] 다음 배치를 위해 10초 대기 중...")
            time.sleep(10)

    valid_tasks = filter_expired(all_tasks)
    unique_tasks = deduplicate_tasks(valid_tasks)
    sorted_tasks = sort_tasks(unique_tasks)
    today_tasks = get_today_tasks(sorted_tasks)

    save_tasks_json(sorted_tasks, today_tasks)

    required_count = len([t for t in sorted_tasks if t.get("task_type") == "required"])
    optional_count = len([t for t in sorted_tasks if t.get("task_type") == "optional"])

    print("\n" + "=" * 50)
    print("분석 결과:")
    print(f"총 메일: {len(emails)}개 | 추출된 할 일: {len(sorted_tasks)}개")
    print(f"- required: {required_count}개")
    print(f"- optional: {optional_count}개")

    print("\n오늘 할 일:")
    if not today_tasks:
        print("- (없음)")
    else:
        for t in today_tasks:
            print(f"- [{t.get('task_type', 'required')}] {t['task']} ({t.get('due_date')})")
            print(f"  ↳ 출처: {t.get('source_subject', '제목 없음')} / {t.get('source_from', '보낸사람 없음')}")

    print("\n전체 일정 (정렬됨):")
    if not sorted_tasks:
        print("- (없음)")
    else:
        for t in sorted_tasks:
            due_display = t.get("due_date") or "마감 미상"
            print(f"- [{t.get('task_type', 'required')}] {t['task']} ({due_display})")
            if t.get("reason"):
                print(f"  ↳ 분류 근거: {t['reason']}")
            print(f"  ↳ 출처: {t.get('source_subject', '제목 없음')} / {t.get('source_from', '보낸사람 없음')}")
    print("=" * 50)

if __name__ == "__main__":
    run_agent()
