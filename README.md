# Daily Briefing — 개인 AI 일정 매니저

Gmail과 SNU eTL의 할 일을 한 화면에서 확인하고, AI 코치의 자기성찰 프롬프트를 매일 아침 받아보는 개인용 대시보드입니다.

## 기능

- Gmail 수신함 분석 → 필수/선택 할 일 자동 분류 (Gemini AI)
- SNU eTL 과제·공지 현황 및 제출 상태 확인
- 체크박스로 완료 처리 (새로고침 후에도 유지)
- 요일별 증거 기반 자기성찰 코칭 메시지
- 매일 아침 보고서 형태의 대시보드

## 필요 환경

- Python 3.10 이상
- SNU 포털 계정 (eTL 사용 시)
- Gmail 계정
- [Gemini API 키](https://aistudio.google.com/apikey) (무료 발급)
- [Google Cloud Console](https://console.cloud.google.com/) OAuth 설정 (Gmail 연동 시)

---

## 설치 및 실행

### Mac / Linux

```bash
git clone <이 저장소 주소>
cd <폴더명>
chmod +x setup.sh
./setup.sh
```

설치 완료 후:

```bash
source venv/bin/activate
streamlit run app.py
```

### Windows

```
setup.bat 더블클릭
```

설치 완료 후:

```
venv\Scripts\activate
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속

---

## 최초 설정 순서

앱 실행 후 왼쪽 사이드바에서 순서대로 진행합니다.

### 1. Gemini API 키

- [Google AI Studio](https://aistudio.google.com/apikey) → API 키 발급
- 사이드바 **Gemini API 키** 입력란에 붙여넣기

### 2. Gmail 연동

1. [Google Cloud Console](https://console.cloud.google.com/) → 새 프로젝트 생성
2. **Gmail API** 활성화
3. **OAuth 클라이언트 ID** 생성 (유형: 데스크톱 앱)
4. 다운로드한 JSON 파일 이름을 `credentials.json`으로 변경
5. 사이드바 **Gmail credentials.json 업로드** 버튼으로 업로드
6. **Gmail 연결** 버튼 클릭 → 브라우저 로그인 완료

> `token.json`은 로그인 후 자동 생성됩니다. 외부에 공유하지 마세요.

### 3. eTL 연동

1. 사이드바 **eTL 로그인** 버튼 클릭
2. 열린 Chromium 브라우저에서 SNU 계정으로 로그인
3. eTL 메인 화면이 열리면 자동으로 세션 저장됨
4. 자동 감지 실패 시 사이드바 **로그인 완료** 버튼 클릭

> `etl_auth.json`은 개인 세션 파일입니다. 외부에 공유하지 마세요.

---

## 공유 금지 파일

| 파일 | 이유 |
|------|------|
| `.env` | Gemini API 키 포함 |
| `credentials.json` | Google OAuth 클라이언트 정보 |
| `token.json` | Gmail 개인 로그인 토큰 |
| `etl_auth.json` | SNU eTL 개인 세션 |

위 파일들은 `.gitignore`에 포함되어 있어 GitHub에 올라가지 않습니다.
