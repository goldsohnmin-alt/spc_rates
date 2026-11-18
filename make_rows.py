"""발행일(하루 또는 기간)을 받아 장부에 붙여넣을 A~Q 줄을 만든다.

실행:  .venv\\Scripts\\python make_rows.py 2026-10-15 [2026-10-16]
결과:  output/rows_<시작>_<끝>.tsv (A~Q, 머리글 없음, 탭 구분) 와 .csv(판정·노란색 사유 포함). 커밋 안 됨.
"""
from __future__ import annotations

import datetime as dt
import os
import sys

import pandas as pd

import kofia
from build import Builder
from ledger import Ledger
from rates import issues_on, norm_spc
from spc_db import SpcDb

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
COLS = "ABCDEFGHIJKLMNOPQ"


def business_days(a: dt.date, b: dt.date) -> list[dt.date]:
    return [a + dt.timedelta(n) for n in range((b - a).days + 1) if (a + dt.timedelta(n)).weekday() < 5]


def make(days: list[dt.date], builder: Builder | None = None, db: SpcDb | None = None) -> pd.DataFrame:
    builder = builder or Builder(Ledger.load())
    db = db or SpcDb()
    today = dt.date.today()
    trades = kofia.fetch_days(days)
    trades = trades[trades["ab_type"].fillna("") != "일반"]
    out = []
    for d in days:
        iss = issues_on(trades, d)
        todo = [i for _, i in iss.iterrows() if not db.skip(i["spc"], today)]
        print(f"{d}: 발행 {len(iss)}건, 평가사 확인 {len(todo)}건 (나머지는 이미 '아니오'로 분류된 SPC)", flush=True)
        for n, i in enumerate(todo, 1):
            if n % 10 == 0:
                print(f"  {n}/{len(todo)}", flush=True)
            t = trades[(trades["kind"] == i["kind"]) & (trades["trade_date"] == d) & (trades["issue_date"] == d)
                       & (trades["maturity"] == i["maturity"]) & (trades["name"].map(norm_spc) == i["spc"])]
            o = builder.build(i, t)
            db.put(i["spc"], i["name"], o.verdict, o.values.get("J"), o.reason, today)
            if o.verdict == "아니오":
                continue
            rec = {c: o.values.get(c) for c in COLS}
            rec.update(verdict=o.verdict, reason=o.reason, checks=dict(o.check),
                       check="; ".join(f"{k}: {v}" for k, v in o.check.items()))
            out.append(rec)
    db.save()
    return pd.DataFrame(out, columns=list(COLS) + ["verdict", "reason", "checks", "check"])


def to_tsv(df: pd.DataFrame, cols: str = "ABCDEFGHIJKLMNOP") -> str:
    def cell(v):
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return ""
        if isinstance(v, (dt.date, pd.Timestamp)):
            return pd.Timestamp(v).strftime("%Y-%m-%d")
        return str(v).replace("\t", " ").replace("\n", " ")
    return "\n".join("\t".join(cell(r[c]) for c in cols) for _, r in df.iterrows())


if __name__ == "__main__":
    a = dt.date.fromisoformat(sys.argv[1])
    b = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else a
    df = make(business_days(a, b))
    base = os.path.join(OUT, f"rows_{a:%Y%m%d}_{b:%Y%m%d}")
    df.to_csv(base + ".csv", index=False, encoding="utf-8-sig")
    with open(base + ".tsv", "w", encoding="utf-8") as f:
        f.write(to_tsv(df, COLS))
    print(f"{len(df)}줄 (확인 필요 {int((df['verdict'] == '확인 필요').sum())}, 노란색 칸 있는 줄 {int((df['check'] != '').sum())})")
    print("->", base + ".tsv")
