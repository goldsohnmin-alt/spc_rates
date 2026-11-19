"""발행일 하루치 KOFIA 발행 목록 -> 시공사 신용공여 판정 -> 장부 A~Q 줄.

판정(CLAUDE.md 'SPC 분류 DB'·'대상 선별'):
  예       : 평가사 PDF의 등급 근거(NICE 평가근거 / 한신평 주요 평가요소)에 시공사 이름이 나온다.
  아니오   : 등급 근거가 있는데 시공사가 없다(은행 정기예금·매입보장 등), 또는 카드대금 건.
  확인 필요: 두 평가사 모두에서 등급 근거를 못 찾았다.
채우기(CLAUDE.md '열별 채우는 방법'):
  장부에 있는 SPC이고 시공사가 같으면 D·O·P는 장부의 직전 줄 표현을 그대로 쓴다(사용자 문체 유지).
  C·E·I는 회차마다 바뀌므로 이번 회차 PDF에서 읽는다. K는 KOFIA의 SPC 등급((sf) 제거).
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

import pandas as pd

from evidence import Evidence
from rates import norm_spc, rate_range

# 평가사 표기 -> 장부 표기
BUILDER_ALIAS = {
    "에이치디씨현대산업개발": "아이파크현대산업개발㈜", "HDC현대산업개발": "아이파크현대산업개발㈜",
    "GS건설": "지에스건설㈜", "DL이앤씨": "디엘이앤씨㈜", "DL건설": "디엘건설㈜",
    "SK에코플랜트": "에스케이에코플랜트㈜", "HL디앤아이한라": "에이치엘디앤아이한라㈜",
    "KCC건설": "㈜케이씨씨건설", "IS동서": "아이에스동서㈜", "포스코건설": "㈜포스코이앤씨",
}
ARRANGER_ALIAS = {
    "KB증권": "케이비증권㈜", "비엔케이투자증권": "㈜BNK투자증권", "BNK투자증권": "㈜BNK투자증권",
    "SK증권": "에스케이증권㈜", "IBK투자증권": "아이비케이투자증권㈜", "NH투자증권": "엔에이치투자증권㈜",
    "DB금융투자": "DB증권㈜", "iM증권": "㈜아이엠증권",
}
EXCLUDE_WORDS = ("카드",)   # 카드대금·카드이용대금·카드매출채권 등(사용자: 카드 관련 건 제외)


def ju(s) -> str:
    """'(주)' -> '㈜', 공백 정리."""
    return re.sub(r"\s+", " ", str(s or "").replace("(주)", "㈜")).strip()


def bare(s) -> str:
    return re.sub(r"\s+|㈜|\(주\)|\(유\)|주식회사", "", str(s or ""))


def days_between(a: dt.date, b: dt.date) -> int:
    return (b - a).days


@dataclass
class Out:
    values: dict                       # 'A'..'Q'
    check: dict = field(default_factory=dict)   # 열 -> 사유(노란색)
    verdict: str = ""                  # 예 / 아니오 / 확인 필요
    reason: str = ""


class Builder:
    def __init__(self, ledger, evidence: Evidence | None = None):
        self.led = ledger
        self.ev = evidence or Evidence()
        self.builders = {bare(r["J"]): ju(r["J"]) for r in ledger.rows if r["J"]}
        for k, v in BUILDER_ALIAS.items():
            self.builders[bare(k)] = v
        self.arrangers = {bare(r["I"]): r["I"] for r in ledger.rows if r["I"] and "," not in r["I"]}
        for k, v in ARRANGER_ALIAS.items():
            self.arrangers[bare(k)] = v
        self.led_names = {norm_spc(r["A"]): r["A"] for r in ledger.rows}

    # ---- 시공사 찾기 ------------------------------------------------------
    def find_builder(self, text: str, extra: list[str] = ()) -> str | None:
        """등급 근거 문장에서 시공사 이름을 찾는다. ㈜가 붙은 전체 이름만 인정
        ('한화임팩트㈜'에서 '㈜한화'를 잡지 않도록)."""
        t = ju(text)
        names = dict(self.builders)
        for e in extra:
            if e:
                names.setdefault(bare(e), ju(e))
        hits = []
        for b, form in names.items():
            # '㈜이름' 뒤에는 조사·구두점만 올 수 있다('㈜한화의' O, '㈜한화건설' X)
            if len(b) >= 2 and re.search(rf"㈜{re.escape(b)}(?=$|[^가-힣A-Za-z0-9]|(?:의|등|이|가|와|과|은|는)(?![가-힣]))"
                                         rf"|(?<![가-힣A-Za-z0-9]){re.escape(b)}㈜", t):
                hits.append((t.find(b), form))
        return min(hits)[1] if hits else None

    def _nice_says_no(self, res: dict) -> bool:
        """NICE 문서만으로 '아니오'가 분명한가(그러면 한신평은 묻지 않는다).
        NICE 조회에 실패했거나 근거 문서가 없거나 시공사가 보이면 False(한신평도 본다)."""
        if res["errors"]:
            return False
        d = res["nice"]["match"] or res["nice"]["latest"]
        if not d or not d.get("factors"):
            return False
        text = " / ".join(d["factors"])
        if any(w in text for w in EXCLUDE_WORDS):
            return True
        extra = [d.get("builder")] + [r.get("시공사") for r in res["nice_table"] if isinstance(r.get("시공사"), str)]
        return self.find_builder(text, extra) is None

    def checked_credit(self, p: str, builder: str) -> str | None:
        """앞 줄 신용보강 문장 p 를 시공사 이름으로 확인한다.
        - 시공사 이름이 그대로 있거나, 같은 회사의 다른 표기(예: 'DL건설㈜')면 그대로
        - 이름이 한두 글자만 틀렸으면(장부 오타, 예: '아이파크현대산업개잘㈜') 고쳐서
        - 그 밖(다른 회사 이름 등)은 None -> 평가 문서에서 다시 찾는다"""
        b = bare(builder)
        if b in bare(p):
            return p
        if any(bare(k) in bare(p) for k, v in BUILDER_ALIAS.items() if bare(v) == b):
            return p
        for m in re.finditer(r"㈜?([가-힣A-Za-z0-9]{2,})(?:㈜|\(주\))", ju(p)):
            name = m.group(1)
            if abs(len(name) - len(b)) <= 1 and len(b) >= 4 and _edit_distance(name, b) <= 2                     and (name[:2] == b[:2] or name[-2:] == b[-2:]):
                return ju(p).replace(name, b, 1)
        return None

    def ledger_arranger(self, s) -> str | None:
        if not s:
            return None
        return self.arrangers.get(bare(s), ju(s))

    # ---- 한 건 ------------------------------------------------------------
    def build(self, issue: pd.Series, trades: pd.DataFrame, before_row: int | None = None) -> Out:
        kind, name = issue["kind"], issue["name"]
        F, G = issue["issue_date"], issue["maturity"]
        led_name = self.led_names.get(norm_spc(name))
        prior = self.led.autofill(led_name, before_row) if led_name else None
        prior = prior if prior and prior.found else None

        ev = self.ev.for_issue(name, kind, F, G, skip_kis=self._nice_says_no)
        nice, kis = ev["nice"], ev["kis"]
        docs = [d for d in (nice["match"], kis["match"]) if d]
        if not docs:
            # 회차가 맞는 문서가 없으면 두 평가사 중 '가장 최근' 문서 하나만 쓴다.
            # (옛 문서의 신용공여자가 지금과 다를 수 있다: 2026-03 포스코이앤씨 -> 2026-09 건설공제조합 사례)
            latest = [d for d in (nice["latest"], kis["latest"]) if d]
            docs = sorted(latest, key=lambda d: d["doc_date"], reverse=True)[:1]
        factors = [f for d in docs for f in d.get("factors", [])]
        site_builders = [d.get("builder") for d in docs] + [r.get("시공사") for r in ev["nice_table"]
                                                            if isinstance(r.get("시공사"), str)]
        ftext = " / ".join(factors)
        builder = self.find_builder(ftext, site_builders) if factors else None

        if factors and any(w in ftext for w in EXCLUDE_WORDS):
            verdict, reason = "아니오", "카드대금 건"
        elif builder:
            verdict, reason = "예", f"등급 근거: {factors[0]}"
        elif factors:
            verdict, reason = "아니오", f"등급 근거: {factors[0]}"
        elif prior:
            verdict, reason = "예", "평가사 문서 없음, 장부의 같은 SPC로 판정"
            builder = prior.values["J"]
        else:
            verdict, reason = "확인 필요", "평가사 문서를 찾지 못함"

        o = Out(values={}, verdict=verdict, reason=reason)
        if verdict == "아니오":
            return o
        v, chk = o.values, o.check

        # A·B
        v["A"] = led_name or ju(name)
        v["B"] = kind
        # C·E: 회차·발행금액(이번 회차 문서)
        # C·E: 평가 문서 회차표에서(사용자 2026-11-19: 회차가 조금 달라도 채우고, 모르면 공란. KOFIA 거래량은 발행액이 아니다)
        #  1) 발행일·만기가 맞는 회차(휴일로 며칠 밀린 것 포함)  2) 발행일만 같은 회차  3) 없으면 C·E 공란
        series = (nice["series"] or kis["series"] or nice.get("loose") or kis.get("loose") or [])
        notes = []
        if series:
            # 같은 날·같은 만기 회차가 여럿이면(예: 2-1, 2-2) 장부처럼 첫 회차 한 줄로 쓰고 비고에 남긴다
            s0 = series[0]
            v["C"] = f"제{s0['series']}회차"
            v["E"] = int(s0["amount"]) if float(s0["amount"]).is_integer() else round(s0["amount"], 2)
            others = list(dict.fromkeys(s["series"] for s in series[1:] if s["series"] != s0["series"]))
            if others and (nice["series"] or kis["series"]):
                notes.append("같은 만기 회차 함께 발행: " + ", ".join(f"제{x}회차" for x in others))
        else:
            v["C"] = v["E"] = None
            notes.append("평가 문서에 이번 발행 회차가 없어 회차·발행액 공란")
        # D
        if prior and prior.values.get("D"):
            v["D"] = prior.values["D"]
        else:
            v["D"] = asset_type(docs)
            if not v["D"]:
                chk["D"] = "유동화 유형 확인 필요"
        # F·G·H
        v["F"], v["G"] = F, G
        v["H"] = days_between(F, G)
        # I: 이번 회차 문서의 주관회사(NICE 우선)
        arr = [d.get("arranger") for d in (nice["match"], kis["match"], nice["latest"], kis["latest"]) if d]
        v["I"] = self.ledger_arranger(next((a for a in arr if a), None))
        if not v["I"] and prior:
            v["I"] = prior.values["I"]
            chk["I"] = "평가 문서에 주관회사 없음, 장부 직전 줄 값"
        elif not v["I"]:
            chk["I"] = "주관회사 확인 필요"
        # J
        v["J"] = builder
        same_builder = prior and bare(prior.values.get("J")) == bare(builder)
        if prior and not same_builder:
            chk["J"] = f"장부 직전 줄 시공사({prior.values.get('J')})와 다름"
        # K: SPC 등급(KOFIA)
        # K: 이번 회차 평가등급(평가사) -> 없으면 KOFIA 등급. KOFIA 등급 열은 가끔 틀린다(2026-09-14 A2- 사례)
        rt = trades["rating"].dropna().astype(str)
        k_kofia = re.sub(r"\s*\(sf\)\s*", "", rt.mode().iloc[0]) if len(rt) else None
        k_agency = [s["rating"] for s in (nice["series"] or nice.get("loose") or [])
                    + (kis["series"] or kis.get("loose") or [])]
        v["K"] = k_agency[0] if k_agency else k_kofia
        if not v["K"]:
            chk["K"] = "등급 없음"
        elif len(set(k_agency)) > 1:
            chk["K"] = f"평가사 등급이 다름: {', '.join(dict.fromkeys(k_agency))}"
        v["L"] = None
        # M·N
        rr = rate_range(trades)
        v["M"] = round(rr.low / 100, 6) if rr.low is not None else "-"   # 거래 없음은 '-'(사용자 2026-11-19)
        v["N"] = round(rr.high / 100, 6) if rr.high is not None else "-"
        notes = [n for n in rr.notes if not n.startswith("거래 없음")] + notes
        # O·P
        # 앞 줄 값이 비었거나 비정상적으로 길면(문서 본문이 섞인 값) 이어받지 않고 문서에서 다시 찾는다.
        # 앞 줄 신용보강의 시공사 이름이 한두 글자 틀린 장부 오타면 고쳐서 쓰고, 판단이 안 되면 문서에서 다시 찾는다.
        prior_p = None
        if (same_builder and prior.values.get("O") and prior.values.get("P")
                and len(prior.values["P"]) <= 150 and len(prior.values["O"]) <= 200):
            prior_p = self.checked_credit(prior.values["P"], builder)
        if prior_p:
            v["O"], v["P"] = prior.values["O"], prior_p
        else:
            v["O"] = site_text(nice, kis, v["D"])
            kis_f = [f for d in (kis["match"] or kis["latest"],) if d for f in d.get("factors", [])]
            v["P"] = credit_text(kis_f + factors, builder)
            if not v["O"]:
                chk["O"] = "사업지를 찾지 못함"
        if reason.startswith("평가사 문서 없음"):
            # 이번 평가 문서가 없어 SPC 표(장부)의 옛 시공사로 채운 줄: 그사이 신용공여자가 바뀌었을 수 있다
            chk["J"] = "이번 평가 문서가 없어 예전 시공사로 채움(신용공여자 변경 여부 확인)"
        if verdict == "확인 필요":
            failed = any("조회 실패" in e for e in ev["errors"])
            why = ("평가사 사이트 조회 실패로 판정 못 함(다음 실행 때 자동으로 다시 조회)" if failed else
                   "NICE·한신평에 이번 발행의 평가 문서가 없음(한기평·서울신용평가만 평가했거나 아직 공시 전일 수 있음)")
            chk["J"] = why
            notes.insert(0, why)
        if ev["errors"]:
            notes += ev["errors"]
        v["Q"] = "; ".join(notes) or None
        return o


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def asset_type(docs) -> str | None:
    for d in docs:
        t = (d.get("type") or "").replace(" ", "")
        if not t:
            continue
        if "PF" in t or "ProjectFinance" in t:
            return "PF Loan"
        if t.startswith("대출채권"):
            return "대출채권 유동화"
        if t.startswith("CDO"):
            return "CDO 유동화"
        return re.sub(r"\(.*?\)", "", d["type"]).strip()
    return None


def site_text(nice: dict, kis: dict, d_value: str | None) -> str | None:
    for d in (nice["match"], nice["latest"], kis["match"], kis["latest"]):
        if d and d.get("site"):
            return d["site"]
    if d_value and d_value != "PF Loan":
        return d_value   # 사업지가 없는 유형은 형태만(사용자 결정)
    return None


def credit_text(factors: list[str], builder: str | None) -> str | None:
    """등급 근거 중 시공사가 나오는 문장(한신평 먼저 - 장부 표현과 가깝다). 각주 번호 등 꼬리를 자른다."""
    for f in factors:
        if builder and bare(builder) in bare(f):
            f = re.split(r"\s\d{1,2}\s", ju(f))[0].strip()
            return f
    return ju(factors[0]) if factors else None
