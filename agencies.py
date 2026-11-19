"""신용평가사 공개 정보 조회 (2026-11-18 확인).

- NICE: 자산유동화증권 상세검색(무료). PDF 없이 표로 '시공사'·'신용도연계기관'·주관사·기초자산을 준다.
  https://www.nicerating.com/disclosure/assetDetailSearch.do?searchType=cmpNmCheck&searchText=<이름>
- 한신평(KIS): 회사 검색(corp_presearch.json) -> 회사 코드 -> 등급 탭(search_corpTab1.json)의
  Rating Summary PDF(무료, checkFeeFile.json 이 needLoginYn=N 이라고 답함). PDF 1쪽에 회차·주관회사·
  '주요 평가요소'(누구의 의무로 등급을 줬는지)·사업명이 있다.
- 한기평(KR): 보고서는 유료 서비스라고 안내한다. 사용하지 않는다.

사이트 부담을 줄이려고 요청 사이에 PAUSE 초를 쉰다.
해외 서버(GitHub Actions)에서는 NICE가 가끔 400·연결 끊김으로 거절한다(2026-11-19 확인) -> 쉬었다가 다시 시도한다.
"""
from __future__ import annotations

import io
import re
import time

import pandas as pd
import requests

PAUSE = 3.0
RETRY_WAITS = (15, 60, 180)   # 실패하면 이만큼(초) 쉬고 세션을 새로 만들어 다시 시도
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}


def _bare(name: str) -> str:
    """검색어용: (주)·㈜·(유)·유동화전문·공백 제거."""
    s = str(name or "")
    s = re.sub(r"\((주|유)\)|㈜|주식회사|유한회사", "", s)
    return re.sub(r"\s+", "", s)


class _Site:
    def __init__(self):
        self.reset()

    def reset(self):
        self.s = requests.Session()
        self.s.headers.update(UA)

    def call(self, method: str, url: str, timeout: int = 30, **kw) -> requests.Response:
        last = None
        for wait in (0,) + RETRY_WAITS:
            if wait:
                time.sleep(wait)
                self.reset()
            try:
                r = self.s.request(method, url, timeout=timeout, **kw)
                r.raise_for_status()
                time.sleep(PAUSE)
                return r
            except requests.RequestException as e:
                last = e
        raise last


class Nice(_Site):
    URL = "https://www.nicerating.com/disclosure/assetDetailSearch.do"

    def search_name(self, name: str) -> pd.DataFrame:
        """유동화회사명으로 검색. 이름이 정확히 같은 줄만 돌려준다(부분일치 제외)."""
        r = self.call("GET", self.URL, params={"searchType": "cmpNmCheck", "searchText": _bare(name)})
        codes = re.findall(r"goView\('[^']*',\s*'(\d+)',\s*'([^']*)'\)", r.text)
        for t in pd.read_html(io.StringIO(r.text)):
            cols = [str(c) for c in t.columns]
            if "시공사" in cols and "신용도연계기관" in cols:
                t = t.rename(columns={"유동화 회사명": "회사명"})
                if len(codes) == len(t):
                    t["cmpCd"] = [c[0] for c in codes]
                return t[t["회사명"].map(_bare) == _bare(name)].reset_index(drop=True)
        return pd.DataFrame()

    def opinions(self, cmp_cd: str) -> list[dict]:
        """유동화회사 상세 화면의 ABSTB·ABCP 의견서 목록(공개 링크): docId, 회차, 종류, 등급확정일. 최신순."""
        r = self.call("GET", "https://www.nicerating.com/disclosure/securitizationCompanyGrade.do",
                      params={"cmpCd": cmp_cd, "seriesNm": ""})
        out = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", r.text, re.S):
            doc = re.search(r"fncFileDown\('([\w-]+)'\)", tr)
            cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                     for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if doc and len(cells) >= 8 and cells[1] in ("ABSTB", "ABCP"):
                dates = [c for c in cells if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", c)]
                out.append(dict(docId=doc.group(1), series=cells[0], kind=cells[1],
                                date=dates[1] if len(dates) > 1 else (dates[0] if dates else "")))
        return sorted(out, key=lambda x: x["date"], reverse=True)

    def download(self, doc_id: str, path: str) -> bool:
        r = self.call("GET", "https://www.nicerating.com/common/fileDown.do", params={"docId": doc_id}, timeout=60)
        if r.content[:4] != b"%PDF":
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True


class Kis(_Site):
    BASE = "https://www.kisrating.com"

    def reset(self):
        super().reset()
        try:
            self.s.get(self.BASE + "/ratingsSearch/corp_search.do", timeout=30)
        except requests.RequestException:
            pass

    def find_code(self, name: str) -> str | None:
        r = self.call("POST", self.BASE + "/ratingsSearch/corp_presearch.json",
                      data={"searchType": "1", "searchKeyword": _bare(name)})
        for x in r.json().get("dataList1") or []:
            if _bare(x.get("upcheOpt")) == _bare(name):
                return x.get("kiscd")
        return None

    def summaries(self, code: str) -> list[dict]:
        """등급 탭의 Rating Summary 목록(최신순): fileName, writedate, title."""
        self.call("POST", self.BASE + "/ratingsSearch/corp_overview.do", data={"kiscd": code})
        r = self.call("POST", self.BASE + "/ratingsSearch/search_corpTab1.json", data={"kiscd": code})
        out = []
        for m in re.finditer(r"fn_file\('R8',\s*'2',\s*'([^']*)',\s*'([^']+\.pdf)',\s*'Rating Summary',\s*'(\d{8})'\)", r.text):
            item = dict(title=m.group(1), fileName=m.group(2), writedate=m.group(3))
            if item not in out:
                out.append(item)
        return sorted(out, key=lambda x: x["writedate"], reverse=True)

    def download(self, item: dict, path: str) -> bool:
        d = dict(fileName=item["fileName"], fileTitle=item["title"], menuCd="R8", gubun="2",
                 writedate=item["writedate"])
        fee = self.call("POST", self.BASE + "/checkFeeFile.json", data=d).json()
        if fee.get("needLoginYn") != "N":  # 유료·로그인 필요 자료는 받지 않는다
            return False
        r = self.call("POST", self.BASE + "/fileDown.do", data=d, timeout=60)
        if r.content[:4] != b"%PDF":
            return False
        with open(path, "wb") as f:
            f.write(r.content)
        return True
