"""한국기업평가(KR) 공개 등급공시 - 자산유동화증권 평가의견 (2026-11-20 확인).

화면: 등급공시 > 등급속보 > 신용등급공시, 대상 '자산유동화증권(ABS)'만.
- 목록: POST /ajaxf/frDisclosureSvc/getRatingDisclosureList.do (SVCTY_CD=05) -> data33.Data
  (회사명, 종류 ABSTB/ABCP/ABS, 회차, 발행액, 등급, 평가일, 평가의견 파일 키)
- 평가의견 PDF: 화면의 PDF 버튼과 같은 순서
  1) /cms/frDisclosureCon/frReportPayUserChk.do  (사이트가 유료 여부를 답한다: isShow = "0" 이면 무료)
  2) /ajaxa/fileCpnt/reportFileDown.do            (무료일 때만 받는다. 유료 자료는 받지 않는다)
- 사용자 주의: 빠르게 연속해서 받으면 사이트가 접속을 막고, 풀리는 데 시간이 걸린다 -> PAUSE 를 넉넉히.
"""
from __future__ import annotations

import re
import time
import urllib.parse

import requests

BASE = "https://www.korearatings.com"
MENU = "/cms/frCmnCon/index.do?MENU_ID=360"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36",
      "Accept-Language": "ko-KR,ko;q=0.9"}
# 화면 소스에 박혀 있는 사이트·메뉴·콘텐츠 번호(암호화 값). 화면에서 다시 읽고, 못 읽으면 이 값을 쓴다.
ENC_DEFAULT = ("3xJQi2TwslfHECDe7X6Ewg==", "FrJGT@.Iq0fkkJkCF2BUFg==", "m4R0rUAW4REnW0XPhHfDCw==")
PAUSE = 10.0


class Blocked(requests.RequestException):
    """사이트가 막은 것으로 보이는 응답(PDF 대신 다른 내용, 오류 코드, 연결 끊김)."""


class Kr:
    def __init__(self, pause: float = PAUSE):
        self.pause = pause
        self.s = requests.Session()
        self.s.headers.update(UA)
        r = self.s.get(BASE + MENU, timeout=30)
        r.raise_for_status()
        m = re.search(r"\"03\"\+'\\\\', \\\\'\\\\', \\\\'([^\\\\]+)\\\\', \\\\'([^\\\\]+)\\\\', \\\\'([^\\\\]+)\\\\'", r.text)
        self.enc = m.groups() if m else ENC_DEFAULT

    def listing(self, start: str, end: str) -> list[dict]:
        """start~end(YYYY-MM-DD, 최대 90일) 자산유동화증권 등급공시 목록."""
        data = {"MENU_ID": "360", "CONTENTS_NO": "1", "SITE_NO": "2", "COMP_CD": "",
                "STDT": start, "ENDT": end, "SVCTY_CD": "05"}
        r = self.s.post(BASE + "/ajaxf/frDisclosureSvc/getRatingDisclosureList.do", data=data, timeout=30,
                        headers={"X-Requested-With": "XMLHttpRequest", "Referer": BASE + MENU})
        r.raise_for_status()
        rows = ((r.json().get("data") or {}).get("data33") or {}).get("Data") or []
        time.sleep(self.pause)
        return rows

    def download(self, row: dict, path: str) -> bool:
        """평가의견 PDF 를 받는다. 사이트가 무료(isShow=0)라고 답할 때만. 막힌 것 같으면 Blocked."""
        if not row.get("RTNG_OPN_FILE_NM"):
            return False
        q = {"dumm": "asdf", "compCd": row["COMP_CD"], "compNm": row["COMP_NM"],
             "encFileNm": row["RTNG_OPN_FILE_NM"], "encSvcSeqNo": row["SVC_ID"], "evalNo": row["EVAL_SEQNO"],
             "rptNo": "03", "fileName": "", "popYn": "false",
             "encSiteNo": self.enc[0], "encMenuNo": self.enc[1], "encContentsNo": self.enc[2]}
        try:
            chk = self.s.get(BASE + "/cms/frDisclosureCon/frReportPayUserChk.do?" + urllib.parse.urlencode(q),
                             timeout=30, headers={"Referer": BASE + MENU})
        except requests.RequestException as e:
            raise Blocked(f"권한 확인 접속 실패: {e}")
        if chk.status_code != 200:
            raise Blocked(f"권한 확인 응답 {chk.status_code}")
        m = re.search(r'var\s+isShow\s*=\s*"(\d)"', chk.text)
        if not m:
            raise Blocked(f"권한 확인 화면이 예상과 다름(길이 {len(chk.text)})")
        if m.group(1) != "0":
            return False          # 유료 안내가 뜨는 자료는 받지 않는다
        d = {k: q[k] for k in ("dumm", "encFileNm", "encSvcSeqNo", "evalNo", "rptNo", "fileName", "compCd", "compNm")}
        try:
            r = self.s.get(BASE + "/ajaxa/fileCpnt/reportFileDown.do?" + urllib.parse.urlencode(d), timeout=60,
                           headers={"Referer": BASE + MENU})
        except requests.RequestException as e:
            raise Blocked(f"내려받기 접속 실패: {e}")
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            raise Blocked(f"PDF 가 아님(응답 {r.status_code}, {r.headers.get('Content-Type')}, {len(r.content)}바이트)")
        with open(path, "wb") as f:
            f.write(r.content)
        time.sleep(self.pause)
        return True
