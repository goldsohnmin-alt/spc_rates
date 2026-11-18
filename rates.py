"""금리 규칙(CLAUDE.md '금리 규칙' 참고).

한 발행 건(SPC·종류·발행일·만기일)의 발행일 당일 거래 중
- 매출 거래가 있으면 매출만, 없으면 직접매입 거래만(비고 '매입 기준') 쓴다.
- 그 거래 구분의 거래량 합계 대비 MIN_SHARE 이상인 금리만 '의미 있는 금리'.
  하단 = 그중 최저, 상단 = 그중 최고.
- 어떤 금리도 MIN_SHARE 에 못 미치면 전체 최저/최고(비고 '균등 분포: 전체 최저/최고').
- 둘 다 없으면 비우고 '확인 필요'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

MIN_SHARE = 0.10
INCLUSIVE = True  # True: 10% 이상, False: 10% 초과 (장부와 맞춰 보며 확정)
BUY_TYPES = ("직접매입(할인)",)


def norm_spc(name) -> str:
    """KOFIA '(주)'와 장부 '㈜' 차이, 공백을 없앤 비교용 이름."""
    s = str(name or "").replace("㈜", "(주)")
    return re.sub(r"\s+", "", s)


@dataclass
class RateResult:
    low: float | None      # % 단위 (장부에 넣을 땐 /100)
    high: float | None
    basis: str | None      # '매출' / '매입'
    notes: list
    total: float           # 분모(백만원)


def rate_range(trades: pd.DataFrame, min_share=MIN_SHARE, inclusive=INCLUSIVE) -> RateResult:
    t = trades.dropna(subset=["rate", "amount"])
    notes = []
    sale = t[t["trade_type"] == "매출"]
    if len(sale):
        use, basis = sale, "매출"
    else:
        use, basis = t[t["trade_type"].isin(BUY_TYPES)], "매입"
        if len(use):
            notes.append("매입 기준")
    if not len(use):
        return RateResult(None, None, None, ["거래 없음: 확인 필요"], 0.0)
    by_rate = use.groupby("rate")["amount"].sum()
    total = float(by_rate.sum())
    share = by_rate / total if total else by_rate * 0
    sig = share[share >= min_share] if inclusive else share[share > min_share]
    if len(sig):
        return RateResult(float(sig.index.min()), float(sig.index.max()), basis, notes, total)
    notes.append("균등 분포: 전체 최저/최고")
    return RateResult(float(by_rate.index.min()), float(by_rate.index.max()), basis, notes, total)


def issues_on(trades: pd.DataFrame, day) -> pd.DataFrame:
    """그날 발행된 건 목록: 발행일=거래일=day 인 거래를 (종류, SPC, 만기일)로 묶는다."""
    t = trades[(trades["issue_date"] == day) & (trades["trade_date"] == day)].copy()
    if not len(t):
        return pd.DataFrame(columns=["kind", "spc", "name", "issue_date", "maturity", "asset"])
    t["spc"] = t["name"].map(norm_spc)
    g = t.groupby(["kind", "spc", "issue_date", "maturity"], as_index=False).agg(
        name=("name", "first"), asset=("asset", "first"))
    return g
