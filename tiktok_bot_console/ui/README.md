# TikTok Bot Console — UI

Vue 3 control panel for the Hermes-Agent-powered TikTok B2B outreach bot. Manages the 6-stage pipeline (collect → filter → strategy → outreach → report → iterate), user library, social accounts, and LLM providers.

## Quick start (preliminary testing)

```bash
# 1. install
npm install

# 2. dev server (mock data, no backend needed)
npm run dev          # → http://localhost:5173

# 3. sign in with any username / password ≥ 4 chars
#    e.g. ops@delong.com / demo
```

The dev server runs against built-in mock data — no Python backend required. A yellow banner under the topbar shows `Mock 模式 · 后端不可达 · 已切换 Mock 数据` so you always know you're on mock.

To switch to the real backend, edit `.env.development`:
```
VITE_USE_MOCK=false
VITE_API_BASE=http://your-api:8000
```

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Vite dev server with HMR |
| `npm run build` | Type-check + production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run type-check` | TypeScript validation via `vue-tsc` |
| `npm run test` | Run smoke tests (Node, no browser needed) |

## Project structure

```
src/
├── api/
│   ├── index.ts          # Axios + wrapped exports (mock-fallback)
│   └── mock.ts           # Mock data + mock API (used in dev / offline)
├── assets/
│   ├── design-system.css # OKLch tokens + components (16 KB)
│   └── main.css          # App entry styles + Element Plus overrides
├── i18n/
│   ├── zh-CN.ts          # Simplified Chinese (405 keys)
│   └── en-US.ts          # English (405 keys)
├── router/
│   └── index.ts          # 10 routes (1 login + 1 redirect + 8 protected)
├── stores/
│   └── auth.ts           # Pinia auth store (token + username)
├── views/
│   ├── Login.vue
│   ├── Dashboard.vue
│   ├── Users.vue
│   ├── UserDetail.vue
│   ├── Pipeline.vue
│   ├── Reports.vue
│   ├── ConfigAccounts.vue
│   ├── ConfigLlm.vue
│   └── ConfigPipeline.vue
├── App.vue               # Shell — sidebar + topbar + lang switcher + breadcrumb
├── main.ts               # Pinia + router + i18n + Element Plus mount
scripts/
└── smoke.mjs             # Pure-Node smoke test (43 checks)
```

## What was redesigned

This UI was redesigned in two passes against the original Vue admin:

1. **Visual direction** — Tech / Utility × TikTok brand hint
   - 240px black sidebar (`oklch(14% 0.012 280)`)
   - Primary brand color: `oklch(58% 0.22 350)` (pink-purple, TikTok-adjacent)
   - Type system: Inter (UI) + JetBrains Mono (data)
   - 12 OKLch color tokens, hairline borders, restrained status pills
   - Density tuned for ops users — 6-stage pipeline strip, 6-cell status bar, dense data tables

2. **Information architecture** — moved from 1 Config tab to 3 dedicated routes
   - `/config-accounts` — TikTok / Douyin account manager with cookie health
   - `/config-llm` — LLM provider + per-Skill usage breakdown
   - `/config-pipeline` — daily limits, intervals, cron schedule, keyword library, anti-ban policy
   - Added `/users/:username` detail route (was previously inaccessible)

## What's in mock mode

Mock mode returns realistic-shaped responses for all 18 endpoints:

| Endpoint | Mock payload |
|---|---|
| `getDashboard` | 1247 users · 47 new · 14.6% reply rate · top 5 keywords |
| `getUsers` | 10 records with personas, regions, follow counts |
| `getPipelineEvents` | 17 events covering the 6 stages |
| `getTrendReport` | 30 days of synthetic but believable numbers |
| `getAccounts` | 3 accounts (2 TikTok, 1 Douyin) with health states |
| `getConfig` | DeepSeek v4 Pro with daily caps and keywords |
| `login` | Accepts any password ≥ 4 chars (≥ 1 char username) |
| `runPipeline` | Echoes back started stage list |

When `VITE_USE_MOCK=false` and the real backend is unreachable, reads automatically fall back to mock so the UI never breaks during development.

## Languages

Switch between 中文 and English using the `中 / EN` toggle in the top-right corner of every page. Locale is persisted to `localStorage` and re-applied on reload.

## Smoke test coverage

`npm run test` runs `scripts/smoke.mjs` — pure-Node, no test runner or browser needed:

- **i18n parity** — every key in `zh-CN.ts` exists in `en-US.ts` and vice versa (405 keys), no empty values
- **Mock API shape** — 13 endpoints return expected payload structures and filter behavior
- **Router** — all 10 routes registered (parsed from router source, since router uses Vite-specific `import.meta.env`)
- **View files** — every `.vue` file exists and is non-empty (9 files, 8.8–17.2 KB)
- **Project structure** — `.env.development`, `design-system.css`, `main.css`, `App.vue`, `README.md` all present
- **Build artifacts** — `dist/` exists after `npm run build`

**43 checks, all passing.**

## Tech stack

- Vue 3.5 + Pinia 3 + Vue Router 5 + Vue I18n 9
- Element Plus 2 (heavily restyled to match the design system)
- Vite 8 + TypeScript 6 + Vue-tsc 3
- Node 24 (uses built-in TS support for the smoke test)
- OKLch colors throughout (modern, perceptually uniform)

## What's NOT included (next steps)

- ❌ Real backend integration tests (mock-mode only)
- ❌ E2E tests (Playwright not installed — add when backend is wired up)
- ❌ Component unit tests for individual views (only smoke coverage for now)
- ❌ Visual regression tests
- ❌ CI pipeline

## Known limitations

- **Vite 8 native binding on Windows** — `npm run dev` and `npm run build` need the `rolldown-win32-x64-msvc.node` native module. If your `npm install` skipped optional dependencies, run `npm rebuild rolldown` to fetch it.
- Search box in topbar is visual-only (no global search handler wired up)
- Login in mock mode accepts any credentials — disable `VITE_USE_MOCK` before deploying
- Pipeline "运行" button in mock mode just echoes success without simulating stages
- No persistence beyond `localStorage` (token, username, locale)

## IDE setup

[VS Code](https://code.visualstudio.com/) + [Vue (Official)](https://marketplace.visualstudio.com/items?itemName=Vue.volar) (and disable Vetur).

## Customize configuration

See [Vite Configuration Reference](https://vite.dev/config/).