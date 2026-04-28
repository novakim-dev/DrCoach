@echo off
chcp 65001 >nul
echo ======================================
echo   Daily Briefing - 설치 시작
echo ======================================

:: Python 확인
python --version >nul 2>&1
if errorlevel 1 (
    echo [오류] Python이 설치되어 있지 않습니다.
    echo https://www.python.org/downloads/ 에서 설치 후 다시 실행해주세요.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do echo [확인] Python %%v 감지

:: 가상환경 생성
if not exist "venv\" (
    echo [진행] 가상환경 생성 중...
    python -m venv venv
)

:: 활성화
call venv\Scripts\activate.bat

:: 패키지 설치
echo [진행] 패키지 설치 중...
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

:: Playwright
echo [진행] Playwright Chromium 설치 중...
python -m playwright install chromium

:: .env 파일 생성
if not exist ".env" (
    echo.
    echo ======================================
    echo   Gemini API 키 설정
    echo ======================================
    echo Google AI Studio ^(https://aistudio.google.com/apikey^) 에서 API 키를 발급받으세요.
    echo.
    set /p GEMINI_KEY="Gemini API 키를 입력하세요: "
    echo GEMINI_API_KEY='%GEMINI_KEY%'> .env
    echo [완료] .env 파일 생성 완료
) else (
    echo [확인] .env 파일 이미 존재
)

:: Streamlit 이메일 프롬프트 비활성화
if not exist "%USERPROFILE%\.streamlit\" mkdir "%USERPROFILE%\.streamlit"
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    echo [general] > "%USERPROFILE%\.streamlit\credentials.toml"
    echo email = "" >> "%USERPROFILE%\.streamlit\credentials.toml"
)

echo.
echo ======================================
echo   설치 완료!
echo ======================================
echo.
echo 실행 방법:
echo   venv\Scripts\activate
echo   streamlit run app.py
echo.
echo 앱이 열리면 사이드바에서 Gmail, eTL 연결을 진행하세요.
echo.
pause
