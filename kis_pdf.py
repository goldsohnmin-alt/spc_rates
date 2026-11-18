"""한신평 Rating Summary PDF 1~3쪽에서 판정·채움에 필요한 부분을 뽑는다.

- 주요 평가요소: 등급의 근거가 된 의무(예: '롯데건설(주)의 SPC에 대한 자금보충 및 미이행시 기초자산 채무인수').
  여기에 시공사가 나오면 시공사 신용공여 건이다.
- 평가 개요 표: 회차·신용등급·발행금액·발행일·만기일(한 PDF에 여러 회차가 있을 수 있다).
  아래쪽 '신용등급 기부여 내역' 표(지난 회차)는 읽지 않는다.
- 주관회사(I), 유동화 유형(D), 사업 개요 표(사업명·사업부지) 또는 '본 사업은 ... 일원에 ...을 개발하는' 문장(O).
"""
from __future__ import annotations

import re

import pdfplumber

SERIES_RE = re.compile(
    r"(ABSTB|ABCP|전자단기사채|기업어음)\s+제\s*([\d-]+)\s*회(?:차)?\s+(A[123][+-]?)\s*\(sf\)\s+"
    r"([\d,.]+)\s*억원\s+(\d{4}\.\d{2}\.\d{2})\s+(\d{4}\.\d{2}\.\d{2})")


def read_text(path: str, pages: int = 3) -> str:
    """줄 끝 공백을 남겨서 읽는다(줄 끝 공백이 있으면 원래 띄어쓰기, 없으면 단어 중간 줄바꿈)."""
    with pdfplumber.open(path) as p:
        return "\n".join((pg.extract_text(keep_blank_chars=True) or "") for pg in p.pages[:pages])


def join_lines(s: str) -> str:
    """줄을 이어 한 문장으로: 줄 끝에 공백이 있었으면 띄우고, 없으면 붙인다."""
    s = re.sub(r"[ \t]+\n\s*", " ", s)
    s = re.sub(r"\n\s*", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _site(text: str) -> tuple[str | None, str | None]:
    """(사업지 문장, 출처) - '사업 개요' 표가 있으면 표, 없으면 '본 사업은 ...' 문장."""
    m = re.search(r"사업\s*개요\s*\n(.*?)(?:\n시행|\n시공사|\n[^\n]*?(?:기초자산|차주|한편))", text, re.S)
    if m:
        f = dict(re.findall(r"^(사업명|사업내용|사업부지)\s+(.+)$", m.group(1), re.M))
        if f.get("사업부지") and f.get("사업명"):
            site = f["사업부지"].strip()
            words = f["사업명"].strip().split()
            # 사업명 앞쪽의 지명이 사업부지에 이미 있으면 뺀다('하남 미사 오피스텔' -> '미사 오피스텔')
            while len(words) > 2 and words[0] in site:
                words.pop(0)
            return f"{site} {' '.join(words)}", "표"
    flat = join_lines(text)
    m = re.search(r"본\s*(?:건\s*)?사업은\s*(.+?일원)\s*(?:의\s*부지)?(?:에서|에)\s*(.{2,80}?)(?:을|를)\s*"
                  r"(개발|조성|신축|건축|재건축)하는", flat)
    if m:
        what = re.sub(r"\(이하[^)]*\)", "", m.group(2))
        what = re.sub(r"\s*등$", "", what.strip())
        return f"{m.group(1)} {what} 등 {m.group(3)}사업", "문장"
    return None, None


def parse(text: str) -> dict:
    out = {}
    head = re.split(r"신용등급\s*기부여\s*내역|주요\s*평가요소", text)[0]
    out["series"] = [dict(kind={"전자단기사채": "ABSTB", "기업어음": "ABCP"}.get(m.group(1), m.group(1)),
                          series=m.group(2), rating=m.group(3), amount=_num(m.group(4)),
                          issue=m.group(5).replace(".", "-"), maturity=m.group(6).replace(".", "-"))
                     for m in SERIES_RE.finditer(head)]
    m = re.search(r"주요\s*평가요소(.*?)(유동화\s*개요|위험요인)", text, re.S)
    if m:
        bullets = re.split(r"•\s*", m.group(1))[1:]
        # 각주 번호와 그 뒤 쪽머리(예: '... 채무인수 1 하이드인덕평제일차(주)')를 자른다
        out["factors"] = [re.split(r"\s\d{1,2}(?:\s|$)", join_lines(b))[0].strip() for b in bullets if b.strip()]
    m = re.search(r"주관회사\s+(\S+)", text)
    if m:
        out["arranger"] = m.group(1)
    m = re.search(r"유동화\s*유형\s+([^\n]+)", text)
    if m:
        out["type"] = m.group(1).strip()
    out["site"], out["site_from"] = _site(text)
    return out
