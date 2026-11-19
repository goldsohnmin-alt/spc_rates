"""한 발행 건(SPC·종류·발행일·만기일)에 맞는 평가사 PDF를 찾아 읽는다.

- NICE: 회사명 검색 표(시공사·신용도연계기관) + 의견서 PDF.
- 한신평: Rating Summary PDF.
- 발행일 이전(당일 포함) 문서 중 최신 순으로 몇 개를 읽어, 회차 표에 '발행일·만기일'이 같은 줄이 있는 문서를 고른다.
  없으면 가장 최신 문서를 '회차 불명'으로 돌려준다(판정·주관회사·사업지에는 쓸 수 있음).
- 받은 PDF와 읽은 결과는 output/pdf/ 아래에 저장해 다시 받지 않는다(커밋 안 됨).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re

import requests

import kis_pdf
import nice_pdf
from agencies import Kis, Nice

BASE = os.path.dirname(os.path.abspath(__file__))
PDF_DIR = os.path.join(BASE, "output", "pdf")
LIST_CACHE = os.path.join(PDF_DIR, "listings.json")
MAX_DOCS = 4   # 출처별로 발행일 이전 문서를 최대 몇 개까지 읽을지


def _ymd(d) -> str:
    return d.isoformat() if isinstance(d, (dt.date, dt.datetime)) else str(d)


def _safe(s: str) -> str:
    return re.sub(r"[^\w.-]", "_", s)


def _plus(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(n)).isoformat()


def _match(p: dict, kind: str, f: str, g: str):
    """문서 회차표에서 이번 발행(발행일 f, 만기 g)에 맞는 회차를 찾는다 -> (맞는 회차, 날짜가 밀렸는지, 발행일만 같은 회차).
    - 발행일 같고 만기가 4일 이내 차이: 같은 회차(예정 만기가 휴일이라 밀림)
    - 발행일·만기가 둘 다 4일 이내로 같이 밀림: 같은 회차(예정 발행일이 주말이라 밀림. 2026-10-12 에오스제일차)
    """
    F, G = dt.date.fromisoformat(f), dt.date.fromisoformat(g)
    ser = [s for s in p.get("series", []) if s.get("kind") in (None, kind)]
    days = lambda a, b: abs((dt.date.fromisoformat(a) - b).days)
    same_day = [s for s in ser if s["issue"] == f]
    hit = [s for s in same_day if days(s["maturity"], G) <= 4]
    if hit:
        return hit, False, same_day
    shifted = [s for s in ser if 0 < days(s["issue"], F) <= 4 and days(s["maturity"], G) <= 4]
    return shifted, bool(shifted), same_day


class Evidence:
    def __init__(self):
        self._nice = self._kis = None
        self._list_cache: dict = {}
        os.makedirs(os.path.join(PDF_DIR, "nice"), exist_ok=True)
        os.makedirs(os.path.join(PDF_DIR, "kis"), exist_ok=True)

    # ---- 출처별 문서 목록 -------------------------------------------------
    @property
    def nice(self) -> Nice:
        if self._nice is None:
            self._nice = Nice()
        return self._nice

    @property
    def kis(self) -> Kis:
        if self._kis is None:
            self._kis = Kis()
        return self._kis

    def listing(self, spc: str, sources=("nice", "kis")) -> dict:
        """{'nice_table': [행...], 'docs': [{src, id, date, ...}], 'errors': [...]} - sources 의 평가사만 묻는다.
        출처별로 따로 기억하므로(실행 안 + 오늘 받은 것은 디스크) 나중에 다른 출처를 더 물어도 다시 받지 않는다."""
        out = {"nice_table": [], "docs": [], "errors": []}
        for src in sources:
            part = self._listing_src(spc, src)
            out["nice_table"] += part.get("nice_table", [])
            out["docs"] += part["docs"]
            out["errors"] += part["errors"]
        return out

    def _listing_src(self, spc: str, src: str) -> dict:
        key = f"{src}:{spc}"
        if key in self._list_cache:
            return self._list_cache[key]
        disk = self._load_lists()
        today = dt.date.today().isoformat()
        hit = disk.get(key)
        if hit and hit["fetched"] == today:      # 오늘 받은 목록이면 다시 묻지 않는다
            self._list_cache[key] = hit["data"]
            return hit["data"]
        out = {"nice_table": [], "docs": [], "errors": []}
        if src == "nice":
            try:
                t = self.nice.search_name(spc)
                out["nice_table"] = t.drop(columns=[c for c in t.columns if c == "cmpCd"]).to_dict("records")
                for cd in dict.fromkeys(t.get("cmpCd", [])):
                    for o in self.nice.opinions(cd):
                        out["docs"].append(dict(src="nice", id=o["docId"], date=o["date"].replace(".", "-"),
                                                series=o["series"], kind=o["kind"]))
            except Exception as e:  # 한 곳이 실패해도 다른 곳으로 계속
                out["errors"].append(f"NICE 조회 실패: {e}")
        else:
            try:
                code = self.kis.find_code(spc)
                if code:
                    for s in self.kis.summaries(code):
                        w = s["writedate"]
                        out["docs"].append(dict(src="kis", id=s["fileName"], date=f"{w[:4]}-{w[4:6]}-{w[6:]}", item=s))
            except Exception as e:
                out["errors"].append(f"한신평 조회 실패: {e}")
        self._list_cache[key] = out
        if not out["errors"]:
            disk[key] = {"fetched": today, "data": out}
            with open(LIST_CACHE, "w", encoding="utf-8") as f:
                json.dump(disk, f, ensure_ascii=False, default=str)
        return out

    def _load_lists(self) -> dict:
        if not hasattr(self, "_disk"):
            self._disk = {}
            if os.path.exists(LIST_CACHE):
                with open(LIST_CACHE, encoding="utf-8") as f:
                    self._disk = json.load(f)
        return self._disk

    # ---- 문서 받기·읽기 ---------------------------------------------------
    def read(self, doc: dict) -> dict | None:
        src = doc["src"]
        base = os.path.join(PDF_DIR, src, _safe(doc["id"]).removesuffix(".pdf"))
        pdf, js = base + ".pdf", base + ".json"
        if os.path.exists(js):
            with open(js, encoding="utf-8") as f:
                return json.load(f)
        if not os.path.exists(pdf):
            ok = (self.nice.download(doc["id"], pdf) if src == "nice"
                  else self.kis.download(doc["item"], pdf))
            if not ok:
                return None
        parsed = (nice_pdf.parse(nice_pdf.read_text(pdf)) if src == "nice"
                  else kis_pdf.parse(kis_pdf.read_text(pdf)))
        parsed.update(src=src, doc_id=doc["id"], doc_date=doc["date"])
        with open(js, "w", encoding="utf-8") as f:
            json.dump(parsed, f, ensure_ascii=False)
        return parsed

    def _read_safe(self, d: dict, src: str, res: dict) -> dict | None:
        try:
            return self.read(d)
        except Exception as e:
            # 내려받기(접속) 문제는 '조회 실패'(다음 실행 때 다시), PDF 내용 문제는 '읽기 실패'(코드 수정 필요)
            what = "조회 실패" if isinstance(e, requests.RequestException) else "읽기 실패"
            msg = f"{src} PDF {what}: {e}"
            if msg not in res["errors"]:
                res["errors"].append(msg)
            return None

    def for_issue(self, spc: str, kind: str, issue, maturity, skip_kis=None) -> dict:
        """출처별 {'match': 회차까지 맞는 문서, 'series': 맞는 회차 목록, 'latest': 최신 문서, 'loose': 발행일만 같은 회차}.

        NICE를 먼저 본다. skip_kis(res) 가 True 면(NICE 문서만으로 '아니오'가 분명) 한신평은 묻지 않는다.
        처음 보는 SPC 확인이 거의 절반으로 준다(2026-11-19). 시공사 건이면 한신평까지 본다(P 문장 등).
        """
        f, g = _ymd(issue), _ymd(maturity)
        empty = {"match": None, "series": None, "latest": None, "loose": None}
        res = {"nice_table": [], "errors": [], "nice": dict(empty), "kis": dict(empty), "kis_skipped": False}
        for src in ("nice", "kis"):
            if src == "kis" and skip_kis and skip_kis(res):
                res["kis_skipped"] = True
                break
            lst = self.listing(spc, (src,))
            res["nice_table"] += lst["nice_table"]
            res["errors"] += lst["errors"]
            docs = sorted((d for d in lst["docs"] if d["src"] == src and d["date"] <= f),
                          key=lambda d: d["date"], reverse=True)
            # 발행일 뒤 30일 안에 나온 문서(수시평가 등): 회차표만 참고한다(발행 계획이 바뀌어 실제 만기가 적힌 경우)
            later = sorted((d for d in lst["docs"] if d["src"] == src and f < d["date"] <= _plus(f, 30)),
                           key=lambda d: d["date"])
            r = {"match": None, "series": None, "latest": None, "loose": None, "shifted": False}
            for d in docs[:MAX_DOCS]:
                p = self._read_safe(d, src, res)
                if not p:
                    continue
                hit, shifted, same_day = _match(p, kind, f, g)
                if hit and not r["series"]:
                    r["series"], r["shifted"] = hit, shifted
                elif same_day and not r["series"] and not r["loose"]:
                    # 발행일은 같은데 만기가 예정과 다름(월 단위 예정 회차를 묶어 3개월물로 발행 등) -> 회차만 참고
                    r["loose"] = sorted(same_day, key=lambda x: abs((dt.date.fromisoformat(x["maturity"])
                                                                      - dt.date.fromisoformat(g)).days))
                if not p.get("factors"):   # 등급 근거가 없는 문서(옛 형식·등급 취소)는 판정에 쓰지 않는다
                    continue
                r["latest"] = r["latest"] or p
                if hit:
                    r["match"] = p
                    break
            if not r["series"]:
                for d in later[:3]:
                    p = self._read_safe(d, src, res)
                    hit, shifted, _ = _match(p, kind, f, g) if p else ([], False, [])
                    if hit and not shifted:          # 뒤 문서는 발행일·만기가 그대로 맞을 때만
                        r["series"], r["loose"] = hit, None
                        break
            res[src] = r
        return res
