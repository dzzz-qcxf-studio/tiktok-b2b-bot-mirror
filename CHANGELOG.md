# Changelog

All notable changes to TikTok B2B Bot.

## [0.1.0] — 2026-07-05

### Added
- **Core layer**: SQLAlchemy ORM (7 entities), SQLite store, ChromaDB vector store, async event bus, plugin registry
- **Plugin system**: 3 Collectors (keyword/recommendation/competitor), 2 Channels (comment/DM), 2 Filters (keyword pre-filter + LLM)
- **Pipeline service**: 6-stage orchestration (collect → filter → strategy → outreach → report → iterate)
- **CLI (Click)**: 7 command groups — user, pipeline, strategy, report, config, status, init
- **REST API (FastAPI)**: 22 routes — users, pipeline, reports, stats, config, TikTok accounts, events (SSE)
- **Web UI (Vue 3)**: Dark sidebar layout, Dashboard (metrics + trend chart + pie + keyword ranking), Users table, Pipeline runner, Reports, Config with tabs
- **Authentication**: Username/password (SHA256 salted hash) + API Key dual login, JWT tokens, router guard
- **i18n**: Chinese (zh-CN) + English (en-US), persisted language preference
- **TikTok account management**: Database table, API CRUD, sidebar status indicators
- **Docker Compose**: Multi-stage build (Node UI builder + Python runtime + Nginx)
- **Hermes Skills**: 4 thin CLI wrappers (pipeline/users/reports/config)

### Changed
- Config page redesigned with tab layout (LLM / TikTok / Pipeline) inspired by cc-switch
- Dashboard redesigned with premium metrics layout: dark primary card + 3 secondary cards + period selector

### Design
- Design system: Neutral palette (N950-N50), Inter font, 6px radius, single shadow level
- Clean table rows with status badges (pending/qualified/rejected/contacted/replied)
- Pure SVG icons in sidebar, minimal chrome

## [0.1.1] — 2026-07-19

### Fixed
- **TikTok/Douyin QR login — QR tab not clicked**:
  TikTok and Douyin login pages no longer auto-show the QR code. Added `login_tab_qrcode` click logic that switches to QR code tab before waiting for the QR element to render. The login dialog is also detected and manually triggered if it doesn't auto-popup.
- **QR code selectors outdated**:
  Updated TikTok QR selectors from generic `canvas[class*="qrcode"]` to include modern selectors (`[data-e2e*="qrcode-login"]`, `[class*="qrcode-tab"]`, etc.). Added `login_dialog` selector for login panel detection.
- **Accounts API missing frontend fields**:
  `list_accounts()` now returns `nickname`, `followers`, `videos`, `likes`, `today` (activity counters), and `statusKey` fields the frontend Account interface expects — preventing display glitches in real backend mode.
- **Browser resource leak in background login task**:
  `_qrcode_login_task` now properly initializes `browser` and `playwright` references to `None` and always closes them in `finally` block with null checks, preventing orphan browser processes.
- **QR login logging too quiet**:
  Upgraded debug logs to `INFO` level for QR element detection, polling progress (every 30s), and page state diagnostics (URL, title, screenshot metadata).
- **Frontend error propagation**:
  `QRScanModal.vue` now captures and displays `error` field from backend `check_login()` response and Axios error details to help users diagnose login failures.
- **Retry logic for QR element detection**:
  Added 4-retry loop with QR tab re-click fallback for TikTok/Douyin pages that are slow to render the QR code. Increased QR wait timeout from 15s to 20s with per-attempt 5s windows.

## [0.1.2] — 2026-07-19

### Fixed — Douyin QR Login

- **Douyin QR login — no interface appears (critical)**:
  Root cause: Douyin login page uses `<p>登录</p>` not `<button>登录</button>`. The old selector `button:has-text("登录")` never matched, so the login dialog never opened. Added `xpath=//p[text()="登录"]` as the primary login button selector (per MediaCrawler reference). Added `login_btn` selector to both `DOUYIN` and `TIKTOK` platform configs.

- **Douyin login detection incomplete**:
  Added `LOGIN_STATUS` cookie marker for Douyin (MediaCrawler checks `LOGIN_STATUS=1`). Added localStorage check for `HasUserLogin=1` (Douyin sets this in addition to cookies). Now uses `_is_logged_in()` with dual cookie + localStorage detection.

- **Frontend shows nothing while browser launches**:
  Added `launching` status (8s window) to `check_login()` so the frontend shows "正在启动浏览器，请稍候..." with a spinning indicator instead of a blank screen. Added `qrStatusLaunching` i18n key to zh-CN and en-US.

### Changed — Auth Service Refactor

- **Refactored `_qrcode_login_task`** into focused helper methods to reduce cognitive complexity:

  - `_launch_browser()` — Playwright setup with anti-detection
  - `_ensure_login_dialog()` — dialog detection + manual trigger
  - `_click_login_button()` — try each login button selector
  - `_get_login_btn_selectors()` — platform-aware selector list
  - `_switch_to_qr_tab()` — QR tab click
  - `_wait_for_qrcode()` — 4-retry QR element wait
  - `_check_login_cookies()` / `_check_login_local_storage()` / `_is_logged_in()` — login detection
  - `_save_login_cookies()` — persist cookies to DB
  - `_poll_login_status()` — 5-minute polling loop
  - `_cleanup_browser()` — resource cleanup
