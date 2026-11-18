"""자동 시험: 장부 최근 N줄을 '그 줄 이전 장부'만으로 재현해 실제 값과 비교한다.

화면에는 집계 숫자만 낸다(장부 내용은 출력하지 않는다).
틀린 줄의 행 번호·열은 output/test_ledger_mismatch.txt 에 저장한다(커밋 안 됨).

실행:  .venv\\Scripts\\python test_ledger.py [N]
"""
import os
import sys
from collections import Counter

from ledger import Ledger, norm_name

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")


def run(n: int = 30):
    led = Ledger.load()
    rows = led.rows
    targets = rows[-n:]
    new_spc = 0
    total = Counter()
    ok = Counter()
    k_same_spc_ok = 0  # 비교용: K를 같은 SPC 직전 줄에서 가져왔다면
    mismatches = []
    for r in targets:
        f = led.autofill(r["A"], before_row=r.excel_row)
        if not f.found:
            new_spc += 1
            continue
        for c in "IJKOP":
            total[c] += 1
            if norm_name(f.values[c]) == norm_name(r[c]):
                ok[c] += 1
            else:
                tag = "노란색 표시됨" if c in f.check else "표시 안 됨"
                mismatches.append(f"행 {r.excel_row} 열 {c} (근거 행 {f.source_row}) {tag}")
        src = next(x for x in rows if x.excel_row == f.source_row)
        if norm_name(src["K"]) == norm_name(r["K"]):
            k_same_spc_ok += 1

    repeat = n - new_spc
    print(f"장부 전체 {len(rows)}줄, 시험 대상 최근 {n}줄")
    print(f"  신규 SPC(이전 장부에 없음): {new_spc}줄 / 반복 SPC: {repeat}줄")
    for c, name in zip("IJKOP", ["주관회사", "시공사", "현재등급", "사업지", "신용보강"]):
        if total[c]:
            print(f"  {c} {name}: {ok[c]}/{total[c]} 일치")
    if repeat:
        print(f"  (비교) K를 같은 SPC 직전 줄에서 가져왔다면: {k_same_spc_ok}/{repeat} 일치")
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "test_ledger_mismatch.txt")
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(mismatches))
    hidden = sum(m.endswith("표시 안 됨") for m in mismatches)
    print(f"  불일치 {len(mismatches)}칸 (그중 노란색 표시 없이 틀린 칸 {hidden}) -> {path}")
    return len(mismatches) == 0


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    ok = run(n)
    sys.exit(0 if ok else 1)
