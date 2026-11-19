"""GitHub Actions(또는 로컬)에서 매일 실행: 전 영업일 발행분 -> docs/ 의 JSON.

- 평소: 전 영업일 하루.
- START_DATE / END_DATE 환경변수: 그 기간(밀린 날짜 채우기).
- BACKFILL_DAYS=N: 아직 안 채운 과거 영업일을 최근부터 거꾸로 N일 채운다(BACKFILL_FROM 까지, 기본 2024-11-18).
- 어느 경우든, 평가사 조회 실패가 남은 날짜를 REDO_DAYS(기본 5)일까지 함께 다시 만든다.
- RUN_MINUTES: 이 시간이 지나면 새 날짜를 시작하지 않고 저장한다(Actions 시간 제한 대비).
결과(커밋됨):
  docs/history.json   날짜별 줄 목록(A~Q, 판정, 노란색 사유). 채운 날짜는 줄이 없어도 [] 로 남는다.
  docs/data.json      가장 최근 날짜의 줄 + errors(화면 위 빨간 상자)
  docs/spc_table.json SPC 표(장부 대신)        state/spc_db.csv  SPC 분류 DB
한 날짜가 실패해도 다른 날짜는 계속한다. 실패는 errors 에 문장으로 남긴다.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import traceback

from build import Builder
from make_rows import make
from spc_db import SpcDb
from spc_table import SpcTable

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
HIST = os.path.join(DOCS, "history.json")
DATA = os.path.join(DOCS, "data.json")
KST = dt.timezone(dt.timedelta(hours=9))


def holidays() -> set[dt.date]:
    with open(os.path.join(BASE, "holidays.csv"), encoding="utf-8") as f:
        return {dt.date.fromisoformat(r[0]) for r in csv.reader(f) if r and r[0][:2] == "20"}


def is_bday(d: dt.date, hol) -> bool:
    return d.weekday() < 5 and d not in hol


def prev_bday(d: dt.date, hol) -> dt.date:
    d -= dt.timedelta(1)
    while not is_bday(d, hol):
        d -= dt.timedelta(1)
    return d


def clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()[:10]
    if hasattr(v, "item"):          # numpy 숫자
        return v.item()
    return v


def load_json(path, default):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=0)


def failed_days(hist: dict) -> list[dt.date]:
    """평가사 조회에 실패한 줄이 남은 날짜(최근 순). 다음 실행 때 다시 만든다."""
    # 평가 문서가 없던 줄은 공시가 늦었을 수 있으니 7일 동안은 다시 본다
    recent = (dt.datetime.now(KST).date() - dt.timedelta(7)).isoformat()
    bad = [d for d, rows in hist.items()
           if any("조회 실패" in (r.get("Q") or "") or (d >= recent and r.get("verdict") == "확인 필요") for r in rows)]
    return [dt.date.fromisoformat(d) for d in sorted(bad, reverse=True)]


def target_days(hist: dict, hol) -> list[dt.date]:
    days = _target_days(hist, hol)
    redo = int(os.environ.get("REDO_DAYS", "5") or 0)
    extra = [d for d in failed_days(hist) if d not in days][:redo]
    return days + extra


def _target_days(hist: dict, hol) -> list[dt.date]:
    today = dt.datetime.now(KST).date()
    s, e = os.environ.get("START_DATE", "").strip(), os.environ.get("END_DATE", "").strip()
    if s:
        a = dt.date.fromisoformat(s)
        b = dt.date.fromisoformat(e) if e else prev_bday(today, hol)
        return [a + dt.timedelta(n) for n in range((b - a).days + 1) if is_bday(a + dt.timedelta(n), hol)]
    n = int(os.environ.get("BACKFILL_DAYS", "0") or 0)
    if n:
        stop = dt.date.fromisoformat(os.environ.get("BACKFILL_FROM", "") or "2024-11-18")
        d, out = prev_bday(today, hol), []
        while d >= stop and len(out) < n:
            if d.isoformat() not in hist:
                out.append(d)
            d = prev_bday(d, hol)
        return out
    return [prev_bday(today, hol)]


def main():
    hol = holidays()
    hist = load_json(HIST, {})
    days = target_days(hist, hol)
    table, db = SpcTable.load(), SpcDb()
    builder = Builder(table)
    errors = []
    print("대상 날짜:", ", ".join(map(str, days)) or "없음", flush=True)
    # GitHub Actions 시간 제한에 걸리면 결과가 커밋되지 않으므로, RUN_MINUTES 가 지나면 새 날짜를 시작하지 않는다.
    limit = float(os.environ.get("RUN_MINUTES", "0") or 0)
    started = dt.datetime.now()
    for d in days:
        if limit and (dt.datetime.now() - started).total_seconds() > limit * 60:
            left = [x.isoformat() for x in days if x >= d]
            errors.append(f"시간 제한으로 {len(left)}일 남김({left[0]}~{left[-1]}). 남은 기간으로 다시 실행해 주세요.")
            break
        try:
            df = make([d], builder=builder, db=db)
        except Exception as e:
            traceback.print_exc()
            errors.append(f"{d} 처리 실패: {type(e).__name__}: {e}")
            continue
        rows = []
        for r in df.to_dict("records"):
            rec = {c: clean(r[c]) for c in "ABCDEFGHIJKLMNOPQ"}
            rec.update(verdict=r["verdict"], checks=r["checks"] or {})
            rows.append(rec)
            if r["verdict"] == "예":
                table.update(rec)
        hist[d.isoformat()] = rows
        # 날짜마다 저장(중간에 끊겨도 앞 날짜는 남는다)
        save_json(HIST, dict(sorted(hist.items())))
        table.save()
        builder = Builder(table)      # 새로 알게 된 SPC·시공사 반영
    left = failed_days(hist)
    if left:
        errors.append(f"평가사를 다시 조회할 날짜 {len(left)}일(조회 실패 또는 최근 평가 문서 없음). "
                      f"다음 실행 때 하루 최대 {os.environ.get('REDO_DAYS', '5')}일씩 자동으로 다시 조회합니다.")
    # PDF 읽기 실패는 다시 해도 같으므로(코드 수정 필요) 다시 돌리지 않고 알리기만 한다
    unread = sorted({d for d in days if any("읽기 실패" in (r.get("Q") or "") for r in hist.get(d.isoformat(), []))})
    if unread:
        errors.append(f"평가사 PDF 읽기 실패가 있는 날짜: {', '.join(map(str, unread))} (프로그램 수정 필요)")
    latest = max(hist) if hist else None
    save_json(DATA, {"updated": dt.datetime.now(KST).isoformat(timespec="minutes"),
                     "latest_date": latest, "rows": hist.get(latest, []) if latest else [],
                     "errors": errors})
    print(f"끝. 오류 {len(errors)}건", flush=True)


if __name__ == "__main__":
    main()
