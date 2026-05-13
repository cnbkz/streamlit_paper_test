"""
제지 업종 AI 분석 대시보드 런처
더블클릭 또는 run_app.bat 실행으로 시작
"""
import os
import sys
import time
import socket
import subprocess
import webbrowser

# UTF-8 강제 설정
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"

PORT = 8502
APP  = "chat_app.py"


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


def find_free_port(start: int) -> int:
    for p in range(start, start + 20):
        if not port_in_use(p):
            return p
    return start


def check_packages():
    required = ["streamlit", "yfinance", "openai", "pandas",
                "plotly", "pypdf", "feedparser", "dotenv"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[설치 중] {', '.join(missing)} ...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install"] + missing + ["--quiet"],
            check=True,
        )
        print("[완료] 패키지 설치 완료")


def main():
    print("=" * 50)
    print("  제지 업종 AI 분석 대시보드")
    print("=" * 50)

    check_packages()

    port = find_free_port(PORT)
    url  = f"http://localhost:{port}"

    print(f"\n앱 시작 중... ({url})")
    print("종료하려면 이 창에서 Ctrl+C 를 누르세요.\n")

    cmd = [
        sys.executable, "-m", "streamlit", "run", APP,
        "--server.port",    str(port),
        "--server.headless", "false",
        "--browser.gatherUsageStats", "false",
        "--theme.primaryColor",            "#FF4B4B",
        "--theme.backgroundColor",         "#FFFFFF",
        "--theme.secondaryBackgroundColor", "#F0F2F6",
    ]

    env = os.environ.copy()
    env["PYTHONUTF8"]        = "1"
    env["PYTHONIOENCODING"]  = "utf-8"

    proc = subprocess.Popen(cmd, env=env)

    # Streamlit 기동 대기 후 브라우저 열기
    for _ in range(20):
        time.sleep(0.5)
        if port_in_use(port):
            break

    time.sleep(0.5)
    webbrowser.open(url)
    print(f"브라우저에서 {url} 이 열렸습니다.")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n앱을 종료합니다...")
        proc.terminate()


if __name__ == "__main__":
    main()
