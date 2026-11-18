"""장부의 최근 발행일들을 도구로 다시 만들어 장부와 칸별로 비교한다(장부 내용은 화면에 집계만).

1) 장부 줄: KOFIA 발행 건을 찾아 build() -> 판정이 '예'인지, A~P 칸이 장부와 같은지.
   반복 SPC는 그 줄보다 앞선 장부 줄만 쓴다(답 보고 맞히기 방지).
2) 장부에 없는 발행(KOFIA): 판정 결과와 '예'로 나온 건 목록 -> 장부 누락인지 오판인지 사람이 본다.
   장부에 없는 SPC는 표본만(기본 40개) 본다. 평가사 사이트 부담 때문.
줄별 결과: output/test_build_ledger.csv, output/test_build_others.csv (커밋 안 됨)
실행:  .venv\\Scripts\\python test_build.py [발행일 수(기본 20)] [표본 수(기본 40)]
"""
import datetime as dt
import os
import random
import sys
from collections import Counter

import pandas as pd

import kofia
from build import Builder, bare, ju
from compare_rates import as_date
from ledger import Ledger
from rates import issues_on, norm_spc

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def same(col, a, b) -> bool:
    if a is None or b is None or (isinstance(a, float) and pd.isna(a)):
        return (a is None or (isinstance(a, float) and pd.isna(a))) and b is None
    if col in "MN":
        return abs(float(a) - float(b)) < 1e-9
    if col == "E":
        try:
            return abs(float(a) - float(b)) < 0.006
        except (TypeError, ValueError):
            return False
    if col in "FG":
        return as_date(a) == as_date(b)
    if col == "H":
        return int(a) == int(b)
    if col in "AIJP":
        return bare(ju(a)) == bare(ju(b))
    return " ".join(str(a).split()) == " ".join(str(b).split())


def main(n_days=20, n_sample=40):
    led = Ledger.load()
    b = Builder(led)
    rows = [r for r in led.rows if as_date(r["F"])]
    days = sorted({as_date(r["F"]) for r in rows})[-n_days:]
    trades = kofia.fetch_days(days)
    trades = trades[trades["ab_type"].fillna("") != "일반"]

    def trades_for(i):
        return trades[(trades["kind"] == i["kind"]) & (trades["trade_date"] == i["issue_date"])
                      & (trades["issue_date"] == i["issue_date"]) & (trades["maturity"] == i["maturity"])
                      & (trades["name"].map(norm_spc) == i["spc"])]

    issues = pd.concat([issues_on(trades, d) for d in days], ignore_index=True)
    known = {norm_spc(r["A"]) for r in led.rows}
    used, out1, c = set(), [], Counter()
    for r in rows:
        d = as_date(r["F"])
        if d not in days:
            continue
        kind = (r["B"] or "").strip()
        m = issues[(issues["kind"] == kind) & (issues["issue_date"] == d) & (issues["spc"] == norm_spc(r["A"]))
                   & (issues["maturity"] == as_date(r["G"]))]
        c["ledger_rows"] += 1
        if not len(m):
            c["kofia_not_found"] += 1
            continue
        i = m.iloc[0]
        used.add(m.index[0])
        o = b.build(i, trades_for(i), before_row=r.excel_row)
        c["verdict:" + o.verdict] += 1
        rec = dict(row=r.excel_row, verdict=o.verdict, reason=o.reason)
        for col in "ABCDEFGHIJKMNOP":
            if o.verdict == "아니오":
                break
            ok = same(col, o.values.get(col), r[col])
            c[f"{col}:ok"] += ok
            c[f"{col}:n"] += 1
            if not ok:
                c[f"{col}:flagged" if col in o.check else f"{col}:unflagged"] += 1
                rec[f"{col}_tool"], rec[f"{col}_ledger"] = o.values.get(col), r[col]
            if col in o.check:
                rec[f"{col}_check"] = o.check[col]
        rec["Q"] = o.values.get("Q")
        out1.append(rec)
    pd.DataFrame(out1).to_csv(os.path.join(OUT, "test_build_ledger.csv"), index=False, encoding="utf-8-sig")

    # 장부에 없는 발행
    rest = issues.drop(index=list(used))
    rest_known = rest[rest["spc"].isin(known)]
    unknown_spcs = sorted(set(rest["spc"]) - known)
    random.seed(1)
    sample = set(random.sample(unknown_spcs, min(n_sample, len(unknown_spcs))))
    rest_unknown = rest[rest["spc"].isin(sample)].drop_duplicates("spc")
    out2 = []
    for grp, df in (("장부SPC_누락", rest_known), ("신규SPC_표본", rest_unknown)):
        for _, i in df.iterrows():
            o = b.build(i, trades_for(i))
            c[f"{grp}:{o.verdict}"] += 1
            out2.append(dict(group=grp, spc=i["name"], kind=i["kind"], issue=i["issue_date"],
                             maturity=i["maturity"], verdict=o.verdict, reason=o.reason,
                             **{k: o.values.get(k) for k in "CDEIJKOP"},
                             check="; ".join(f"{k}:{v}" for k, v in o.check.items())))
    pd.DataFrame(out2).to_csv(os.path.join(OUT, "test_build_others.csv"), index=False, encoding="utf-8-sig")

    print(f"발행일 {len(days)}일 ({days[0]} ~ {days[-1]})")
    print(f"장부 줄 {c['ledger_rows']} (KOFIA에서 못 찾음 {c['kofia_not_found']})")
    print("  판정:", {k[8:]: v for k, v in c.items() if k.startswith("verdict:")})
    print("  칸별 일치(일치/비교, 불일치 중 노란색 표시/미표시):")
    for col in "ABCDEFGHIJKMNOP":
        print(f"    {col}: {c[col+':ok']}/{c[col+':n']}  (표시 {c[col+':flagged']}, 미표시 {c[col+':unflagged']})")
    for grp in ("장부SPC_누락", "신규SPC_표본"):
        print(f"  {grp}:", {k.split(':')[1]: v for k, v in c.items() if k.startswith(grp)})
    print("  ->", os.path.join(OUT, "test_build_ledger.csv"), "/ test_build_others.csv")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(int(a[0]) if a else 20, int(a[1]) if len(a) > 1 else 40)
