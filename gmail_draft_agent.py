"""
Gmail 초안 작성 에이전트
- Gemini로 메일 초안 생성
- Gmail Drafts API로 임시보관함 저장
"""

import os
import base64
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from google import genai
from dotenv import load_dotenv

load_dotenv()

SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.compose',
]

BASE = os.path.dirname(os.path.abspath(__file__))


def get_gmail_service():
    creds = None
    token_path = os.path.join(BASE, 'token.json')
    creds_path = os.path.join(BASE, 'credentials.json')

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, 'w') as f:
            f.write(creds.to_json())

    return build('gmail', 'v1', credentials=creds)


def generate_draft_text(task: dict) -> str:
    """Gemini로 메일 초안 본문 생성"""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    client = genai.Client(api_key=api_key)

    subject   = task.get("source_subject", "")
    sender    = task.get("source_from", "")
    snippet   = task.get("source_snippet", "")
    task_desc = task.get("task", "")
    reason    = task.get("reason", "")

    prompt = f"""다음 메일에 대한 한국어 답장 초안을 작성해줘.

[원본 메일]
보낸 사람: {sender}
제목: {subject}
내용 요약: {snippet}

[필요한 액션]
{task_desc}
({reason})

작성 규칙:
- 정중하고 간결하게 (3~5문장)
- 인사말로 시작, 마무리 인사로 끝
- 발신인 서명 없이 본문만 작성
- 마크다운 없이 순수 텍스트로
"""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    return response.text.strip()


def save_draft(service, task: dict, draft_body: str) -> str:
    """Gmail 임시보관함에 초안 저장. 성공 시 draft_id 반환"""
    sender_raw = task.get("source_from", "")
    # "Name <email@example.com>" 형식에서 이메일만 추출
    if "<" in sender_raw and ">" in sender_raw:
        to_email = sender_raw[sender_raw.index("<")+1 : sender_raw.index(">")]
    else:
        to_email = sender_raw

    subject = task.get("source_subject", "")
    re_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    message_id = task.get("source_message_id", "")
    thread_id  = task.get("source_thread_id", "")

    msg = MIMEText(draft_body, "plain", "utf-8")
    msg["To"]      = to_email
    msg["Subject"] = re_subject
    if message_id:
        msg["In-Reply-To"] = message_id
        msg["References"]  = message_id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    body = {"message": {"raw": raw}}
    if thread_id:
        body["message"]["threadId"] = thread_id

    draft = service.users().drafts().create(userId="me", body=body).execute()
    return draft.get("id", "")
