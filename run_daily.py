"""GitHub Actions(또는 로컬)에서 매일 실행: 전 영업일 발행분 -> docs/ 의 JSON.

- 평소: 전 영업일 하루.
- START_DATE / END_DATE 환경변수: 그 기간(밀린 날짜 채우기).
- BACKFILL_DAYS=N: 아직 안 채운 과거 영업일을 최근부터 거꾸로 N일 채운다(BACKFILL_FROM 까지, 기본 2024-11-18).
- 어느 경우든, 평가사 조회 실패가 남은 날짜를 REDO_DAYS(기본 5)일까지 함께 다시 만든다.
- REDO_ONLY=1: 다시 만들 날짜만 처리(PC 보충 실행 pc_fill.py 가 쓴다).
- 시작할 때 NICE에 한 번 접속해 보고, 막혀 있으면 이번 실행에서는 NICE를 묻지 않는다.
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

import agencies
from build import Builder
import make_rows
from make_rows import make
from spc_db import SpcDb
from spc_table import SpcTable

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(BASE, "docs")
HIST = os.path.join(DOCS, "history.json")
DATA = os.path.join(DOCS, "data.json")
WAIT = os.path.join(BASE, "state", "nice_wait.json")   # NICE 확인 대기 {발행일: [SPC]} (커밋한다)
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
        v = v.item()
    # 빈칸이 섞인 열은 pandas 가 정수를 실수로 바꾼다(1012 -> 1012.0). 정수면 정수로 되돌린다
    if isinstance(v, float) and v.is_integer():
        return int(v)
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


def failed_days(hist: dict, nice_ok: bool = True) -> list[dt.date]:
    """다시 만들 날짜(최근 순): 평가사 조회 실패 줄이 남은 날짜 + 최근 7일 안의 '평가 문서 없음' 줄이 있는 날짜.
    nice_ok=False(이번 실행에서 NICE가 막힘)면 NICE 말고 다른 곳(한신평) 실패가 있는 날짜만 고른다."""
    recent = (dt.datetime.now(KST).date() - dt.timedelta(7)).isoformat()

    def need(d, r):
        q = r.get("Q") or ""
        if not nice_ok:
            return "한신평 조회 실패" in q or "kis PDF 조회 실패" in q
        return "조회 실패" in q or (d >= recent and r.get("verdict") == "확인 필요")
    bad = {d for d, rows in hist.items() if any(need(d, r) for r in rows)}
    if nice_ok:      # NICE가 열려 있으면 'NICE 확인 대기' 날짜도 다시 만든다
        bad |= set(load_json(WAIT, {}))
    return [dt.date.fromisoformat(d) for d in sorted(bad, reverse=True)]


def target_days(hist: dict, hol, nice_ok: bool = True) -> list[dt.date]:
    redo = int(os.environ.get("REDO_DAYS", "5") or 0)
    if os.environ.get("REDO_ONLY") == "1":      # PC 보충 실행: 다시 만들 날짜만
        days = failed_days(hist, nice_ok)[:redo]
        # GitHub 밤 실행이 밀려 전 영업일이 아직 없으면 PC가 먼저 만든다
        last = prev_bday(dt.datetime.now(KST).date(), hol)
        if last.isoformat() not in hist and last not in days:
            days.insert(0, last)
        return days
    days = _target_days(hist, hol, nice_ok)
    extra = [d for d in failed_days(hist, nice_ok) if d not in days][:redo]
    return days + extra


def _target_days(hist: dict, hol, nice_ok: bool = True) -> list[dt.date]:
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
    d = prev_bday(today, hol)
    # 평일 아침 여러 번(06:00·06:30·07:00) 돌므로, 이미 문제없이 만든 날짜면 다시 하지 않는다
    if d.isoformat() in hist and d not in failed_days(hist, nice_ok):
        print(f"{d} 은 이미 만들어져 있어 건너뜁니다.", flush=True)
        return []
    return [d]


def main():
    hol = holidays()
    hist = load_json(HIST, {})
    nice_ok = agencies.probe_nice()
    print("NICE 접속:", "정상" if nice_ok else agencies.NICE_OFF, flush=True)
    days = target_days(hist, hol, nice_ok)
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
        wait = load_json(WAIT, {})
        wait.pop(d.isoformat(), None)            # 이 날짜를 다시 만들었으니 예전 대기 목록은 지운다
        if make_rows.NICE_WAIT.get(d.isoformat()):
            wait[d.isoformat()] = make_rows.NICE_WAIT[d.isoformat()]
        save_json(WAIT, dict(sorted(wait.items())))
        # 날짜마다 저장(중간에 끊겨도 앞 날짜는 남는다)
        save_json(HIST, dict(sorted(hist.items())))
        table.save()
        builder = Builder(table)      # 새로 알게 된 SPC·시공사 반영
    if getattr(builder.ev, "_kr_down", ""):
        errors.append(builder.ev._kr_down)
    waiting = load_json(WAIT, {})
    # NICE가 막혔다는 안내는 NICE가 필요한 노란 줄이 실제로 남았을 때만 띄운다
    # (2026-11-23: PC 보충 실행이 다 채운 뒤 돈 GitHub 실행이 할 일 없이 이 문구만 다시 띄움)
    nice_rows = sum(1 for rs in hist.values() for r in rs
                    if any(x.lower().startswith("nice") and "조회 실패" in x for x in (r.get("Q") or "").split("; ")))
    if not nice_ok and nice_rows:
        errors.append(f"{agencies.NICE_OFF}. NICE가 필요한 노란 줄 {nice_rows}개는 PC 보충 실행이나 다음 실행 때 채워집니다.")
    if waiting:
        n = sum(map(len, waiting.values()))
        errors.append(f"NICE 확인 대기 {n}건({len(waiting)}일): 한기평·한신평에 평가 문서가 없어 NICE로만 판정할 수 있는 SPC입니다. "
                      "줄은 만들지 않았고, PC 보충 실행 때 NICE로 확인해 시공사 건이면 줄을 넣습니다.")
    left = [d for d in failed_days(hist) if d.isoformat() not in waiting]   # 대기 날짜는 위에서 따로 알렸다
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
