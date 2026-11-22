"""한기평 비교 시험 (2026-11-20 사용자 요청).

NICE 없이(한기평 + 한신평) 기간을 다시 만들어, 지금 docs/history.json(PC에서 NICE까지 써서 만든 정답)과 비교한다.
- SPC 분류 DB(state/spc_db.csv)를 비우고 시작해 모든 SPC를 새로 판정하게 한다.
- 결과는 커밋하지 않는다(워크플로 권한이 읽기 전용). 비교표는 A~P 열 값만 보여 준다.
환경변수: START_DATE, END_DATE (기본 2026-11-09 ~ 2026-11-19)
"""
from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("START_DATE", "2026-11-09")
os.environ.setdefault("END_DATE", "2026-11-19")
os.environ["USE_KR"] = "1"
os.environ["NICE"] = "off"
os.environ["REDO_DAYS"] = "0"
os.environ.setdefault("RUN_MINUTES", "300")

BASE = os.path.dirname(os.path.abspath(__file__))
HIST = os.path.join(BASE, "docs", "history.json")
DB = os.path.join(BASE, "state", "spc_db.csv")
COLS = "BCDEFGHIJKOP"     # 비교할 열(A는 줄 찾는 열쇠, L~N 금리는 KOFIA라 평가사와 무관)


def same(x, y) -> bool:
    """150 과 150.0 은 같은 값으로 본다."""
    try:
        return float(x) == float(y)
    except (TypeError, ValueError):
        return str(x or "") == str(y or "")


def key(r):
    return (r["A"], r["F"], r["G"])


def main():
    with open(HIST, encoding="utf-8") as f:
        answer = json.load(f)
    if os.path.exists(DB):
        os.remove(DB)
    import run_daily          # 환경변수를 정한 뒤에 불러온다(evidence.USE_KR)
    run_daily.main()
    with open(HIST, encoding="utf-8") as f:
        test = json.load(f)

    a, b = os.environ["START_DATE"], os.environ["END_DATE"]
    days = sorted(d for d in answer if a <= d <= b)
    out = [f"# 한기평 비교 시험 {a} ~ {b} (NICE 없이 한기평+한신평)", ""]
    tot = dict(same=0, diff=0, missing=0, extra=0, yellow_ans=0, yellow_test=0)
    lines = []
    for d in days:
        A = {key(r): r for r in answer.get(d, [])}
        T = {key(r): r for r in test.get(d, [])}
        tot["yellow_ans"] += sum(bool(r.get("checks")) for r in A.values())
        tot["yellow_test"] += sum(bool(r.get("checks")) for r in T.values())
        for k in sorted(A.keys() | T.keys()):
            if k not in T:
                tot["missing"] += 1
                lines.append(f"- {d} **빠짐**: {k[0]} (정답에는 있음, 시험 판정: 줄 없음)")
            elif k not in A:
                tot["extra"] += 1
                lines.append(f"- {d} **더 생김**: {k[0]} (시험 판정 {T[k].get('verdict')})")
            else:
                bad = [c for c in COLS if not same(A[k].get(c), T[k].get(c))]
                if bad:
                    tot["diff"] += 1
                    lines.append(f"- {d} **값 다름** {k[0]}:")
                    lines += [f"  - {c}: 정답 `{A[k].get(c)}` / 시험 `{T[k].get(c)}`" for c in bad]
                else:
                    tot["same"] += 1
                if T[k].get("checks") and not A[k].get("checks"):
                    lines.append(f"- {d} 노란색 새로 생김 {k[0]}: {', '.join(T[k]['checks'])} 열")
    out.append(f"- 똑같음 {tot['same']}줄, 값 다름 {tot['diff']}줄, 빠짐 {tot['missing']}줄, 더 생김 {tot['extra']}줄")
    out.append(f"- 노란 줄: 정답 {tot['yellow_ans']}줄 / 시험 {tot['yellow_test']}줄")
    import make_rows
    wait = sum(len(v) for d, v in make_rows.NICE_WAIT.items() if a <= d <= b)
    out.append(f"- NICE 확인 대기(줄 안 만듦, PC가 나중에 확인): {wait}건")
    out.append("")
    out += lines or ["차이 없음"]
    text = "\n".join(out)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    # 원래대로 되돌린다(로컬에서 돌렸을 때 정답 파일이 바뀌지 않게)
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(answer, f, ensure_ascii=False, indent=0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
