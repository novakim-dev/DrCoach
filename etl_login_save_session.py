"""
eTL 로그인 세션 저장 스크립트
- auto: myetl.snu.ac.kr URL 감지 시 즉시 저장
- manual: Streamlit에서 완료 버튼 클릭 시 저장 (_etl_save_now.txt 신호 파일)
"""

from playwright.sync_api import sync_playwright
import os, sys, time

BASE = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE = os.path.join(BASE, "etl_auth.json")
SIGNAL_FILE = os.path.join(BASE, "_etl_save_now.txt")
ETL_URL = "https://myetl.snu.ac.kr"

def log(msg):
    print(msg, flush=True)

# 이전 신호 파일 제거
if os.path.exists(SIGNAL_FILE):
    os.remove(SIGNAL_FILE)

log("브라우저를 열어 SNU 계정으로 로그인해주세요.")
log("⚠️  로그인 후 이 브라우저 창을 절대 닫지 마세요!")
log("로그인 완료 후 Streamlit에서 [로그인 완료] 버튼을 클릭하거나 자동으로 저장됩니다.")

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()

    # wait_until="load" (기본값) → JS 리다이렉트 포함하여 SSO 로그인 페이지까지 이동
    page.goto(ETL_URL, timeout=30000)
    log(f"페이지 이동 완료. 현재: {page.url[:60]}")

    saved = False

    # 방법 1: URL 자동 감지 (wait_until="commit" → 가능한 빨리 저장)
    try:
        page.wait_for_url("*myetl.snu.ac.kr*", timeout=300000, wait_until="commit")
        log(f"eTL 감지! 현재 URL: {page.url[:60]}")
        context.storage_state(path=AUTH_FILE)
        saved = True
        log("자동 세션 저장 완료!")
    except Exception as e:
        log(f"자동 감지 실패: {e}")

    # 방법 2: 수동 신호 파일 대기 (브라우저가 아직 열려있는 경우)
    if not saved:
        log("수동 모드: Streamlit에서 [로그인 완료] 버튼을 클릭하세요.")
        for i in range(300):
            try:
                # URL 재확인
                url = page.url
                if "myetl.snu.ac.kr" in url and "/passni" not in url:
                    context.storage_state(path=AUTH_FILE)
                    saved = True
                    log(f"URL 재감지 저장 완료! ({url[:60]})")
                    break

                # 신호 파일 확인
                if os.path.exists(SIGNAL_FILE):
                    os.remove(SIGNAL_FILE)
                    context.storage_state(path=AUTH_FILE)
                    saved = True
                    log("수동 신호 세션 저장 완료!")
                    break

            except Exception as e:
                log(f"[{i}s] 오류: {e}")
                break

            log(f"[{i}s] 대기 중... URL: {page.url[:60]}")
            time.sleep(1)

    if saved:
        log("완료! 브라우저를 닫습니다.")
    else:
        log("저장 실패. 다시 시도해주세요.")

    try:
        browser.close()
    except Exception:
        pass
