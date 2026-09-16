# 환율 AI 비서 (USD/KRW FX AI Assistant)

원/달러 환율 시계열 데이터를 Firestore에 저장하고, 그 **통계 요약을 시스템 프롬프트에 주입**해
AI가 일반론이 아니라 **내 데이터에 근거해** 답하는 웹 서비스입니다.

> "최근 환율 추세가 어때?" → "최근 20영업일 평균이 이전 대비 +1.2%로 원화 약세입니다.
> 기간 최고는 1,554.48원(2026-06-08)이었습니다."

---

## 현재 상태

**Phase 1 완료** — 환경설정, 프로젝트 구조, AI provider 추상화 레이어까지 구현·검증 완료.
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
├── models/                  # Pydantic 스키마            (Phase 3)
├── routers/                 # HTTP 엔드포인트            (Phase 3~6)
├── services/
│   └── ai_service.py        # AI 호출 (provider 분기)
├── scripts/
│   └── check_setup.py       # 환경 점검 스크립트
├── requirements.txt
└── .env.example
```

**설계 원칙** — 라우터는 HTTP 입출력만, 서비스는 비즈니스 로직만 담당합니다.
라우터는 어떤 AI 모델을 쓰는지 알지 못하고, `generate_reply()`만 호출합니다.

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

세 항목이 모두 `[OK]`면 서버를 띄웁니다.

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
| `GEMINI_API_KEY` | Google AI Studio 키 | `AIza...` |
| `GEMINI_MODEL` | Gemini 모델명 | `gemini-2.5-flash` |
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
