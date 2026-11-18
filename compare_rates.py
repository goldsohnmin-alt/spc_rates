"""장부의 최근 발행일들에 대해 KOFIA 거래로 M·N(매출금리 하단·상단)을 다시 계산해 장부와 비교한다.

화면에는 집계만 낸다. 줄별 결과는 output/compare_rates.csv (커밋 안 됨).
실행:  .venv\\Scripts\\python compare_rates.py [발행일 수(기본 20)]
"""
import datetime as dt
import os
import sys
from collections import Counter

import pandas as pd

import kofia
from ledger import Ledger
from rates import issues_on, norm_spc, rate_range

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "compare_rates.csv")


def as_date(v):
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return None


def pct(v):
    """장부 소수(0.0435) -> % (4.35). 숫자가 아니면 None."""
    try:
        return round(float(v) * 100, 4)
    except (TypeError, ValueError):
        return None


def run(n_days=20, min_share=None, inclusive=None):
    led = Ledger.load()
    rows = [r for r in led.rows if as_date(r["F"])]
    days = sorted({as_date(r["F"]) for r in rows})[-n_days:]
    trades = kofia.fetch_days(days)
    kw = {}
    if min_share is not None:
        kw["min_share"] = min_share
    if inclusive is not None:
        kw["inclusive"] = inclusive

    out, c = [], Counter()
    for r in rows:
        d = as_date(r["F"])
        if d not in days:
            continue
        kind = (r["B"] or "").strip()
        c["ledger_rows"] += 1
        t = trades[(trades["kind"] == kind) & (trades["trade_date"] == d) & (trades["issue_date"] == d)
                   & (trades["name"].map(norm_spc) == norm_spc(r["A"]))
                   & (trades["maturity"] == as_date(r["G"]))]
        rec = dict(row=r.excel_row, kind=kind, date=d, found=len(t) > 0,
                   M=pct(r["M"]), N=pct(r["N"]), E=r["E"])
        if not len(t):
            c["not_found"] += 1
            out.append(rec)
            continue
        res = rate_range(t, **kw)
        rec.update(low=res.low, high=res.high, basis=res.basis, notes="; ".join(res.notes),
                   sale_amt=t.loc[t.trade_type == "매출", "amount"].sum(),
                   buy_amt=t.loc[t.trade_type == "직접매입(할인)", "amount"].sum())
        if rec["M"] is None and rec["N"] is None:
            c["ledger_blank"] += 1
        else:
            # 장부에 N이 비어 있으면(옛 형식) M 하나만 비교
            okM = rec["M"] is not None and res.low is not None and abs(rec["M"] - res.low) < 1e-6
            okN = (rec["N"] is None) or (res.high is not None and abs(rec["N"] - res.high) < 1e-6)
            rec["match"] = okM and okN
            c["compared"] += 1
            c["match"] += rec["match"]
        for note in res.notes:
            c["note:" + note] += 1
        out.append(rec)

    # 장부에 없는데 같은 날 발행된, 장부에 이미 있는 SPC(=시공사 건으로 추정되는데 빠진 건?)
    known = {norm_spc(r["A"]) for r in led.rows}
    for d in days:
        iss = issues_on(trades, d)
        led_keys = {(norm_spc(r["A"]), as_date(r["G"])) for r in rows if as_date(r["F"]) == d}
        for _, i in iss.iterrows():
            if i["spc"] in known and (i["spc"], i["maturity"]) not in led_keys:
                c["kofia_known_spc_not_in_ledger"] += 1

    df = pd.DataFrame(out)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print(f"발행일 {len(days)}일 ({days[0]} ~ {days[-1]}), 규칙: {kw or '기본(10% 이상)'}")
    for k in ["ledger_rows", "not_found", "ledger_blank", "compared", "match", "kofia_known_spc_not_in_ledger"]:
        print(f"  {k}: {c[k]}")
    for k, v in c.items():
        if k.startswith("note:"):
            print(f"  {k}: {v}")
    print(f"  -> {OUT}")
    return df


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
