# 닥터코치

Gmail과 SNU eTL 할 일을 한 화면에서 확인하고, 매일 아침 증거 기반 AI 코칭을 받는 개인 대시보드.

## 시작하기

**Mac / Linux**
```bash
git clone https://github.com/novakim-dev/DrCoach.git
cd DrCoach
chmod +x setup.sh && ./setup.sh
streamlit run app.py
```

**Windows**
```
setup.bat 실행 후
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속

---

## 최초 설정 (앱 사이드바에서 진행)

**1. Gemini API 키** — [발급](https://aistudio.google.com/apikey) 후 사이드바에 입력

**2. Gmail 연동**
1. [Google Cloud Console](https://console.cloud.google.com/) → Gmail API 활성화
2. OAuth 클라이언트 ID 생성 (데스크톱 앱) → JSON 다운로드
3. 파일명 `credentials.json`으로 변경 후 사이드바에서 업로드
4. **Gmail 연결** 버튼 → 브라우저 로그인

**3. eTL 연동**
1. 사이드바 **eTL 로그인** → Chromium 브라우저에서 SNU 계정 로그인
2. eTL 메인 화면 진입 시 자동 저장

**4. 수강 과목 설정** — `analyze_etl_modules.py` 상단 `COURSE_IDS` / `COURSE_NAMES`를 본인 과목으로 교체

---

## 주의

아래 파일은 개인 인증 정보입니다. 절대 공유하지 마세요.

- `.env` · `credentials.json` · `token.json` · `etl_auth.json`

모두 `.gitignore`에 포함되어 있습니다.
