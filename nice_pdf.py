"""NICE 'CREDIT OPINION – STRUCTURED FINANCE' PDF 1쪽 요약표에서 판정·채움에 필요한 부분을 뽑는다.

1쪽 형식(2026-11-18 확인):
  평가등급 2026년 11월 10일 유동화전자단기사채 본평가
  구 분 발행금액(억원) 발행일 만기일 평가등급 / 제1회 772 2026.09.30 2026.12.01 A1 (sf)  (여러 줄일 수 있음)
  유동화구조 / 기초자산 / 본건 사업 <사업지> (시행사 : ..., 시공사 : ...) / 업무수탁자 / 자산관리자 / 주관회사
  주요 위험요소 및 통제방안 / 평가근거 ▪ <등급 근거>
"""
from __future__ import annotations

import re


from kis_pdf import join_lines, read_text as _read

FIELDS = ["유동화구조", "기초자산", "본건 사업", "업무수탁자", "자산관리자", "주관회사", "법률자문기관",
          "자산보유자", "신용공여기관", "위탁자/수탁자", "주요 위험요소"]
SERIES_RE = re.compile(r"(?:제\s*)?([\d-]+)\s*회(?:차)?\s+([\d,.]+)\s+(\d{4}\.\d{2}\.\d{2})\s+(\d{4}\.\d{2}\.\d{2})\s+"
                       r"(A[123][+-]?)\s*\(sf\)")


def read_text(path: str, pages: int = 3) -> str:
    return _read(path, pages)


def _field(text: str, name: str) -> str | None:
    others = "|".join(re.escape(f) for f in FIELDS if f != name)
    m = re.search(rf"{re.escape(name)}\s+(.*?)(?=\n\s*(?:{others})\b|\n주\)|\n평가근거)", text, re.S)
    return join_lines(m.group(1)) if m else None


def parse(text: str) -> dict:
    out = {}
    head = text.split("유동화개요")[0]
    kind = "ABCP" if "기업어음" in head else ("ABSTB" if "전자단기사채" in head else None)
    out["series"] = [dict(kind=kind, series=m.group(1), amount=float(m.group(2).replace(",", "")),
                          issue=m.group(3).replace(".", "-"), maturity=m.group(4).replace(".", "-"),
                          rating=m.group(5)) for m in SERIES_RE.finditer(head)]
    for key, name in [("type", "유동화구조"), ("asset", "기초자산"), ("project", "본건 사업"),
                      ("arranger", "주관회사")]:
        v = _field(text, name)
        if v:
            out[key] = v
    if out.get("project"):
        # '(시행사 : ..., 시공사 : ...)' 를 떼어 사업지와 시공사로 나눈다
        m = re.search(r"\(([^()]*(?:시\s*공\s*사|시\s*행\s*사)\s*:[^()]*)\)\s*$", out["project"])
        if m:
            out["site"] = re.sub(r"[「」“”\"]", "", out["project"][:m.start()]).strip()
            c = re.search(r"시\s*공\s*사\s*:\s*([^,)]+)", m.group(1))
            if c:
                out["builder"] = c.group(1).strip()
        else:
            out["site"] = re.sub(r"[「」“”\"]", "", out["project"]).strip()
    m = re.search(r"주\s*요\s*평가근거는\s*아래와\s*같다\.\s*(.*?)(?=\n유동화구조|\n기초자산\n|\Z)", text, re.S)
    if m:
        bullets = [join_lines(b) for b in re.split(r"▪", m.group(1))[1:] if b.strip()]
        if not bullets:
            # 글머리표 글자가 깨져 안 읽히는 PDF: 빈 줄 전까지의 줄을 근거로 본다
            block = re.split(r"\n\s*\n", m.group(1).strip())[0]
            bullets = [ln.strip() for ln in block.split("\n") if ln.strip()]
        out["factors"] = bullets
    return out
