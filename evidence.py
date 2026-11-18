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

    def listing(self, spc: str) -> dict:
        """{'nice_table': [행...], 'docs': [{src, id, date, ...}]} (한 번 실행 안에서는 다시 묻지 않는다)."""
        if spc in self._list_cache:
            return self._list_cache[spc]
        disk = self._load_lists()
        hit = disk.get(spc)
        if hit and hit["fetched"] == dt.date.today().isoformat():   # 오늘 받은 목록이면 다시 묻지 않는다
            self._list_cache[spc] = hit["data"]
            return hit["data"]
        out = {"nice_table": [], "docs": [], "errors": []}
        try:
            t = self.nice.search_name(spc)
            out["nice_table"] = t.drop(columns=[c for c in t.columns if c == "cmpCd"]).to_dict("records")
            for cd in dict.fromkeys(t.get("cmpCd", [])):
                for o in self.nice.opinions(cd):
                    out["docs"].append(dict(src="nice", id=o["docId"], date=o["date"].replace(".", "-"),
                                            series=o["series"], kind=o["kind"]))
        except Exception as e:  # 한 곳이 실패해도 다른 곳으로 계속
            out["errors"].append(f"NICE 조회 실패: {e}")
        try:
            code = self.kis.find_code(spc)
            if code:
                for s in self.kis.summaries(code):
                    w = s["writedate"]
                    out["docs"].append(dict(src="kis", id=s["fileName"], date=f"{w[:4]}-{w[4:6]}-{w[6:]}", item=s))
        except Exception as e:
            out["errors"].append(f"한신평 조회 실패: {e}")
        self._list_cache[spc] = out
        if not out["errors"]:
            disk[spc] = {"fetched": dt.date.today().isoformat(), "data": out}
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

    def for_issue(self, spc: str, kind: str, issue, maturity) -> dict:
        """출처별 {'match': 회차까지 맞는 문서, 'series': 맞는 회차 목록, 'latest': 최신 문서}."""
        lst = self.listing(spc)
        f, g = _ymd(issue), _ymd(maturity)
        res = {"nice_table": lst["nice_table"], "errors": list(lst["errors"])}
        for src in ("nice", "kis"):
            docs = sorted((d for d in lst["docs"] if d["src"] == src and d["date"] <= f),
                          key=lambda d: d["date"], reverse=True)
            r = {"match": None, "series": None, "latest": None}
            for d in docs[:MAX_DOCS]:
                try:
                    p = self.read(d)
                except Exception as e:
                    res["errors"].append(f"{src} PDF 읽기 실패: {e}")
                    continue
                if not p or not p.get("factors"):   # 등급 취소 문서 등 근거가 없는 문서는 건너뛴다
                    continue
                r["latest"] = r["latest"] or p
                hit = [s for s in p.get("series", []) if s["issue"] == f and s["maturity"] == g
                       and (s.get("kind") in (None, kind))]
                if hit:
                    r["match"], r["series"] = p, hit
                    break
            res[src] = r
        return res
