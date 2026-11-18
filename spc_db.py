"""SPC 분류 DB (CLAUDE.md 'SPC 분류 DB').

SPC(비교용 이름) -> 판정(예/아니오/확인 필요), 시공사, 근거, 확인일.
- '아니오'는 RECHECK_DAYS 동안 다시 평가사를 조회하지 않는다(negative list). 차환 때 신용공여자가 바뀔 수 있어 기한을 둔다.
- '예'는 매번 이번 회차 PDF를 읽으므로(회차·발행액·주관회사) 판정만 기록한다.
- '확인 필요'는 다음 실행 때 다시 조회한다.
"""
from __future__ import annotations

import datetime as dt
import os

import pandas as pd

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "spc_db.csv")  # 커밋한다(다음 실행이 재사용)
COLS = ["spc", "name", "verdict", "builder", "reason", "checked"]
RECHECK_DAYS = 90


class SpcDb:
    def __init__(self, path: str = PATH):
        self.path = path
        self.rows: dict[str, dict] = {}
        if os.path.exists(path):
            for r in pd.read_csv(path, dtype=str, keep_default_na=False).to_dict("records"):
                self.rows[r["spc"]] = r

    def skip(self, spc: str, today: dt.date) -> dict | None:
        """다시 볼 필요 없는 '아니오'면 그 기록을 돌려준다."""
        r = self.rows.get(spc)
        if r and r["verdict"] == "아니오":
            if (today - dt.date.fromisoformat(r["checked"])).days < RECHECK_DAYS:
                return r
        return None

    def put(self, spc: str, name: str, verdict: str, builder, reason: str, today: dt.date):
        self.rows[spc] = dict(spc=spc, name=name, verdict=verdict, builder=builder or "",
                              reason=reason, checked=today.isoformat())

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        df = pd.DataFrame(list(self.rows.values()), columns=COLS).sort_values("spc")
        df.to_csv(self.path, index=False, encoding="utf-8-sig")
