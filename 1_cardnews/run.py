"""서버 실행: python run.py [--port 8765]. 127.0.0.1에만 바인딩한다."""
import argparse
import os
import socket
import sys

import uvicorn

HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((HOST, port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="카드뉴스 에이전트 서버")
    parser.add_argument("--port", type=int, default=None, help=f"사용할 포트 (기본 {DEFAULT_PORT})")
    parser.add_argument("--engine", choices=["claude-cli", "mock"], default=None,
                        help="새 프로젝트가 쓸 엔진 (기본 claude-cli: 실제 웹 검색, mock: 모의 데이터)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--agy", action="store_true",
                       help="카드 이미지를 만들 때 agy(Antigravity)를 먼저 시도한다(기본 켜짐. 환경 변수로 꺼 둔 경우 다시 켠다)")
    group.add_argument("--no-agy", action="store_true",
                       help="agy를 쓰지 않고 바로 Claude(SVG, 안 되면 도형 도안)로 이미지를 만든다(agy 사용 한도를 아끼고 싶을 때)")
    args = parser.parse_args()
    if args.agy:
        os.environ["CARDNEWS_USE_AGY"] = "1"
    if args.no_agy:
        os.environ["CARDNEWS_USE_AGY"] = "0"
    if args.engine:
        os.environ["CARDNEWS_ENGINE"] = args.engine
    engine = os.environ.get("CARDNEWS_ENGINE", "claude-cli")

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

    print(f"\n  엔진: {'모의 데이터 (mock)' if engine == 'mock' else 'Claude CLI (실제 웹 검색, 모델 호출이 일어납니다)'}")
    if engine != "mock":
        from app import images
        print("  agy 이미지 생성: " + ("켜짐 (이미지를 만들 때 agy를 먼저 시도하고 agy 사용 한도를 씁니다. 끄려면 --no-agy)" if images.agy_enabled()
                                       else "꺼짐 (이미지는 Claude로 만듭니다. 켜려면 --agy)"))
    print(f"  브라우저에서 이 주소를 여세요:  http://{HOST}:{port}")
    print("  끄려면 이 창에서 Ctrl+C 를 누르세요.\n")
    uvicorn.run("app.main:app", host=HOST, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
