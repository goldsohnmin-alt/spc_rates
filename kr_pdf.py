"""한기평 Structured Finance 평가의견 PDF 1~2쪽에서 판정·채움에 필요한 부분을 뽑는다(2026-11-20 확인).

1쪽 형식:
  신용등급 / 구분 발행금액(또는 발행한도) 발행일 만기일 신용등급 / 제4회 ABSTB 679 억원 . 2026.11.20 2027.01.20 A1(sf)
  요약정보 / 거래유형 PF Loan 유동화 / ... / 주관회사 케이비증권(주)
  평정논거 / 주요평정요인  현대건설㈜의 자금보충(미이행시 채무인수) 의무 / ■ 유동화 개요 ...
  본문: '차주는 서울특별시 강서구 가양동 92-1 일원에서 추진중인 복합시설 개발사업(이하 '본 사업')의 시행사'
"""
from __future__ import annotations

import re

from kis_pdf import _site as _kis_site
from kis_pdf import join_lines, read_text  # noqa: F401  (read_text 는 evidence 에서 씀)

SERIES_RE = re.compile(r"제\s*([\d-]+)\s*회\s+(ABSTB|ABCP|ABS)\s+(\d[\d,]*(?:\.\d+)?)\s*(억원|원)\s*\.?\s+"
                       r"(\d{4}\.\d{2}\.\d{2})\s+(\d{4}\.\d{2}\.\d{2})\s+([A-D][123]?[+-]?)\s*\(sf\)")
PROVINCE = r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충청|충북|충남|전라|전북|전남|경상|경북|경남|제주)[가-힣]*"


def _site(text: str) -> tuple[str | None, str | None]:
    flat = join_lines(text)
    # '차주는 <주소> 일원에서 추진중인 <사업>(이하 '본 사업')' / '<주소> 일원에 <시설>을 개발하는 사업(이하 '본 사업')'
    m = re.search(rf"({PROVINCE}\s[^()']{{3,60}}?일원)(?:에서|에)\s*(?:추진\s*중인\s*)?([^()']{{2,60}}?)"
                  r"(?:\(이하\s*['‘\"“]본\s*사업|을\s*개발하는\s*사업|를\s*개발하는\s*사업)", flat)
    if m:
        what = re.sub(r"\s+", " ", m.group(2)).strip()
        if not what.endswith("사업"):
            what += " 개발사업"
        return f"{m.group(1)} {what}", "문장"
    return _kis_site(text)


def parse(text: str) -> dict:
    out = {}
    head = re.split(r"요약정보|평가의견|\[기발행", text)[0]
    out["series"] = []
    for m in SERIES_RE.finditer(head):
        amt = float(m.group(3).replace(",", ""))
        if m.group(4) == "원":
            amt = round(amt / 1e8, 2)
        out["series"].append(dict(kind=m.group(2), series=m.group(1), amount=amt,
                                  issue=m.group(5).replace(".", "-"), maturity=m.group(6).replace(".", "-"),
                                  rating=m.group(7)))
    # 평정논거 칸은 두 칸 배치라 글자가 '주요평정요인' 앞뒤로 흩어져 나온다(2026-11-20 케이비제기제일차·
    # 케이아이에스안심제십삼차: 요인 문장 중간에 '주요평정요인'이 끼어 듦). '평정논거'부터 '■'나 쪽 끝까지 모은다.
    m = re.search(r"평정\s*논거(?:\s*\(Key Rating Rationale\))?(.*?)(?=■|www\.korearatings\.com|\Z)", text, re.S)
    if m and "주요평정요인" in re.sub(r"\s+", "", m.group(1)):
        block = join_lines(re.sub(r"주요\s*평정\s*요인", " ", m.group(1)))
        # 글머리표는 깨진 글자(사용자 정의 영역, 예: U+F0A7)나 •▪ 로 온다
        parts = [p.strip() for p in re.split("[•▪\ue000-\uf8ff]", block) if p.strip()]
        out["factors"] = [re.sub(r"\s+", " ", p) for p in parts]
    m = re.search(r"주관회사\s+(\S+)", text)
    if m:
        out["arranger"] = m.group(1)
    m = re.search(r"거래유형\s+([^\n]+)", text)
    if m:
        out["type"] = m.group(1).strip()
    m = re.search(r"시공(?:은|사는)\s*([가-힣A-Za-z0-9]+(?:㈜|\(주\))|(?:㈜|\(주\))[가-힣A-Za-z0-9]+)", join_lines(text))
    if m:
        out["builder"] = m.group(1)
    out["site"], out["site_from"] = _site(text)
    return out
