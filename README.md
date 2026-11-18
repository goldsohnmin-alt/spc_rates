# 시공사 매출금리 (spc-rates)

KOFIA 단기사채(ABSTB)·ABCP 발행분 중 **시공사가 신용을 보강한 건**을 골라,
장부에 붙여넣을 줄(A~P열, 비고 Q열)을 매일 자동으로 만들어 사이트에 보여 줍니다.

- 판정 근거: NICE신용평가·한국신용평가의 공개 평가 의견서(PDF)
- 금리(매출금리 하단·상단): KOFIA 발행일 당일 매출 거래, 거래량 10% 이상인 금리의 최저·최고

## 자동 실행 (GitHub Actions)
- `daily.yml`: 평일 07:40(한국시간) 전 영업일 발행분. 수동 실행 때 시작·종료일을 넣으면 밀린 날짜를 채웁니다.
- `backfill.yml`: 매일 자정, 아직 안 채운 과거 영업일(2024-11-18 이후)을 최근부터 5일씩 채웁니다.
- 결과: `docs/history.json`(날짜별 줄), `docs/data.json`(최근 날짜·오류), `docs/spc_table.json`(SPC 표),
  `state/spc_db.csv`(SPC 분류: 시공사 건 여부).

## 사이트 사용법
발행일을 고르고 **장부용 A~P 복사** 또는 **비고 포함 A~Q 복사**를 누른 뒤 엑셀에 붙여넣습니다.
노란 칸은 확인이 필요한 값입니다(칸에 마우스를 올리면 사유).

## 개인 PC에서 (Windows)
```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python make_rows.py 2026-11-17          # 하루치 줄 만들기 (output\ 에 저장)
.venv\Scripts\python spc_table.py export              # 장부 복사본(data\)으로 SPC 표 다시 만들기
```
PDF·엑셀·`data/`·`output/`은 커밋하지 않습니다. 원본 엑셀은 읽기만 합니다.
