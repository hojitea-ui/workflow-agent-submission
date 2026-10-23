"""테스트용 가짜 agy. 실제 agy의 동작을 흉내 낸다(실제 관찰 기록):
- `agy -p <프롬프트> --output-format json` 은 conversation_id/status/response/denied_actions가 든 JSON을 낸다.
- 이미지는 하위 에이전트(image-generator)의 대화 폴더(brain/<id>/...)에 만들어지고, 작업 폴더에는 복사되지 않는다.
- 이미지 도구가 500 오류를 내도 JSON의 status는 SUCCESS다(이미지는 없고 오류는 하위 대화 기록에만 남는다).
FAKE_AGY_MODE: success(3:4 PNG 생성) | fail500 | corrupt(깨진 PNG) | slow(오래 걸림)"""
import json
import os
import sys
import time
import uuid
from pathlib import Path

from PIL import Image, ImageDraw

argv = sys.argv[1:]
prompt = argv[argv.index("-p") + 1] if "-p" in argv else ""
mode = os.environ.get("FAKE_AGY_MODE", "success")
home = Path(os.environ["CARDNEWS_AGY_HOME"])
log = os.environ.get("FAKE_AGY_LOG")
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"argv": argv, "pid": os.getpid(), "prompt": prompt, "cwd": os.getcwd(), "mode": mode}, ensure_ascii=False) + "\n")

if mode == "slow":
    time.sleep(60)

conv, sub = f"conv-{uuid.uuid4().hex[:8]}", f"sub-{uuid.uuid4().hex[:8]}"
main_sys = home / "brain" / conv / ".system_generated"
sub_sys = home / "brain" / sub / ".system_generated"
(main_sys / "subagents").mkdir(parents=True, exist_ok=True)
(sub_sys / "logs").mkdir(parents=True, exist_ok=True)
(main_sys / "subagents" / f"{sub}.json").write_text(json.dumps({"conversationId": sub, "subagentDescriptor": {"typeName": "image-generator"}}), encoding="utf-8")

transcript = []
if mode == "fail500":
    for i in (2, 4, 6):
        transcript.append({"step_index": i, "status": "ERROR", "error": 'image failed to generate: failed to generate content: 500 Internal Server Error, body: {\n  "error": {\n    "code": 500,\n    "message": "Internal error encountered.",\n    "status": "INTERNAL"\n  }\n}\n'})
elif mode == "corrupt":
    (home / "brain" / sub / "cloud_servers_vector_1.png").write_bytes(b"\x89PNG\r\n\x1a\n this is not a real png")
elif mode == "success":
    im = Image.new("RGB", (768, 1024), (234, 242, 255))  # 3:4 — agy 이미지 도구가 4:5를 지원하지 않아 3:4로 만든다
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([120, 520, 380, 760], radius=24, fill=(30, 95, 216))
    d.rounded_rectangle([420, 580, 680, 820], radius=24, fill=(11, 31, 58))
    d.line([(380, 640), (420, 690)], fill=(255, 184, 77), width=10)
    (home / "brain" / sub).mkdir(parents=True, exist_ok=True)
    im.save(home / "brain" / sub / "cloud_servers_vector_1.png")
(sub_sys / "logs" / "transcript.jsonl").write_text("\n".join(json.dumps(t) for t in transcript), encoding="utf-8")

print(json.dumps({"conversation_id": conv, "status": "SUCCESS",
                  "response": "I have launched the image generation task. Once the image is ready, I will copy it to the working directory.\n",
                  "duration_seconds": 1.0, "num_turns": 1, "usage": {"total_tokens": 100},
                  "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}))
