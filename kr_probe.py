"""GitHub 서버(해외)에서 한기평이 열리는지 확인하는 시험(2026-11-20).
목록 1번 + 최근 7일 평가의견 PDF 전체(5초 간격). 막히면 그 자리에서 멈추고 몇 건째였는지 남긴다. 결과는 실행 기록(로그)에만 남기고 아무것도 커밋하지 않는다."""
import datetime as dt
import os
import tempfile

import requests

import kr


def main():
    try:
        ip = requests.get("https://api.ipify.org", timeout=10).text
    except Exception:  # noqa: BLE001
        ip = "?"
    print("이 서버의 인터넷 주소:", ip, flush=True)
    try:
        k = kr.Kr(pause=5)
        print("한기평 첫 화면: 정상", flush=True)
    except Exception as e:  # noqa: BLE001
        print("한기평 첫 화면: 실패 -", e)
        return
    end = dt.date.today()
    rows = k.listing((end - dt.timedelta(7)).isoformat(), end.isoformat())
    docs = [r for r in rows if r.get("RTNG_OPN_FILE_NM")]
    print(f"목록: {len(rows)}줄 (평가의견 있는 줄 {len(docs)})", flush=True)
    seen, uniq = set(), []
    for r in docs:
        if r["RTNG_OPN_FILE_NM"] not in seen:
            seen.add(r["RTNG_OPN_FILE_NM"])
            uniq.append(r)
    print(f"서로 다른 평가의견 {len(uniq)}개를 5초 간격으로 받습니다", flush=True)
    tmp = tempfile.mkdtemp()
    got = paid = 0
    t0 = dt.datetime.now()
    for n, r in enumerate(uniq, 1):
        try:
            ok = k.download(r, os.path.join(tmp, "x.pdf"))
            got += ok
            paid += not ok
            if n % 10 == 0:
                print(f"  {n}/{len(uniq)} 진행 (받음 {got}, 유료 건너뜀 {paid})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"!! {n}번째에서 실패({(dt.datetime.now() - t0).seconds // 60}분 경과, 받음 {got}): {e}", flush=True)
            return
    print(f"끝: {len(uniq)}개 중 받음 {got}, 유료 건너뜀 {paid}, 걸린 시간 {(dt.datetime.now() - t0).seconds // 60}분",
          flush=True)

if __name__ == "__main__":
    main()
