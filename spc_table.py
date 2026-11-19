"""공개용 SPC 표 (docs/spc_table.json).

사용자 결정(2026-11-18): 장부 중 'SPC 표'만 공개한다. SPC 이름 -> 주관회사(I)·시공사(J)·등급(K)·사업지(O)·신용보강(P).
- 처음에는 장부(로컬 엑셀)에서 SPC마다 가장 최근 줄로 만든다:  python spc_table.py export
- 그 뒤로는 매일 실행 결과('예' 판정 줄)로 갱신한다. GitHub Actions에는 장부가 없으므로 이 표가 장부를 대신한다.
- 담는 값: I·J·K·O·P + 마지막 발행일(date). 도구가 만든 줄은 회차(C)도(다음 차환 회차 추정용).
  (사용자 2026-11-18: 장부 A~P 범위 안의 값이면 공개해도 된다. 비고 등 P 밖은 넣지 않는다.)

Builder 가 장부(Ledger) 대신 쓸 수 있도록 같은 모양(rows, autofill)을 갖춘다.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys

from ledger import Fill

BASE = os.path.dirname(os.path.abspath(__file__))
PATH = os.path.join(BASE, "docs", "spc_table.json")
FIELDS = ["I", "J", "K", "O", "P"]
SINCE = "2023-01-01"


class SpcTable:
    def __init__(self, data: dict):
        self.data = data                       # {SPC 이름(장부 표기): {I,J,K,O,P,C,date}}
        self.rows = [dict(A=k, **v) for k, v in data.items()]

    @classmethod
    def load(cls, path: str = PATH) -> "SpcTable":
        if not os.path.exists(path):
            return cls({})
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f)["spc"])

    def save(self, path: str = PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        out = {"updated": dt.date.today().isoformat(), "count": len(self.data),
               "spc": dict(sorted(self.data.items()))}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=0)

    def autofill(self, name, before_row=None) -> Fill:
        r = self.data.get(name)
        if not r:
            return Fill(found=False)
        f = Fill(found=True)
        f.values = {c: r.get(c) for c in FIELDS}
        f.values["C"] = r.get("C")
        f.values["date"] = r.get("date")   # 이 정보가 나온 발행일(과거 날짜를 채울 때 '직전 회차'인지 확인용)
        f.values["D"] = None          # 기초자산은 평가사 문서에서 정한다
        return f

    def update(self, values: dict):
        """도구가 만든 '예' 줄 하나로 그 SPC의 정보를 최신으로 바꾼다(발행일이 더 늦을 때만)."""
        name, date = values["A"], str(values["F"])
        old = self.data.get(name)
        if old and old.get("date", "") > date:
            return
        self.data[name] = {c: values.get(c) for c in FIELDS} | {"C": values.get("C"), "date": date}
        self.rows = [dict(A=k, **v) for k, v in self.data.items()]


def export_from_ledger() -> SpcTable:
    from ledger import Ledger
    data = {}
    for r in Ledger.load().rows:          # 장부 순서대로 덮어쓰면 SPC마다 마지막 줄이 남는다
        f = r["F"]
        if not r["A"] or not isinstance(f, dt.datetime):
            continue
        data[r["A"]] = {c: r[c] for c in FIELDS} | {"date": f.date().isoformat()}
    # 공개 범위를 줄인다: 마지막 발행이 SINCE 이후인 SPC만(과거 2년 채우기에 필요한 범위 + 여유).
    return SpcTable({k: v for k, v in data.items() if v["date"] >= SINCE})


if __name__ == "__main__":
    if sys.argv[1:] == ["export"]:
        t = export_from_ledger()
        t.save()
        print(f"SPC {len(t.data)}개 -> {PATH}")
