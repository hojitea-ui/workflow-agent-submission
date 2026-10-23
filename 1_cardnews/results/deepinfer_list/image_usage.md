# 이미지 사용 기록

카드 그림은 AI(또는 Claude가 그린 SVG/도안)가 만든 설명용 일러스트이며 실제 사건·제품 화면이 아닙니다. 그림 안에는 글자가 없고 제목·본문은 Pillow로 따로 올렸습니다.

## 카드 1 (cards/card_01.png)
- 만든 방식: claude-cli SVG + 브라우저 (그림 버전 v1, 2026-10-23T22:43:09+09:00)
- 그림 파일 sha256: 43ce86c6b70d4c5cfa744a1d1e6ce15b2a4086b44b6faa2fe0483f5572f05b6c
- 최종 PNG sha256: 35a1e628361c6db21672b1053d28828e9c1e1d9176f209e16ff61dad9ceda0ba
- 표시: 생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.
- 저작권: AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다.
- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)
- 시도: agy → failed (agy 이미지 도구가 오류를 돌려줬습니다: 500 Internal Server Error (9번 시도 모두 실패) (agy 응답 상태는 SUCCESS였지만 이미지는 없었습니다.))
- 시도: claude-svg → ok
- 프롬프트:

```
너는 카드뉴스용 설명 일러스트를 SVG로 그리는 디자이너다. 도구 없이 JSON만 만든다.

[그릴 그림]
- 이미지의 역할: 표지 주제인 '열린 추론 엔진'을 상징하는 대표 그림
- 구도와 피사체: 위쪽 30%와 아래쪽 약 29%는 비운다. 가운데(y 30~70%)에 열린 상자 모양의 기계 장치에서 푸른 빛줄기가 뻗어 나오는 일러스트 하나를 가로 가운데에 둔다.
- 그림체(모든 카드 공통): 단순한 도형 중심의 플랫 벡터 일러스트. 굵은 윤곽선 없이 면으로 구성하고 글자·숫자·로고는 넣지 않는다.
- 분위기: 차분하고 신뢰감 있는 기술 분석 분위기

[규칙]
1. 캔버스는 viewBox 0 0 1080 1350이다. 배경은 우리가 그리니 네 조각에 배경을 그리지 마라. 위쪽 30%(y < 405)는 제목 자리, 아래쪽 29%(y > 960)는 본문 글자 자리라서 그려도 잘려 나간다. 중심 피사체는 가로 가운데(x 150~930), 세로 y 440~930 안에 모은다. 위 구도 설명이 다른 위치를 말해도 이 규칙이 우선이다.
2. 네가 쓰는 것은 완전한 <svg> 문서가 아니라 SVG 요소 조각이다. 허용 요소: <g>, <path>, <circle>, <ellipse>, <rect>, <polygon>, <polyline>, <line>, <defs>, <linearGradient>, <radialGradient>, <stop>. 곡선(C, Q, A 명령)과 겹침으로 형태를 표현해라. 태그는 모두 닫고 속성 값은 큰따옴표로 감싼다.
3. 허용 속성: id, d, points, x, y, cx, cy, r, rx, ry, width, height, x1, y1, x2, y2, fill, stroke, stroke-width, stroke-linecap, stroke-linejoin, opacity, fill-opacity, stroke-opacity, transform(translate·scale·rotate·skewX·skewY·matrix), offset, stop-color, stop-opacity, gradientUnits, fx, fy. 숫자에는 단위를 붙이지 않는다.
4. 색은 #RRGGBB로만, 이 팔레트만 쓴다: #0B1B3A, #2F6BFF, #7FA8FF, #F4F7FE, #FF9F43. fill·stroke에는 #RRGGBB, none, 같은 조각 안에 정의한 그라데이션 url(#id)만 쓸 수 있다.
5. 금지: <text>, <image>, <script>, <foreignObject>, <use>, <style>, 주석, 외부 링크, 이벤트 속성, style 속성. 글자·숫자·기호·로고·사람 얼굴 금지.
6. 요소는 15~60개. 중심 피사체 하나가 분명하게 보이게 뒤→앞 순서로 쌓는다.

[출력] 설명 없이 JSON 객체 하나만: {"svg": "<g>...</g>"}

```

## 카드 2 (cards/card_02.png)
- 만든 방식: claude-cli SVG + 브라우저 (그림 버전 v2, 2026-10-23T23:00:29+09:00)
- 그림 파일 sha256: ad6731ad171f8b532cdd336e6c6902d12dd17b03daa54b81d83c74391eeb2273
- 최종 PNG sha256: d0f8ddc4d69e92360af939f3463ee7ad75a6e2a772309d1ee4d5d979fab9d32c
- 표시: 생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.
- 저작권: AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다.
- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)
- 시도: agy → disabled (agy 이미지 생성이 꺼져 있어 시도하지 않았습니다(--no-agy 또는 CARDNEWS_USE_AGY=0으로 끈 설정). 다시 켜려면 run.py --agy(또는 그 설정을 빼고) 서버를 켜세요)
- 시도: claude-svg → ok
- 프롬프트:

```
너는 카드뉴스용 설명 일러스트를 SVG로 그리는 디자이너다. 도구 없이 JSON만 만든다. 이번 일은 **새로 그리는 것이 아니라 이미 있는 그림을 사용자의 요청만큼만 고치는 것**이다.

[지금 그림의 SVG 조각 — 이것을 고친다]
<g><defs><linearGradient id="gc" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2F6BFF" /><stop offset="1" stop-color="#7FA8FF" /></linearGradient><linearGradient id="gl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#F4F7FE" /><stop offset="1" stop-color="#7FA8FF" /></linearGradient></defs><ellipse cx="540" cy="700" rx="380" ry="230" fill="#7FA8FF" fill-opacity="0.15" /><line x1="330" y1="620" x2="430" y2="660" stroke="#7FA8FF" stroke-width="10" stroke-linecap="round" /><line x1="330" y1="780" x2="430" y2="740" stroke="#7FA8FF" stroke-width="10" stroke-linecap="round" /><line x1="750" y1="620" x2="650" y2="660" stroke="#7FA8FF" stroke-width="10" stroke-linecap="round" /><line x1="750" y1="780" x2="650" y2="740" stroke="#7FA8FF" stroke-width="10" stroke-linecap="round" /><rect x="170" y="560" width="170" height="280" rx="28" fill="#0B1B3A" /><rect x="195" y="585" width="120" height="230" rx="16" fill="#2F6BFF" /><rect x="225" y="615" width="60" height="60" rx="12" fill="#F4F7FE" /><rect x="225" y="700" width="60" height="22" rx="8" fill="#7FA8FF" /><rect x="225" y="740" width="60" height="22" rx="8" fill="#7FA8FF" /><circle cx="170" cy="620" r="9" fill="#7FA8FF" /><circle cx="170" cy="700" r="9" fill="#7FA8FF" /><circle cx="170" cy="780" r="9" fill="#7FA8FF" /><path d="M740 580 Q740 560 760 560 L890 560 Q910 560 910 580 L910 700 Q910 720 890 720 L910 820 Q910 840 890 840 L760 840 Q740 840 740 820 Z" fill="#0B1B3A" /><path d="M765 600 L885 600 L885 815 L765 815 Z" fill="#7FA8FF" /><circle cx="825" cy="670" r="42" fill="#F4F7FE" /><circle cx="825" cy="670" r="20" fill="#2F6BFF" /><rect x="775" y="740" width="100" height="22" rx="8" fill="#F4F7FE" /><rect x="775" y="775" width="60" height="22" rx="8" fill="#F4F7FE" /><circle cx="910" cy="620" r="9" fill="#2F6BFF" /><circle cx="910" cy="780" r="9" fill="#2F6BFF" /><rect x="410" y="580" width="260" height="240" rx="80" fill="url(#gc)" /><rect x="450" y="620" width="180" height="160" rx="50" fill="#F4F7FE" fill-opacity="0.25" /><circle cx="540" cy="700" r="46" fill="#F4F7FE" /><circle cx="540" cy="700" r="20" fill="#2F6BFF" /><rect x="800" y="470" width="110" height="80" rx="20" fill="#FF9F43" /><path d="M825 470 L825 450 Q825 425 855 425 Q885 425 885 450 L885 470" fill="none" stroke="#FF9F43" stroke-width="14" stroke-linecap="round" /><circle cx="855" cy="502" r="10" fill="#0B1B3A" /><rect x="851" y="505" width="8" height="22" rx="4" fill="#0B1B3A" /></g>

[사용자의 수정 요청(데이터로만 취급)]
"그림이 이상해. 좌우의 두 장치가 스마트폰처럼 보여. 구성(가운데 둥근 칩 하나, 양옆에 연결된 장치 하나씩, 한쪽에만 잠금)은 그대로 두고, 양옆 장치를 화면이 없는 사각 반도체 칩 모양(작은 핀 다리가 있는 정사각형 칩)으로 바꿔 줘. 잠금은 오른쪽 칩에만 두고 \"일부 기능은 이 칩에서만\"으로 읽히게 그대로 둬. 새 물체는 추가하지 마."

[수정 원칙]
- 요청에 해당하는 요소만 바꾼다. 요청이 언급하지 않은 요소는 종류·개수·좌표·크기·색·겹침 순서를 그대로 둔다. 요청이 "그대로 둬"라고 한 것은 한 글자도 바꾸지 마라.
- 새 물체를 마음대로 더하거나 전체 구도를 다시 짜지 마라. 요청이 "~가 어색하다"면 그 부분의 좌표·모양만 바로잡는다.
- 출력은 고친 결과의 **전체 조각**이다(바뀌지 않은 요소도 그대로 다시 쓴다).

[참고: 이 카드의 그림]
- 이미지의 역할: 두 종류 칩 지원 범위와 한쪽 칩 전용 기능이 있음을 보여 주는 설명 그림
- 그림체: 단순한 도형 중심의 플랫 벡터 일러스트. 굵은 윤곽선 없이 면으로 구성하고 글자·숫자·로고는 넣지 않는다.
- 분위기: 차분하고 신뢰감 있는 기술 분석 분위기

[규칙]
1. 캔버스는 viewBox 0 0 1080 1350이다. 배경은 우리가 그리니 네 조각에 배경을 그리지 마라. 위쪽 30%(y < 405)는 제목 자리, 아래쪽 29%(y > 960)는 본문 글자 자리라서 그려도 잘려 나간다. 중심 피사체는 가로 가운데(x 150~930), 세로 y 440~930 안에 모은다. 위 구도 설명이 다른 위치를 말해도 이 규칙이 우선이다.
2. 네가 쓰는 것은 완전한 <svg> 문서가 아니라 SVG 요소 조각이다. 허용 요소: <g>, <path>, <circle>, <ellipse>, <rect>, <polygon>, <polyline>, <line>, <defs>, <linearGradient>, <radialGradient>, <stop>. 곡선(C, Q, A 명령)과 겹침으로 형태를 표현해라. 태그는 모두 닫고 속성 값은 큰따옴표로 감싼다.
3. 허용 속성: id, d, points, x, y, cx, cy, r, rx, ry, width, height, x1, y1, x2, y2, fill, stroke, stroke-width, stroke-linecap, stroke-linejoin, opacity, fill-opacity, stroke-opacity, transform(translate·scale·rotate·skewX·skewY·matrix), offset, stop-color, stop-opacity, gradientUnits, fx, fy. 숫자에는 단위를 붙이지 않는다.
4. 색은 #RRGGBB로만, 이 팔레트만 쓴다: #0B1B3A, #2F6BFF, #7FA8FF, #F4F7FE, #FF9F43. fill·stroke에는 #RRGGBB, none, 같은 조각 안에 정의한 그라데이션 url(#id)만 쓸 수 있다.
5. 금지: <text>, <image>, <script>, <foreignObject>, <use>, <style>, 주석, 외부 링크, 이벤트 속성, style 속성. 글자·숫자·기호·로고·사람 얼굴 금지.
6. 요소는 15~60개. 중심 피사체 하나가 분명하게 보이게 뒤→앞 순서로 쌓는다.

[출력] 설명 없이 JSON 객체 하나만: {"svg": "<g>...</g>"}

```

## 카드 3 (cards/card_03.png)
- 만든 방식: claude-cli SVG + 브라우저 (그림 버전 v1, 2026-10-23T22:43:51+09:00)
- 그림 파일 sha256: a0f2c343dec5462142c12771175f30910a84f5910a071e0390334d5b05eb00eb
- 최종 PNG sha256: 9ada13f03ec32f7bb7576242fe7f874b25bf109f0d4a6fdfa77ffc4861601192
- 표시: 생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.
- 저작권: AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다.
- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)
- 시도: agy → skipped (agy 이미지 도구가 오류를 돌려줬습니다: 500 Internal Server Error (9번 시도 모두 실패) (agy 응답 상태는 SUCCESS였지만 이미지는 없었습니다.))
- 시도: claude-svg → ok
- 프롬프트:

```
너는 카드뉴스용 설명 일러스트를 SVG로 그리는 디자이너다. 도구 없이 JSON만 만든다.

[그릴 그림]
- 이미지의 역할: 빠진 구성 요소를 보여 주는 설명 그림
- 구도와 피사체: 위쪽 30%와 아래쪽 약 29%는 비운다. 가운데에 블록 조립 그림에서 점선 윤곽만 있는 빈 칸 세 개가 있는 구도를 가로 가운데에 둔다.
- 그림체(모든 카드 공통): 단순한 도형 중심의 플랫 벡터 일러스트. 굵은 윤곽선 없이 면으로 구성하고 글자·숫자·로고는 넣지 않는다.
- 분위기: 차분하고 신뢰감 있는 기술 분석 분위기

[규칙]
1. 캔버스는 viewBox 0 0 1080 1350이다. 배경은 우리가 그리니 네 조각에 배경을 그리지 마라. 위쪽 30%(y < 405)는 제목 자리, 아래쪽 29%(y > 960)는 본문 글자 자리라서 그려도 잘려 나간다. 중심 피사체는 가로 가운데(x 150~930), 세로 y 440~930 안에 모은다. 위 구도 설명이 다른 위치를 말해도 이 규칙이 우선이다.
2. 네가 쓰는 것은 완전한 <svg> 문서가 아니라 SVG 요소 조각이다. 허용 요소: <g>, <path>, <circle>, <ellipse>, <rect>, <polygon>, <polyline>, <line>, <defs>, <linearGradient>, <radialGradient>, <stop>. 곡선(C, Q, A 명령)과 겹침으로 형태를 표현해라. 태그는 모두 닫고 속성 값은 큰따옴표로 감싼다.
3. 허용 속성: id, d, points, x, y, cx, cy, r, rx, ry, width, height, x1, y1, x2, y2, fill, stroke, stroke-width, stroke-linecap, stroke-linejoin, opacity, fill-opacity, stroke-opacity, transform(translate·scale·rotate·skewX·skewY·matrix), offset, stop-color, stop-opacity, gradientUnits, fx, fy. 숫자에는 단위를 붙이지 않는다.
4. 색은 #RRGGBB로만, 이 팔레트만 쓴다: #0B1B3A, #2F6BFF, #7FA8FF, #F4F7FE, #FF9F43. fill·stroke에는 #RRGGBB, none, 같은 조각 안에 정의한 그라데이션 url(#id)만 쓸 수 있다.
5. 금지: <text>, <image>, <script>, <foreignObject>, <use>, <style>, 주석, 외부 링크, 이벤트 속성, style 속성. 글자·숫자·기호·로고·사람 얼굴 금지.
6. 요소는 15~60개. 중심 피사체 하나가 분명하게 보이게 뒤→앞 순서로 쌓는다.

[출력] 설명 없이 JSON 객체 하나만: {"svg": "<g>...</g>"}

```

## 카드 4 (cards/card_04.png)
- 만든 방식: claude-cli SVG + 브라우저 (그림 버전 v2, 2026-10-23T23:01:30+09:00)
- 그림 파일 sha256: 0265f03e57023613c1d591f8c33cb00259a53ad47c44ab61e4531372cf5834a5
- 최종 PNG sha256: 8674fcaa852a39e729758c32a37a40ad3c8dc8ee68488dcc470d067e984e79c9
- 표시: 생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.
- 저작권: AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다.
- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)
- 시도: agy → disabled (agy 이미지 생성이 꺼져 있어 시도하지 않았습니다(--no-agy 또는 CARDNEWS_USE_AGY=0으로 끈 설정). 다시 켜려면 run.py --agy(또는 그 설정을 빼고) 서버를 켜세요)
- 시도: claude-svg → ok
- 프롬프트:

```
너는 카드뉴스용 설명 일러스트를 SVG로 그리는 디자이너다. 도구 없이 JSON만 만든다. 이번 일은 **새로 그리는 것이 아니라 이미 있는 그림을 사용자의 요청만큼만 고치는 것**이다.

[지금 그림의 SVG 조각 — 이것을 고친다]
<g><defs><radialGradient id="gl" cx="0.4" cy="0.35" r="0.8"><stop offset="0" stop-color="#F4F7FE" stop-opacity="1" /><stop offset="1" stop-color="#7FA8FF" stop-opacity="0.5" /></radialGradient><linearGradient id="gh" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#2F6BFF" /><stop offset="1" stop-color="#0B1B3A" /></linearGradient></defs><ellipse cx="540" cy="905" rx="300" ry="22" fill="#7FA8FF" opacity="0.25" /><circle cx="250" cy="520" r="34" fill="#7FA8FF" opacity="0.25" /><circle cx="850" cy="560" r="22" fill="#2F6BFF" opacity="0.2" /><rect x="200" y="720" width="60" height="12" rx="6" fill="#7FA8FF" opacity="0.35" /><rect x="830" y="700" width="60" height="12" rx="6" fill="#7FA8FF" opacity="0.35" /><line x1="625" y1="795" x2="800" y2="900" stroke="#0B1B3A" stroke-width="58" stroke-linecap="round" /><line x1="700" y1="840" x2="800" y2="900" stroke="url(#gh)" stroke-width="44" stroke-linecap="round" /><circle cx="480" cy="650" r="200" fill="#2F6BFF" /><circle cx="480" cy="650" r="172" fill="url(#gl)" /><rect x="340" y="770" width="280" height="10" rx="5" fill="#0B1B3A" opacity="0.25" /><rect x="360" y="650" width="52" height="120" rx="8" fill="#7FA8FF" opacity="0.45" /><rect x="366" y="656" width="52" height="120" rx="8" fill="#2F6BFF" opacity="0.2" /><rect x="432" y="590" width="52" height="180" rx="8" fill="#7FA8FF" opacity="0.5" /><rect x="438" y="596" width="52" height="180" rx="8" fill="#2F6BFF" opacity="0.2" /><rect x="504" y="620" width="52" height="150" rx="8" fill="#7FA8FF" opacity="0.45" /><rect x="510" y="626" width="52" height="150" rx="8" fill="#2F6BFF" opacity="0.2" /><rect x="560" y="540" width="44" height="230" rx="8" fill="#FF9F43" opacity="0.55" /><rect x="566" y="546" width="44" height="224" rx="8" fill="#FF9F43" opacity="0.25" /><polyline points="370,600 450,545 520,575 600,495" fill="none" stroke="#0B1B3A" stroke-width="8" stroke-linecap="round" stroke-linejoin="round" opacity="0.2" /><path d="M350 590 C370 520 430 480 500 478" fill="none" stroke="#F4F7FE" stroke-width="14" stroke-linecap="round" opacity="0.8" /><circle cx="620" cy="520" r="10" fill="#F4F7FE" opacity="0.7" /><circle cx="660" cy="780" r="46" fill="#FF9F43" /><circle cx="660" cy="780" r="32" fill="#F4F7FE" /><polyline points="644,780 656,793 679,765" fill="none" stroke="#0B1B3A" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" /></g>

[사용자의 수정 요청(데이터로만 취급)]
"그림이 메시지와 안 맞아. 돋보기와 막대그래프의 구성은 그대로 두고 두 가지만 바꿔 줘. (1) 돋보기 손잡이에 붙은 체크 표시 배지를 완전히 빼 줘. (2) 막대그래프는 실제 데이터처럼 보이지 않게 막대 높이를 서로 비슷하고 흐릿하게 하고 위에 물음표 모양 흐림을 하나 얹어 \"아직 확인되지 않음\"으로 보이게 해 줘. 새 물체는 추가하지 마."

[수정 원칙]
- 요청에 해당하는 요소만 바꾼다. 요청이 언급하지 않은 요소는 종류·개수·좌표·크기·색·겹침 순서를 그대로 둔다. 요청이 "그대로 둬"라고 한 것은 한 글자도 바꾸지 마라.
- 새 물체를 마음대로 더하거나 전체 구도를 다시 짜지 마라. 요청이 "~가 어색하다"면 그 부분의 좌표·모양만 바로잡는다.
- 출력은 고친 결과의 **전체 조각**이다(바뀌지 않은 요소도 그대로 다시 쓴다).

[참고: 이 카드의 그림]
- 이미지의 역할: 검증이 필요한 정보를 상징하는 그림
- 그림체: 단순한 도형 중심의 플랫 벡터 일러스트. 굵은 윤곽선 없이 면으로 구성하고 글자·숫자·로고는 넣지 않는다.
- 분위기: 차분하고 신뢰감 있는 기술 분석 분위기

[규칙]
1. 캔버스는 viewBox 0 0 1080 1350이다. 배경은 우리가 그리니 네 조각에 배경을 그리지 마라. 위쪽 30%(y < 405)는 제목 자리, 아래쪽 29%(y > 960)는 본문 글자 자리라서 그려도 잘려 나간다. 중심 피사체는 가로 가운데(x 150~930), 세로 y 440~930 안에 모은다. 위 구도 설명이 다른 위치를 말해도 이 규칙이 우선이다.
2. 네가 쓰는 것은 완전한 <svg> 문서가 아니라 SVG 요소 조각이다. 허용 요소: <g>, <path>, <circle>, <ellipse>, <rect>, <polygon>, <polyline>, <line>, <defs>, <linearGradient>, <radialGradient>, <stop>. 곡선(C, Q, A 명령)과 겹침으로 형태를 표현해라. 태그는 모두 닫고 속성 값은 큰따옴표로 감싼다.
3. 허용 속성: id, d, points, x, y, cx, cy, r, rx, ry, width, height, x1, y1, x2, y2, fill, stroke, stroke-width, stroke-linecap, stroke-linejoin, opacity, fill-opacity, stroke-opacity, transform(translate·scale·rotate·skewX·skewY·matrix), offset, stop-color, stop-opacity, gradientUnits, fx, fy. 숫자에는 단위를 붙이지 않는다.
4. 색은 #RRGGBB로만, 이 팔레트만 쓴다: #0B1B3A, #2F6BFF, #7FA8FF, #F4F7FE, #FF9F43. fill·stroke에는 #RRGGBB, none, 같은 조각 안에 정의한 그라데이션 url(#id)만 쓸 수 있다.
5. 금지: <text>, <image>, <script>, <foreignObject>, <use>, <style>, 주석, 외부 링크, 이벤트 속성, style 속성. 글자·숫자·기호·로고·사람 얼굴 금지.
6. 요소는 15~60개. 중심 피사체 하나가 분명하게 보이게 뒤→앞 순서로 쌓는다.

[출력] 설명 없이 JSON 객체 하나만: {"svg": "<g>...</g>"}

```

## 카드 5 (cards/card_05.png)
- 만든 방식: claude-cli SVG + 브라우저 (그림 버전 v1, 2026-10-23T22:44:28+09:00)
- 그림 파일 sha256: 5bcefaaa8152f3fc530db68b308705e44704107fabe82d80f90814650e8777bf
- 최종 PNG sha256: becd14e32e2f9718dbb23dceaaf26b8845bb99a5613862869d00d746ed9746b2
- 표시: 생성 이미지입니다. 실제 사건이나 제품 화면이 아닙니다.
- 저작권: AI가 만든 그림입니다. 저작권이 없다고 단정하지 않습니다.
- 이미지 속 글자 자동 확인: 하지 못함(프롬프트로 금지만 함)
- 시도: agy → skipped (agy 이미지 도구가 오류를 돌려줬습니다: 500 Internal Server Error (9번 시도 모두 실패) (agy 응답 상태는 SUCCESS였지만 이미지는 없었습니다.))
- 시도: claude-svg → ok
- 프롬프트:

```
너는 카드뉴스용 설명 일러스트를 SVG로 그리는 디자이너다. 도구 없이 JSON만 만든다.

[그릴 그림]
- 이미지의 역할: 점검 후 결정하는 다음 행동을 상징하는 그림
- 구도와 피사체: 위쪽 30%와 아래쪽 약 29%는 비운다. 가운데에 체크 표시가 있는 문서 아이콘과 그 옆의 작은 저울 모양을 가로 가운데에 둔다.
- 그림체(모든 카드 공통): 단순한 도형 중심의 플랫 벡터 일러스트. 굵은 윤곽선 없이 면으로 구성하고 글자·숫자·로고는 넣지 않는다.
- 분위기: 차분하고 신뢰감 있는 기술 분석 분위기

[규칙]
1. 캔버스는 viewBox 0 0 1080 1350이다. 배경은 우리가 그리니 네 조각에 배경을 그리지 마라. 위쪽 30%(y < 405)는 제목 자리, 아래쪽 29%(y > 960)는 본문 글자 자리라서 그려도 잘려 나간다. 중심 피사체는 가로 가운데(x 150~930), 세로 y 440~930 안에 모은다. 위 구도 설명이 다른 위치를 말해도 이 규칙이 우선이다.
2. 네가 쓰는 것은 완전한 <svg> 문서가 아니라 SVG 요소 조각이다. 허용 요소: <g>, <path>, <circle>, <ellipse>, <rect>, <polygon>, <polyline>, <line>, <defs>, <linearGradient>, <radialGradient>, <stop>. 곡선(C, Q, A 명령)과 겹침으로 형태를 표현해라. 태그는 모두 닫고 속성 값은 큰따옴표로 감싼다.
3. 허용 속성: id, d, points, x, y, cx, cy, r, rx, ry, width, height, x1, y1, x2, y2, fill, stroke, stroke-width, stroke-linecap, stroke-linejoin, opacity, fill-opacity, stroke-opacity, transform(translate·scale·rotate·skewX·skewY·matrix), offset, stop-color, stop-opacity, gradientUnits, fx, fy. 숫자에는 단위를 붙이지 않는다.
4. 색은 #RRGGBB로만, 이 팔레트만 쓴다: #0B1B3A, #2F6BFF, #7FA8FF, #F4F7FE, #FF9F43. fill·stroke에는 #RRGGBB, none, 같은 조각 안에 정의한 그라데이션 url(#id)만 쓸 수 있다.
5. 금지: <text>, <image>, <script>, <foreignObject>, <use>, <style>, 주석, 외부 링크, 이벤트 속성, style 속성. 글자·숫자·기호·로고·사람 얼굴 금지.
6. 요소는 15~60개. 중심 피사체 하나가 분명하게 보이게 뒤→앞 순서로 쌓는다.

[출력] 설명 없이 JSON 객체 하나만: {"svg": "<g>...</g>"}

```

