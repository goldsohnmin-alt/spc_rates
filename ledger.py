"""장부(시공사매출금리 시트) 읽기 + 반복 SPC 자동 채움.

원본 엑셀은 읽기 전용(read_only=True)으로만 연다. 수식 대신 저장된 값(data_only=True)을 읽는다.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

import openpyxl

SHEET = "시공사매출금리"
COLS = "ABCDEFGHIJKLMNOP"  # 장부 A~P
HEADERS = ["회사명", "종류", "회차", "기초자산", "발행액", "발행일", "만기일", "일수",
           "주관회사", "시공사", "현재등급", "발행금리", "매출금리 하단", "매출금리 상단",
           "사업지", "신용보강"]
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def norm_name(s) -> str:
    """SPC·시공사 이름 비교용: 앞뒤 공백 제거, 연속 공백은 하나로."""
    if s is None:
        return ""
    return re.sub(r"\s+", " ", str(s)).strip()


def clean(v):
    """문자열이면 앞뒤 공백 제거, 빈 문자열은 None."""
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


@dataclass
class Row:
    excel_row: int          # 엑셀의 실제 행 번호(1행이 머리글)
    values: dict            # {'A': ..., 'P': ...}

    def __getitem__(self, col):
        return self.values[col]


@dataclass
class Fill:
    """반복 SPC 자동 채움 결과. check 에 든 열은 노란색('확인 필요')으로 표시한다."""
    found: bool
    values: dict = field(default_factory=dict)   # I, J, K, O, P
    check: dict = field(default_factory=dict)    # 열 -> 사유
    source_row: int | None = None                # 근거가 된 같은 SPC 줄(엑셀 행 번호)


def find_workbook(path: str | None = None) -> str:
    if path:
        return path
    files = [f for f in glob.glob(os.path.join(DATA_DIR, "*.xlsx"))
             if not os.path.basename(f).startswith("~$")]
    if not files:
        raise FileNotFoundError(f"{DATA_DIR} 에 장부 엑셀 복사본(.xlsx)이 없습니다.")
    if len(files) > 1:
        files.sort(key=os.path.getmtime)  # 가장 최근에 바뀐 파일을 쓴다
    return files[-1]


def load_ledger(path: str | None = None) -> list[Row]:
    path = find_workbook(path)
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[SHEET]
        it = ws.iter_rows(min_row=1, max_col=len(COLS), values_only=True)
        header = [clean(v) for v in next(it)]
        if header != HEADERS:
            raise ValueError(f"'{SHEET}' 시트 1행 머리글이 예상과 다릅니다: {header}")
        rows = []
        for i, r in enumerate(it, start=2):
            if all(v is None or (isinstance(v, str) and not v.strip()) for v in r):
                continue
            rows.append(Row(i, {c: clean(v) for c, v in zip(COLS, r)}))
        return rows
    finally:
        wb.close()


class Ledger:
    def __init__(self, rows: list[Row]):
        self.rows = rows
        self._by_spc: dict[str, list[Row]] = {}
        self._by_builder: dict[str, list[Row]] = {}
        for r in rows:
            self._by_spc.setdefault(norm_name(r["A"]), []).append(r)
            if r["J"]:
                self._by_builder.setdefault(norm_name(r["J"]), []).append(r)

    @classmethod
    def load(cls, path: str | None = None) -> "Ledger":
        return cls(load_ledger(path))

    def has_spc(self, name) -> bool:
        return norm_name(name) in self._by_spc

    def autofill(self, spc_name, before_row: int | None = None) -> Fill:
        """같은 SPC의 가장 최근 줄에서 I·J·O·P를, 같은 시공사의 가장 최근 줄에서 K를 가져온다.

        before_row 를 주면 그 엑셀 행보다 앞선 줄만 본다(시험용).
        """
        def prior(lst):
            return [r for r in lst if before_row is None or r.excel_row < before_row]

        spc_rows = prior(self._by_spc.get(norm_name(spc_name), []))
        if not spc_rows:
            return Fill(found=False)
        last = spc_rows[-1]
        f = Fill(found=True, source_row=last.excel_row)
        f.values["D"] = last["D"]
        f.values["C"] = last["C"]   # 회차 추정용(직전 회차)
        for c in "IJOP":
            f.values[c] = last[c]
            if last[c] is None:
                f.check[c] = "직전 줄에 값 없음"

        # K: 같은 시공사의 가장 최근 등급(같은 SPC 옛 줄보다 최신일 가능성이 높다)
        builder_rows = prior(self._by_builder.get(norm_name(last["J"]), []))
        f.values["K"] = builder_rows[-1]["K"] if builder_rows else last["K"]
        f.check["K"] = "등급 기준(평가사·산정 방식) 확인 필요"
        # 시공사의 최근 등급이 바로 앞 줄과 다르면(실제 변경 또는 장부 입력 실수) 사유에 남긴다
        if len(builder_rows) >= 2 and builder_rows[-1]["K"] != builder_rows[-2]["K"]:
            f.check["K"] += (f"; 시공사 최근 등급 변경 {builder_rows[-2]['K']}→{builder_rows[-1]['K']}"
                             f" (행 {builder_rows[-1].excel_row})")

        # 직전 같은 SPC 줄들끼리 I·J·O·P가 달랐으면 표시
        if len(spc_rows) >= 2:
            prev = spc_rows[-2]
            for c in "IJOP":
                if norm_name(prev[c]) != norm_name(last[c]) and c not in f.check:
                    f.check[c] = "같은 SPC의 이전 줄과 값이 다름"
        return f
