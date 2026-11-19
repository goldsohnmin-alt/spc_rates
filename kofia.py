"""KOFIA 채권정보센터에서 ABSTB(전자단기사채)·ABCP 거래 내역을 하루 단위로 가져온다.

화면(2026-11-18 확인):
- 전자단기사채정보: 단기금융시장 > 전자단기사채 > 전자단기사채정보. 기간 조회, 단위 백만원(기본).
  열: 거래일·AB구분·회사명·기초자산·거래구분·통화·발행일·만기일·금리(%)·거래량·신용등급·잔존만기.
- ABCP거래내역공시: 단기금융시장 > CP정보 > ABCP거래내역공시. 하루 조회, 단위 기본 '억' -> '백만'으로 바꾼다.
  열: 거래일·회사명·기초자산·거래구분·매매성격구분·발행일·만기일·거래대금·금리·통화·신용등급·잔존기간.
- 두 화면 모두 프레임 없이 주소로 바로 열 수 있다. 표는 WebSquare 부품 grdMain 에서 getCellData 로 읽는다.
- 회차·주관회사 열은 없다. 같은 SPC의 여러 회차는 발행일·만기일로 구분한다.
- 2024년 이전 거래도 조회된다. 휴일은 0줄.

조회 결과는 output/kofia/ 에 날짜별 CSV로 저장하고, 있으면 다시 조회하지 않는다(사이트 부담을 줄인다).
"""
from __future__ import annotations

import datetime as dt
import os
import time

import pandas as pd
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE = "https://www.kofiabond.or.kr/websquare/websquare.html?w2xPath=/xml/Com/Common_GnrDsp.xml"
SCREENS = {
    "ABSTB": dict(
        url=BASE + "&divisionId=MBIS01040040010000&serviceId=BIS0100100250&topMenuIndex=4&w2xHome=/xml/&w2xDocumentRoot=",
        date_ids=["startDtDD", "endDtDD"],
        unit_radio=None,  # 기본이 백만원
        cols=dict(val1="trade_date", val2="ab_type", val3="name", val4="asset", val5="trade_type",
                  val6="currency", val7="issue_date", val8="maturity", val9="rate", val13="amount",
                  val11="rating", val12="days_left"),
    ),
    "ABCP": dict(
        url=BASE + "&divisionId=MBIS01040020030000&serviceId=BIS0100100200&topMenuIndex=4&w2xHome=/xml/&w2xDocumentRoot=",
        date_ids=["srchDt"],
        unit_radio="unitGb_input_0",  # 백만
        cols=dict(val1="trade_date", val2="name", val3="asset", val4="trade_type", val5="trade_char",
                  val6="issue_date", val7="maturity", val8="amount", val9="rate", val10="currency",
                  val11="rating", val12="days_left"),
    ),
}
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output", "kofia")
PAUSE = 3.0  # 조회 사이 쉬는 시간(초): 사람이 쓰는 속도

_JS_READ = """
var g = WebSquare.util.getComponentById('grdMain');
var ids = arguments[0], out = [];
for (var i = 0; i < g.getRowCount(); i++) {
  var r = [];
  for (var k = 0; k < ids.length; k++) r.push(g.getCellData(i, ids[k]));
  out.push(r);
}
return out;
"""

_JS_SEARCH = """
var ids = arguments[0], v = arguments[1], radio = arguments[2];
for (var k = 0; k < ids.length; k++) WebSquare.util.getComponentById(ids[k]).setValue(v);
if (radio) { var r = document.getElementById(radio); if (r && !r.checked) r.click(); }
var got = WebSquare.util.getComponentById(ids[0]).getValue();
var btn = Array.from(document.querySelectorAll('img,a,input,button')).find(function (x) {
  return (x.offsetWidth || x.offsetHeight) && /조회/.test((x.alt || x.value || x.innerText || '').trim());
});
if (!btn) return 'nobtn';
window.__spcGridBefore = WebSquare.util.getComponentById('grdMain').getRowCount();
btn.click();
return got;
"""


def make_driver(headless: bool = True):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=1400,1000")
    opts.add_argument("--lang=ko-KR")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    return webdriver.Chrome(options=opts)


def _dismiss_popup(driver):
    driver.execute_script(
        "var b=document.getElementById('popup_ok');"
        "if(!b){b=Array.from(document.querySelectorAll('input')).find(function(i){"
        "return (i.value||'').trim()==='Yes' && i.offsetWidth;});}"
        "if(b && b.offsetWidth){b.click(); return true;} return false;")


def _wait_grid(driver, ymd: str, timeout=30):
    """조회 뒤 표가 다 그려질 때까지 기다린다: 줄 수가 3번 연속 같고, 줄이 있으면 첫 줄 거래일이 맞아야 한다."""
    end = time.time() + timeout
    last, same = None, 0
    time.sleep(2)
    while time.time() < end:
        _dismiss_popup(driver)
        n, first = driver.execute_script(
            "var g=WebSquare.util.getComponentById('grdMain');var n=g.getRowCount();"
            "return [n, n? g.getCellData(0,'val1') : null];")
        ok = (n == 0) or (first == ymd)
        same = same + 1 if (n == last and ok) else 0
        last = n
        if same >= 3:
            return n
        time.sleep(0.8)
    raise TimeoutError(f"KOFIA 표가 준비되지 않음(줄 수 {last})")


def _wait_initial(driver, timeout=30):
    """grdMain 부품이 생기고 줄 수가 3번 연속 같아질 때까지(=처음 기본 조회가 끝남) 기다린다."""
    end = time.time() + timeout
    last, same = None, 0
    time.sleep(2)
    while time.time() < end:
        _dismiss_popup(driver)
        try:
            n = driver.execute_script(
                "try{return WebSquare.util.getComponentById('grdMain').getRowCount();}catch(e){return null;}")
        except Exception:  # noqa: BLE001
            n = None
        same = same + 1 if (n is not None and n == last) else 0
        last = n
        if same >= 3:
            return
        time.sleep(0.8)
    raise TimeoutError("KOFIA 화면이 열리지 않음")


def _save_debug(driver, name):
    """실패하면 화면 캡처를 output/kofia/debug 에 남긴다(추측하지 말고 먼저 읽기 위해)."""
    try:
        p = os.path.join(CACHE_DIR, "debug")
        os.makedirs(p, exist_ok=True)
        driver.save_screenshot(os.path.join(p, name + ".png"))
    except Exception:  # noqa: BLE001
        pass


def _cache_path(kind, d: dt.date):
    return os.path.join(CACHE_DIR, f"{kind}_{d:%Y%m%d}.csv")


def fetch_day(driver, kind: str, d: dt.date) -> pd.DataFrame:
    """그날 거래일 기준 전체 거래(모든 회사)를 DataFrame으로. amount 단위는 백만원, rate 는 %."""
    path = _cache_path(kind, d)
    if os.path.exists(path):
        return pd.read_csv(path, dtype=str).pipe(_types, kind)
    s = SCREENS[kind]
    ymd = d.strftime("%Y%m%d")
    last_err = None
    for attempt in range(3):
        driver.get(s["url"])
        try:
            _wait_initial(driver)  # 화면이 처음 열릴 때 하는 기본 조회가 끝나길 기다린다
            got = driver.execute_script(_JS_SEARCH, s["date_ids"], ymd, s["unit_radio"])
            if got != ymd:
                raise RuntimeError(f"KOFIA {kind} 날짜 입력 실패: {got!r} (원하는 값 {ymd})")
            _wait_grid(driver, ymd)
            n = driver.execute_script("return WebSquare.util.getComponentById('grdMain').getRowCount();")
            if not n:
                # 영업일에 거래가 0줄일 수는 없다: 화면이 덜 불러온 것으로 보고 다시 한다(빈 결과는 저장하지 않는다)
                raise RuntimeError(f"KOFIA {kind} {ymd} 결과가 0줄")
            break
        except (TimeoutError, RuntimeError) as e:
            last_err = e
            _save_debug(driver, f"{kind}_{ymd}_try{attempt + 1}")
            time.sleep(PAUSE * 2)
    else:
        raise last_err
    ids = list(s["cols"])
    rows = driver.execute_script(_JS_READ, ids)
    df = pd.DataFrame(rows, columns=[s["cols"][c] for c in ids])
    if s["unit_radio"]:
        # 단위 라디오가 실제로 백만으로 바뀌었는지 확인
        if not driver.execute_script(f"return document.getElementById('{s['unit_radio']}').checked;"):
            raise RuntimeError("KOFIA ABCP 단위를 백만으로 바꾸지 못함")
    df["kind"] = kind
    if len(df) and (df["trade_date"] != ymd).any():
        raise RuntimeError(f"KOFIA {kind} 결과에 다른 거래일이 섞임")
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    time.sleep(PAUSE)
    return _types(df, kind)


def _types(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    df = df.copy()
    df["name"] = df["name"].astype(str).str.strip()
    for c in ("rate", "amount"):
        df[c] = pd.to_numeric(df[c].astype(str).str.replace(",", ""), errors="coerce")
    for c in ("trade_date", "issue_date", "maturity"):
        df[c] = pd.to_datetime(df[c], format="%Y%m%d", errors="coerce").dt.date
    df["kind"] = kind
    return df


def fetch_days(dates, kinds=("ABSTB", "ABCP"), driver=None) -> pd.DataFrame:
    own = driver is None
    if own:
        need = [(k, d) for d in dates for k in kinds if not os.path.exists(_cache_path(k, d))]
        driver = make_driver() if need else None
    try:
        frames = [fetch_day(driver, k, d) for d in dates for k in kinds]
    finally:
        if own and driver is not None:
            driver.quit()
    frames = [f for f in frames if len(f)]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
