"""서버 실행: python run.py [--port 8770]. 127.0.0.1에만 바인딩한다."""
import argparse
import socket
import sys

import uvicorn

HOST = "127.0.0.1"
DEFAULT_PORT = 8770  # 카드뉴스 앱(8765)과 같이 켜도 부딪히지 않게 다르게 둔다


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="보고서·과제 글 검수 에이전트 서버")
    parser.add_argument("--port", type=int, default=None, help=f"사용할 포트 (기본 {DEFAULT_PORT})")
    args = parser.parse_args()
    if args.port is not None:
        if not port_is_free(args.port):
            print(f"[오류] {args.port}번 포트를 이미 다른 프로그램이 쓰고 있습니다. 다른 번호로 --port 를 지정해 주세요.")
            return 1
        port = args.port
    elif port_is_free(DEFAULT_PORT):
        port = DEFAULT_PORT
    else:
        port = next((p for p in range(DEFAULT_PORT + 1, DEFAULT_PORT + 40) if port_is_free(p)), None)
        if port is None:
            print(f"[오류] {DEFAULT_PORT}번 근처에서 빈 포트를 찾지 못했습니다. --port 로 직접 지정해 주세요.")
            return 1
        print(f"[안내] {DEFAULT_PORT}번 포트가 사용 중이라 {port}번으로 시작합니다.")
    print("\n  엔진: Claude CLI (실제 웹 검색, 모델 호출이 일어납니다)")
    print(f"  브라우저에서 이 주소를 여세요:  http://{HOST}:{port}")
    print("  끄려면 이 창에서 Ctrl+C\n")
    uvicorn.run("app.main:app", host=HOST, port=port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
