"""PC 보충 실행: GitHub 실행에서 평가사(주로 NICE) 조회에 실패한 날짜를 이 PC에서 다시 만들어 올린다.

왜: 2026-11-19부터 NICE가 GitHub 서버 접속을 막았다. 이 PC(한국 인터넷)에서는 열린다.
충돌 방지:
  1) GitHub 실행이 돌고 있으면 끝날 때까지 기다린다(최대 WAIT_MAX 분).
  2) 시작 전·올리기 직전에 최신 결과를 받는다(git pull --rebase --autostash).
  3) '조회 실패'·'최근 평가 문서 없음' 줄이 있는 날짜만 다시 만든다(다른 날짜는 그대로).
  4) docs/·state/ 만 커밋한다(코드는 건드리지 않는다).
실행:  .venv\\Scripts\\python pc_fill.py        (작업 스케줄러용: run_pc_fill.bat)
기록:  output\\pc_fill.log (bat 로 실행할 때)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = "goldsohnmin-alt/spc_rates"
WAIT_MAX = 90      # 분
POLL = 120         # 초


def log(*a):
    print(dt.datetime.now().strftime("%Y-%m-%d %H:%M"), *a, flush=True)


def git(*args, check=True) -> subprocess.CompletedProcess:
    r = subprocess.run(["git", *args], cwd=BASE, capture_output=True, text=True, encoding="utf-8")
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)} 실패: {r.stderr.strip() or r.stdout.strip()}")
    return r


def actions_running() -> bool:
    """이 저장소의 GitHub 실행 중 돌고 있거나 대기 중인 것이 있는지(공개 API, 로그인 불필요)."""
    url = f"https://api.github.com/repos/{REPO}/actions/runs?per_page=10"
    req = urllib.request.Request(url, headers={"User-Agent": "spc-rates-pc-fill", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        runs = json.load(r)["workflow_runs"]
    return any(x["status"] in ("in_progress", "queued", "waiting") and x["name"].startswith("spc-rates")
               for x in runs)


def wait_for_actions() -> bool:
    waited = 0
    while True:
        try:
            if not actions_running():
                return True
        except Exception as e:      # 확인을 못 하면 안전하게 기다린다
            log("GitHub 실행 상태 확인 실패:", e)
        if waited >= WAIT_MAX * 60:
            return False
        log(f"GitHub 실행이 돌고 있어 기다립니다 ({waited // 60}분)")
        time.sleep(POLL)
        waited += POLL


def main() -> int:
    log("PC 보충 실행 시작")
    if not wait_for_actions():
        log(f"GitHub 실행이 {WAIT_MAX}분 넘게 끝나지 않아 이번에는 건너뜁니다.")
        return 1
    git("pull", "--rebase", "--autostash", "-q")

    os.environ.update(REDO_ONLY="1", REDO_DAYS=os.environ.get("REDO_DAYS", "20"), RUN_MINUTES="60")
    sys.path.insert(0, BASE)
    import run_daily
    run_daily.main()

    if not git("status", "--porcelain", "--", "docs", "state").stdout.strip():
        log("바뀐 결과가 없습니다.")
        return 0
    git("add", "--", "docs", "state")
    git("commit", "-q", "-m", f"PC fill {dt.date.today().isoformat()}")
    if not wait_for_actions():
        log("올리기 직전 GitHub 실행이 끝나지 않아, 커밋만 해 두었습니다. 다음 실행 때 함께 올라갑니다.")
        return 1
    git("pull", "--rebase", "--autostash", "-q")
    git("push", "-q")
    log("올렸습니다.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("실패:", e)
        # 끝나지 않은 rebase 가 남지 않게 정리
        if os.path.isdir(os.path.join(BASE, ".git", "rebase-merge")) or os.path.isdir(os.path.join(BASE, ".git", "rebase-apply")):
            git("rebase", "--abort", check=False)
        sys.exit(1)
