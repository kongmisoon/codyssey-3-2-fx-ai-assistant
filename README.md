# 환율 AI 비서 (USD/KRW FX AI Assistant)

원/달러 환율 시계열 데이터를 Firestore에 저장하고, 그 **통계 요약을 시스템 프롬프트에 주입**해
AI가 일반론이 아니라 **내 데이터에 근거해** 답하는 웹 서비스입니다.

> "최근 환율 추세가 어때?" → "최근 20영업일 평균이 이전 대비 +1.2%로 원화 약세입니다.
> 기간 최고는 1,554.48원(2026-06-08)이었습니다."

---

## 현재 상태

**Phase 3 완료** — 환경설정 · AI provider 추상화 · 환율 522건 적재 · 데이터 CRUD API까지 완료.
전체 진행 계획과 단계별 실행 프롬프트는 [3-2.md](3-2.md) 참고.

---

## 기술 스택

| 영역 | 사용 기술 |
|---|---|
| Backend | FastAPI, Uvicorn, Pydantic v2 |
| Database | Firebase Firestore (firebase-admin) |
| AI | **provider 교체형** — Gemini(개발) / OpenAI GPT(제출) |
| Frontend | HTML / CSS / JavaScript (바닐라, 프레임워크 미사용) |
| 배포 | Render (백엔드) · Vercel (프론트엔드) |

### AI provider 추상화

개발 중 API 비용을 없애기 위해 Gemini 무료 티어를 쓰고, 제출·시연 시에는 GPT로 전환합니다.
`services/ai_service.py`가 두 SDK의 차이(메시지 role 명칭, 시스템 프롬프트 위치, 예외 타입)를
흡수하므로, **환경변수 한 줄만 바꾸면 코드 수정 없이 전환**됩니다.

```env
AI_PROVIDER=gemini   # 개발 중 (무료)
AI_PROVIDER=openai   # 제출 · 시연
```

---

## 프로젝트 구조

```
backend/
├── main.py                  # FastAPI 앱, CORS, 라우터 등록, 헬스체크
├── config.py                # 환경변수 → 설정 객체 (단일 창구)
├── database.py              # Firestore 초기화 (싱글톤 + 인증 경로 분기)
├── models/
│   └── schemas.py           # Pydantic 요청/응답 모델 + 검증 규칙
├── routers/
│   └── data.py              # /api/data CRUD
├── services/
│   ├── errors.py            # 서비스 예외 (NotFound 404 / Conflict 409)
│   ├── data_service.py      # Firestore CRUD 로직
│   └── ai_service.py        # AI 호출 (provider 분기)
├── data/
│   └── usdkrw_2024_2026.csv # 원/달러 환율 원본 (522영업일)
├── scripts/
│   ├── check_setup.py       # 환경 점검 스크립트
│   ├── seed_data.py         # CSV → Firestore 적재
│   ├── smoke_data_api.py    # /api/data 통합 검증 (28개 케이스)
│   └── set_key.py           # .env 에 API 키 저장 헬퍼 (set_key.bat 더블클릭)
├── requirements.txt
└── .env.example
```

**설계 원칙** — 라우터는 HTTP 입출력만, 서비스는 비즈니스 로직만 담당합니다.
라우터는 어떤 AI 모델을 쓰는지 알지 못하고, `generate_reply()`만 호출합니다.

서비스는 HTTP를 모르기 때문에 `HTTPException` 대신 `NotFoundError` / `ConflictError`를 던지고,
`main.py`의 전역 예외 핸들러가 이를 404 / 409로 변환합니다. 예상하지 못한 예외는 500과 일반 메시지만
반환하고 내부 스택은 서버 로그에만 남깁니다. 덕분에 라우터에 try/except를 반복하지 않아도 되고,
같은 서비스 함수를 챗봇(Phase 6)이나 Function Calling 도구에서 그대로 재사용할 수 있습니다.

---

## API

### 데이터 (`/api/data`)

| 메서드 | 경로 | 설명 | 주요 응답 |
|---|---|---|---|
| `POST` | `/api/data` | 환율 추가 | 201 · 409(같은 날짜 존재) · 422 |
| `GET` | `/api/data` | 목록 조회 (`start_date`, `end_date`, `limit`, `order`) | 200 · 400(기간 역전) · 422 |
| `PUT` | `/api/data/{id}` | 부분 수정 (날짜 변경 시 문서 이동) | 200 · 404 · 409 · 422 |
| `DELETE` | `/api/data/{id}` | 삭제 | 200 · 404 |

**문서 ID = 날짜(`YYYY-MM-DD`)** — 환율은 하루에 한 값만 존재하므로, 날짜를 ID로 써서 DB 차원에서 중복을 막습니다.
생성은 Firestore `create()`(이미 있으면 서버가 거부)로, 날짜 변경은 "새 ID 생성 + 기존 ID 삭제"를
**트랜잭션**으로 묶어 처리하므로 중간에 다른 요청이 끼어들어도 데이터가 꼬이지 않습니다.

### 입력 검증 (Pydantic)

| 필드 | 규칙 | 이유 |
|---|---|---|
| `date` | `YYYY-MM-DD` 형식 + 실제 존재하는 날짜 | 정규식만으로는 `2026-02-30`을 못 거름 |
| `value` | 500 ~ 5000, NaN/무한대 거부, 소수점 2자리 반올림 | 원/달러 환율의 현실적 범위, 오타(예: 13.5) 차단 |
| `memo` | 최대 200자, 앞뒤 공백 제거 | 저장 용량·화면 표시 보호 |
| 공통 | 정의되지 않은 필드 거부 (`extra="forbid"`) | `rate`처럼 이름을 잘못 보내면 조용히 무시되는 대신 422로 알려줌 |
| `PUT` | 수정할 필드가 하나도 없으면 거부 | 아무 변화 없는 요청으로 `updated_at`만 바뀌는 일 방지 |

잘못된 요청은 라우터 함수에 들어오기 전에 FastAPI가 422로 돌려보내므로, 서비스와 DB 코드는 검증된 데이터만 다룹니다.

---

## 로컬 실행

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env          # macOS/Linux: cp .env.example .env
```

`.env`에 키를 채운 뒤 환경 점검부터 실행합니다.

```bash
python scripts/check_setup.py
```

세 항목이 모두 `[OK]`면 환율 데이터를 Firestore에 적재합니다.

```bash
python scripts/seed_data.py --dry-run    # 미리보기 (DB에 쓰지 않음)
python scripts/seed_data.py --limit 10   # 10건 시험 적재
python scripts/seed_data.py              # 전체 522건 적재
```

문서 ID를 날짜(`YYYY-MM-DD`)로 쓰기 때문에 여러 번 실행해도 중복이 생기지 않습니다.

서버를 띄웁니다.

```bash
uvicorn main:app --reload
```

- API 문서(Swagger): http://127.0.0.1:8000/docs
- 헬스체크: http://127.0.0.1:8000/health
- 설정·연결 상태: http://127.0.0.1:8000/health/detail

---

## 환경 변수

| 이름 | 설명 | 예시 |
|---|---|---|
| `AI_PROVIDER` | 사용할 AI provider | `gemini` / `openai` |
| `GEMINI_API_KEY` | Google AI Studio 키 | `AIza...` 또는 `AQ....` |
| `GEMINI_MODEL` | Gemini 모델명 | `gemini-3.6-flash` |
| `OPENAI_API_KEY` | OpenAI 키 | `sk-...` |
| `OPENAI_MODEL` | GPT 모델명 | `gpt-4o-mini` |
| `MAX_OUTPUT_TOKENS` | 응답 최대 토큰 (비용 방어) | `500` |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | 서비스 계정 키 JSON 전문 (배포용) | `{"type":"service_account",...}` |
| `GOOGLE_APPLICATION_CREDENTIALS` | 서비스 계정 키 파일 경로 (로컬용) | `./serviceAccountKey.json` |
| `ALLOWED_ORIGINS` | CORS 허용 도메인 (콤마 구분) | `http://localhost:5500,https://app.vercel.app` |

> 🔐 `.env`와 서비스 계정 키 파일은 `.gitignore`에 등록되어 있습니다. 절대 커밋하지 마세요.

---

## 데이터

원/달러 환율 일별 종가 **522개 영업일** (2024-09-05 ~ 2026-09-04).

| 지표 | 값 |
|---|---|
| 평균 | 1,431.01원 |
| 최고 | 1,554.48원 (2026-06-08) |
| 최저 | 1,309.30원 (2024-09-30) |
| 변동폭 | 245.18원 |
| 표준편차 | 50.81원 |

환율은 **합계(total)가 의미 없는 지표**이므로, 과제 예시의 `total` 대신
변동폭(range) · 표준편차(std) · 기간 변화율(total_change_pct)을 요약 지표로 사용합니다.
추세 판정도 환율의 낮은 일간 변동성을 반영해 **±0.5% / 20영업일** 기준으로 계산합니다.

---

## 배포 URL

| 구분 | URL |
|---|---|
| 프론트엔드 (Vercel) | _Phase 8에서 추가_ |
| 백엔드 API (Render) | _Phase 8에서 추가_ |
| Swagger 문서 | _Phase 8에서 추가_ |

> ⏱️ Render 무료 티어는 15분간 요청이 없으면 절전 상태가 됩니다.
> 첫 접속 시 응답까지 최대 50초가 걸릴 수 있습니다.
