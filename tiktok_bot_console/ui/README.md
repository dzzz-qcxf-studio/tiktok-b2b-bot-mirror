# TikTok Bot Console — UI

Vue 3 control panel for the Hermes-Agent-powered TikTok B2B outreach bot. Manages the 6-stage pipeline (collect → filter → strategy → outreach → report → iterate), user library, social accounts, and LLM providers.

> Last updated: 2026-08-01

## Quick start

```bash
# 1. install
npm install

# 2. start the API from the repository root
python -m uvicorn tiktok_bot_api.main:app --env-file .env --reload --port 8000

# 3. start the UI
npm run dev          # → http://localhost:5173

# 4. sign in with a registered username/password or API key
```

Development `Auto` mode now prefers the real backend. Most read-only screens fall back
to visibly marked mock data only when the API is unreachable; LLM management never
falls back to editable fake configuration. To point the UI at another API, edit
`.env.development`:
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
| `npm exec vitest -- --config vitest.config.ts --run src/views/ConfigLlm.spec.ts` | Run focused LLM configuration component tests |

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
│   ├── zh-CN.ts          # Simplified Chinese (673 keys)
│   └── en-US.ts          # English (673 keys)
├── router/
│   └── index.ts          # 12 routes (including redirect and 404)
├── stores/
│   └── auth.ts           # Pinia auth store (token + username)
├── views/
│   ├── Login.vue
│   ├── Dashboard.vue
│   ├── Users.vue
│   ├── UserDetail.vue
│   ├── Leads.vue
│   ├── Pipeline.vue
│   ├── Reports.vue
│   ├── ConfigAccounts.vue
│   ├── ConfigLlm.vue
│   ├── ConfigPipeline.vue
│   └── NotFound.vue       # 11 view files in total
├── components/
│   └── InteractiveLoginModal.vue # Manual browser login; no QR or credentials
├── App.vue               # Shell — sidebar + topbar + lang switcher + breadcrumb
├── main.ts               # Pinia + router + i18n + Element Plus mount
scripts/
└── smoke.mjs             # Pure-Node smoke test (128 checks)
```

## Interactive account login

`/config-accounts` opens an isolated TikTok or Douyin browser session through the
real backend. Complete QR, SMS, CAPTCHA, and any other platform checks inside that
browser, then return to the modal and select **Verify and save login**. The modal
never renders a QR image, cookie, storage state, profile path, or session token.

Closing the modal, switching platform, or unmounting the page cancels an unfinished
session. Only a backend `confirmed` response refreshes the account list. This flow
therefore requires real API mode; the legacy mock QR simulation is not used.

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
   - `/config-llm` — real Provider CRUD, server-side connection tests, five business routes, and usage
   - `/config-pipeline` — daily limits, intervals, cron schedule, keyword library, anti-ban policy
   - Added `/users/:username` detail route (was previously inaccessible)

## What's in mock mode

Mock mode returns realistic-shaped responses for all 18 endpoints:

| Endpoint | Mock payload |
|---|---|
| `getDashboard` | 1247 users · 47 new · 14.6% reply rate · top 5 keywords |
| `getUsers` | 10 records with personas, regions, follow counts |
| `listPipelineJobs` | Unified TikTok/Douyin job history with persisted stages |
| `getPipelineCapabilities` | Provider, account and concurrency preflight |
| `listPipelineSchedules` | Schedule-triggered jobs from the same SQLite queue |
| `getTrendReport` | 30 days of synthetic but believable numbers |
| `getAccounts` | 3 accounts (2 TikTok, 1 Douyin) with health states |
| `getConfig` | DeepSeek v4 Pro with daily caps and keywords |
| `login` | Accepts any password ≥ 4 chars (≥ 1 char username) |
| `createPipelineJob` | Creates one unified mock job with platform/account/stages |

When `VITE_USE_MOCK=false` and the real backend is unreachable, most reads automatically fall back to mock so the UI never breaks during development. LLM management is the deliberate exception: Provider secrets, routes, connectivity, and usage always use the real backend and surface an unavailable state instead of presenting editable fake configuration.

## Languages

Switch between 中文 and English using the `中 / EN` toggle in the top-right corner of every page. Locale is persisted to `localStorage` and re-applied on reload.

## Unified Pipeline UI

There is only one `/pipeline` route. The task creator selects:

- platform: TikTok or Douyin;
- account mode: automatic or specified account;
- an account when specified mode is selected;
- one or more of the six stages.

The same page shows durable job history and stage detail, with cancel and retry actions.
TikTok is shown as blocked because the backend currently registers only the unavailable
placeholder Provider. Unlocking it requires implementing and registering a concrete
fingerprint-browser adapter in code first, then configuring the account Profile required
by that adapter; populating Provider/Profile fields alone does not unlock execution.
The UI never implies a Playwright fallback. Douyin uses isolated Playwright contexts in
the backend and displays the configured platform concurrency.

`/config-pipeline` atomically saves the complete runtime configuration and manages
five-field-cron schedules for both platforms. Changing `douyin_max_concurrency`
(valid range 1..20) requires a backend restart. `/config-accounts` shows the
TikTok Provider/Profile metadata without claiming that metadata alone activates a
fingerprint-browser vendor.

## LLM configuration

`/config-llm` manages the database-backed Provider registry and the ordered
`collection`, `qualification`, `strategy`, `iteration`, and `default` routes. There is
no separate browser-side “main Provider” state: route order is the only source of
priority. Connection tests call the backend adapter and never fetch an upstream model
URL from the browser.

Existing API keys are never returned or filled into the editor. The password input is
blank on every edit and submits a Secret request only when the operator types a
replacement. The backend persists that value to the Provider's named environment
variable in the Git-ignored project `.env`. Provider and route states are real even
while the rest of the development console is in mock mode.

All LLM calls require backend authentication. The Axios request interceptor reads the
current token for every request, so a login performed after the module was loaded takes
effect without reloading the bundle. If Provider creation succeeds but its Secret write
fails, the editor keeps the new Provider id and retries as an update instead of creating
a duplicate record.

## Smoke test coverage

`npm run test` runs `scripts/smoke.mjs` — pure-Node, no test runner or browser needed:

- **i18n parity** — all 673 keys exist in both locales, with no empty values
- **Mock/API contracts** — unified job lifecycle, capabilities, schedule CRUD and atomic runtime config
- **Pipeline UI contracts** — platform/account selection, provider block, pagination, polling, cancel and retry
- **LLM contracts** — complete typed API usage, no browser upstream fetch, blank secret input, no mock management path
- **Router** — no platform-specific Pipeline routes; `/pipeline` remains the single task page
- **View files** — all 11 `.vue` views exist and are non-empty
- **Project structure** — `.env.development`, `design-system.css`, `main.css`, `App.vue`, `README.md` all present
- **Build artifacts** — `dist/` exists after `npm run build`

2026-08-01 verification: `npm run test` passed all 128 smoke checks, focused LLM
component tests passed 8/8, `npm run type-check` passed, and `npm run build` completed
successfully. Authenticated desktop and 390px browser checks loaded 1 Provider and all
5 routes from the real API, kept the Secret editor blank, and found no horizontal
overflow or console errors.

## Tech stack

- Vue 3.5 + Pinia 3 + Vue Router 5 + Vue I18n 9
- Element Plus 2 (heavily restyled to match the design system)
- Vite 8 + TypeScript 6 + Vue-tsc 3
- Node 24 (uses built-in TS support for the smoke test)
- OKLch colors throughout (modern, perceptually uniform)

## What's NOT included (next steps)

- ❌ Full end-to-end coverage for every console page (LLM management has focused real-backend and browser acceptance)
- ❌ E2E tests (Playwright not installed — add when backend is wired up)
- ❌ Component unit tests for individual views (only smoke coverage for now)
- ❌ Visual regression tests
- ❌ CI pipeline

## Known limitations

- **Vite 8 native binding on Windows** — `npm run dev` and `npm run build` need the `rolldown-win32-x64-msvc.node` native module. If your `npm install` skipped optional dependencies, run `npm rebuild rolldown` to fetch it.
- Search box in topbar is visual-only (no global search handler wired up)
- Login in mock mode accepts any credentials — disable `VITE_USE_MOCK` before deploying
- No persistence beyond `localStorage` (token, username, locale)

## IDE setup

[VS Code](https://code.visualstudio.com/) + [Vue (Official)](https://marketplace.visualstudio.com/items?itemName=Vue.volar) (and disable Vetur).

## Customize configuration

See [Vite Configuration Reference](https://vite.dev/config/).
