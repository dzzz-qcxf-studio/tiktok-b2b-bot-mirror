# 03 — Core 层详解

> 关联: [索引](00-索引.md) | [架构设计](02-架构设计.md) | [Plugin层](04-Plugin层.md)

Core 层是所有业务逻辑的所在，被 CLI/API/UI 三层共享。

## 3.1 数据模型 (`models/entities.py`)

7 个 SQLAlchemy ORM 实体（单文件维护）：

| 实体 | 表名 | 用途 |
| --- | --- | --- |
| `User` | `users` | TikTok 用户信息 + 状态跟踪 |
| `Strategy` | `strategies` | 触达策略（评论/私信模板） |
| `Message` | `messages` | 评论/私信发送记录 |
| `Reply` | `replies` | 用户回复 + 情感分析 |
| `DailyReport` | `daily_reports` | 每日统计快照 |
| `ExperienceRule` | `experience_rules` | 闭环迭代沉淀的规则 |
| `ConfigRecord` | `config_records` | 可通过 Web UI 修改的配置 |

**User 状态机：**

```text
pending → qualified → contacted → replied
         ↘ rejected
```

每次状态变更自动记录 `updated_at`。

## 3.2 存储层 (`storage/`)

**双数据库架构：**

```python
# SQLite — 给人看的
db = get_db()           # 全局 Database 单例
store = SqliteStore()   # CRUD 仓库

# ChromaDB — 给 AI 用的
vector = VectorStore()  # 3 collection: user_profiles / strategies / experience
```

**SqliteStore** 提供:
- User CRUD + 状态更新
- Strategy/Message/Reply CRUD
- DailyReport UPSERT + 列表
- ExperienceRule CRUD
- ConfigRecord 读写
- 聚合统计: `get_keyword_effectiveness()`, `get_category_distribution()`

**VectorStore** 提供:
- `add_user_profile()` — bio → embedding
- `search_similar_users()` — "找一个类似 @alice 的用户"
- `search_similar_strategies()` — "针对这类用户什么话术最有效"
- `search_experience()` — "以前遇到过类似情况吗"

## 3.3 事件总线 (`events/bus.py`)

异步事件总线，解耦 Pipeline 阶段：

```python
bus = get_event_bus()
bus.subscribe(EventType.USER_QUALIFIED, my_handler)
await bus.publish(Event(EventType.USER_QUALIFIED, {"user_id": 42}))
```

**10 种事件类型：**

| 事件 | 触发时机 |
| --- | --- |
| `COLLECT_DONE` → `ITERATE_DONE` | 每个 Pipeline 阶段完成 |
| `USER_DISCOVERED / QUALIFIED / REJECTED / CONTACTED / REPLIED` | 用户状态变更 |
| `PIPELINE_START / PIPELINE_END` | Pipeline 生命周期 |
| `ERROR_OCCURRED` | 错误捕获 |

**特性：** 并发订阅、错误隔离、最近 1000 条历史。

## 3.4 扩展注册器 (`extensions/registry.py`)

替代 ChopperBot META-INF 机制：

```python
reg = get_registry()
reg.register_collector(MyCollector())
reg.register_channel(MyChannel())
reg.get_collector("keyword")      # 按名获取
reg.list_plugins()                # {"collectors": [...], ...}
```

三类插件基类 (ABC)：
- `CollectorPlugin` — `async collect(config) → list[dict]`
- `ChannelPlugin` — `async execute(target, content) → bool`
- `FilterPlugin` — `async evaluate(user) → dict`

## 3.5 配置管理 (`settings.py`)

Pydantic Settings，`DEEPSEEK_API_KEY` 等敏感值从 `.env` 读取：

```python
s = get_settings()
s.llm_api_key       # ${DEEPSEEK_API_KEY}
s.tiktok_keywords   # ["wholesale", "importer"]
s.daily_dm_limit    # 12
```

## 3.6 LLM 抽象 (`llm/client.py`)

```python
llm = get_llm_client()
text = await llm.chat("分析...")
json = await llm.json_completion("返回JSON...")
```

`json_completion()` 自动从 markdown code block 或自然语言提取 JSON。

## 3.7 浏览器封装 (`browser/client.py`)

异步 Playwright，单例管理：

```python
async with BrowserClient() as browser:
    await browser.navigate("https://tiktok.com/@alice")
    await browser.fill("input", "Hello")
    await browser.click("button")
```
