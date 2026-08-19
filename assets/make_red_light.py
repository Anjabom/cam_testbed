#!/usr/bin/env python3
"""빨간불 목업 그림을 만든다 — `overlay:` 합성 자극의 재료.

★왜 그림이 필요한가★ 정지선 관문은 ★빨간 박스를 본 뒤에만★ 돌고(traffic_light
._sl_should_run), 정지 판정 자체도 RED 확정이 있어야 시작된다. 그런데 손에 있는
주행 영상에는 신호등이 한 번도 나오지 않는다(실측: 세 영상 186프레임 샘플에서
검출 0회). 실차 절차서가 '사람이 목업 신호등을 삼각대에 세운다'로 푸는 대목을,
영상에서는 이 그림을 화면에 합성해서 대신한다.

★이 그림은 정답이 아니다★ 대상 노드의 YOLO 가 이걸 실제로 red 로 검출해야만
아무 일이든 일어난다. 검출을 못 하면 결과에 '검출 0회'로 남을 뿐이고, 그래서 이
장치로는 판정을 통과시킬 수 없다.

실측(combined_light/best.pt, 1920x1080 프레임에 합성):
    폭 120px → conf 0.45 / 폭 180px → 0.84 / 폭 260px → 0.87 / 폭 360px → 0.87

★진짜 목업 사진이 있으면 그걸 쓰는 게 낫다★ — 같은 이름으로 덮어쓰면 된다
(알파 채널이 있는 PNG 면 배경이 비쳐 더 자연스럽다).

    python3 assets/make_red_light.py
"""
import cv2
import numpy as np

W = 480                      # 원본 크기. 합성할 때 시나리오의 width 로 줄인다
H = W // 3

img = np.full((H, W, 3), 38, np.uint8)
cv2.rectangle(img, (0, 0), (W - 1, H - 1), (30, 30, 30), 6)      # 등기구 테두리
r = int(H * 0.34)
for i, (col, on) in enumerate([((40, 40, 220), 1),        # 빨강 ★점등★
                               ((40, 180, 220), 0),       # 노랑
                               ((90, 200, 90), 0)]):      # 초록
    cx, cy = int(W * (0.18 + 0.32 * i)), H // 2
    cv2.circle(img, (cx, cy), r, col if on else (60, 60, 60), -1, cv2.LINE_AA)
    if on:                                                # 점등된 램프의 심지
        cv2.circle(img, (cx, cy), int(r * 0.6), (120, 120, 255), -1, cv2.LINE_AA)

cv2.imwrite("assets/red_light_mock.png", img)
print(f"assets/red_light_mock.png  {W}x{H}")
