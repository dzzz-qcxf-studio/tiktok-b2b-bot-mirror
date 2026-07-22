# TikTok B2B Bot — Full Stack

Hermes-Agent-powered TikTok B2B outreach automation. Frontend (Vue 3) + Backend (FastAPI + SQLite) + Browser automation (Playwright) + LLM (DeepSeek).

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Ubuntu / Docker (生产)                          │
│                                                              │
│  ┌──────────────────────┐   ┌───────────────────────────┐  │
│  │   Vue 3 控制台        │   │   FastAPI REST API          │  │
│  │   (Nginx → dist/)    │←→│   (uvicorn :8000)          │  │
│  │   :8080               │   │                            │  │
│  └──────────────────────┘   └─────────────┬─────────────┘  │
│                                                │            │
│                     ┌──────────────────────────┴──────┐    │
│                     │  SQLite (tiktok_bot.db, 127 KB)   │    │
│                     │  + ChromaDB (vector memory)      │    │
│                     └─────────────────────────────────┘    │
│                                                              │
│           ┌──────────────┐    ┌─────────────────────┐    │
│           │  Playwright  │    │  DeepSeek v4 Pro LLM │    │
│           │  (browser)   │    │  (api.deepseek.com)  │    │
│           └──────────────┘    └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Quick start (Docker, one command)

```bash
cd "tiktok-bot-software"
docker compose up -d --build
# 1. seed mock data into SQLite
docker compose exec tiktok-bot python -m tiktok_bot_api.seed
# 2. open UI
#    http://localhost:8080      (frontend, served by nginx)
#    http://localhost:8000/docs  (FastAPI auto-generated OpenAPI)
```

Login with any username + password ≥ 4 chars. The UI defaults to mock mode (`VITE_USE_MOCK=true`); switch to real backend by editing `tiktok_bot_console/ui/.env.production`:

```
VITE_USE_MOCK=false
VITE_API_BASE=http://localhost:8000
```

Then rebuild the UI image:
```bash
docker compose up -d --build tiktok-bot
```

## Quick start (dev, no Docker)

Two terminals:

```bash
# Terminal 1 — backend
cd tiktok-bot-software
pip install -e .
python -m tiktok_bot_api.seed
uvicorn tiktok_bot_api.main:app --reload --port 8000

# Terminal 2 — frontend
cd tiktok_bot-software/tiktok_bot_console/ui
npm install
npm run dev          # http://localhost:5173
```

The UI defaults to mock mode (`VITE_USE_MOCK=true`). Switch by editing `tiktok_bot_console/ui/.env.development`:
```
VITE_USE_MOCK=false
VITE_API_BASE=http://localhost:8000
```

## Project structure

```
tiktok-bot-software/
├── tiktok_bot_api/        # FastAPI REST backend
│   ├── main.py            # 25 endpoints, CORS, Pydantic models
│   ├── auth.py            # JWT login + register
│   ├── seed.py            # data seeding (mirrors mock.ts)
│   └── __init__.py
├── tiktok_bot_core/       # business logic
│   ├── models/entities.py # 9 SQLAlchemy 2.0 models
│   ├── storage/database.py # SQLAlchemy engine + session
│   ├── settings.py        # pydantic-settings, env-driven config
│   ├── events/bus.py      # async event bus
│   ├── llm/client.py      # OpenAI-compatible LLM client
│   ├── browser/client.py  # Playwright wrapper
│   ├── plugins/           # collector / channel plugins
│   └── services/          # business services (auth, etc.)
├── tiktok_bot_console/    # Vue 3 frontend
│   ├── ui/                # Vite + Element Plus + Pinia + i18n
│   │   ├── src/api/mock.ts # mock layer (used when VITE_USE_MOCK=true)
│   │   └── src/api/index.ts # axios + auto-fallback wrapper
│   └── cli/               # legacy CLI
├── data/
│   └── tiktok_bot.db      # SQLite (created on first run)
├── docker/
│   ├── nginx.conf
│   └── entrypoint.sh
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
└── README.md
```

## Data contract

9 tables in `tiktok_bot_core/models/entities.py`:

| Table | Purpose | Mock mirror |
|---|---|---|
| `users` | TikTok / Douyin user library | `MOCK_USERS` in `mock.ts` (10 records) |
| `strategies` | Per-user strategy + templates | generated for qualified users |
| `messages` | Comments / DMs sent log | 3 sample messages |
| `replies` | Reply tracking + sentiment | 1 sample positive reply |
| `daily_reports` | Daily aggregates | 1 today's report |
| `experience_rules` | Stage-6 learned rules | empty |
| `accounts` | TikTok / Douyin accounts | `MOCK_ACCOUNTS` (3 accounts) |
| `tiktok_accounts` | account-specific metadata | empty |
| `config_records` | runtime config key-value | daily limits, intervals, keywords |

Mock data is **identical** to seed data — both are 10 users with the same personas, statuses, regions, and follow counts. Switching from mock to real backend is a `VITE_USE_MOCK` toggle with zero frontend changes.

## API surface (25 endpoints)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/auth/login` | accepts any 4+ char pwd in mock; real backend uses bcrypt + JWT |
| POST | `/api/auth/register` | 2-arg (username, password) since Pinia store refactor |
| GET  | `/api/auth/me` | returns auth status |
| GET  | `/api/users?status=&limit=` | list with filters |
| GET  | `/api/users/{id}` | single user |
| GET  | `/api/users/stats` | count by status |
| GET  | `/api/stats/dashboard` | overview + keywords + categories |
| GET  | `/api/stats/wordcloud` | word frequency |
| POST | `/api/pipeline/run` | body: `{ stages: [...] }` |
| GET  | `/api/pipeline/events` | latest event log |
| GET  | `/api/pipeline/events/stream` | SSE stream (future) |
| GET  | `/api/reports/daily?d=YYYY-MM-DD` | single day |
| GET  | `/api/reports/trend?days=30` | trend array |
| GET  | `/api/config` | all config records |
| PUT  | `/api/config/{key}` | body: `{ value, description }` |
| POST | `/api/config/apikey` | body: `{ api_key }` |
| GET  | `/api/accounts?platform=` | list with filter |
| POST | `/api/accounts` | body: `{ platform, username }` |
| PUT  | `/api/accounts/{id}/cookies` | manual cookie update |
| DELETE | `/api/accounts/{id}` | remove |
| POST | `/api/accounts/login-qrcode` | start QR login |
| GET  | `/api/accounts/login-status` | poll QR status |
| GET  | `/api/accounts/qrcode/{token}` | QR image |
| POST | `/api/accounts/{id}/check-session` | cookie still valid? |
| GET  | `/api/health` | healthcheck (used by docker compose) |

Full schema available at `http://localhost:8000/docs` (Swagger UI auto-generated from Pydantic models).

## Authentication

- Mock mode: any username + password ≥ 4 chars (e.g. `ops@delong.com / demo`)
- Real mode: bcrypt-hashed passwords stored in `users` table (or dedicated auth table), JWT bearer token

In either mode, `localStorage.token` is set and sent as `Authorization: Bearer …` header (mock mode skips the actual verification).

## Anti-ban strategy (operational guardrails)

The Pipeline respects:
- Daily caps: 25 comments / 12 DMs per account (3 accounts × caps = 75 / 36 daily)
- Random intervals: 3-10 min between comments, 8-20 min between DMs
- 24-hour gap between comment and follow-up DM
- Off-hours blocked (00:00 – 08:00 UTC-6)
- Cookie re-check every 6 hours
- Account rotation on cookie expiry

Configured in the UI at `/config-pipeline`. Mirror values to `config_records` table for backend enforcement.

## Known limitations

- ❌ No automated E2E tests (Playwright tests planned)
- ❌ No production deployment guide (just `docker compose up`)
- ❌ No CI pipeline
- ❌ `npm run build` in Windows needs `npm rebuild rolldown` for the native binding

## Tech stack

- **Backend**: Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite · Alembic (future) · ChromaDB · Playwright · OpenAI SDK
- **Frontend**: Vue 3.5 · Pinia · Vue Router · Vue I18n · Element Plus · ECharts · TypeScript · Vite
- **Infra**: Docker · Nginx · uvicorn
- **Colors**: OKLch design tokens throughout the Vue UI