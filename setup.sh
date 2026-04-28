#!/bin/bash
set -e

echo "======================================"
echo "  Daily Briefing — 설치 시작"
echo "======================================"

# Python 버전 확인
if ! command -v python3 &>/dev/null; then
    echo "❌ Python3가 설치되어 있지 않습니다."
    echo "   https://www.python.org/downloads/ 에서 설치 후 다시 실행해주세요."
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python $PY_VER 감지"

# 가상환경 생성
if [ ! -d "venv" ]; then
    echo "📦 가상환경 생성 중..."
    python3 -m venv venv
fi

# 활성화
source venv/bin/activate

# 패키지 설치
echo "📦 패키지 설치 중..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Playwright Chromium 설치
echo "🌐 Playwright Chromium 설치 중..."
python -m playwright install chromium

# .env 파일 생성
if [ ! -f ".env" ]; then
    echo ""
    echo "======================================"
    echo "  Gemini API 키 설정"
    echo "======================================"
    echo "Google AI Studio (https://aistudio.google.com/apikey) 에서 API 키를 발급받으세요."
    echo ""
    read -p "Gemini API 키를 입력하세요: " GEMINI_KEY
    echo "GEMINI_API_KEY='$GEMINI_KEY'" > .env
    echo "✅ .env 파일 생성 완료"
else
    echo "✅ .env 파일 이미 존재"
fi

# Streamlit 이메일 프롬프트 비활성화
mkdir -p ~/.streamlit
if [ ! -f ~/.streamlit/credentials.toml ]; then
    echo '[general]' > ~/.streamlit/credentials.toml
    echo 'email = ""' >> ~/.streamlit/credentials.toml
fi

echo ""
echo "======================================"
echo "  설치 완료!"
echo "======================================"
echo ""
echo "▶ 실행 방법:"
echo "   source venv/bin/activate"
echo "   streamlit run app.py"
echo ""
echo "앱이 열리면 사이드바에서 Gmail, eTL 연결을 진행하세요."
