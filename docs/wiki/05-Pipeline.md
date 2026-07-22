# 05 — Pipeline 编排

> 关联: [索引](00-索引.md) | [Plugin层](04-Plugin层.md) | [CLI-API-UI](06-CLI-API-UI.md)

## PipelineService (`services/pipeline.py`)

```python
service = PipelineService()

# 运行全部阶段
async for result in service.run():
    print(f"{result['stage']}: {result['status']}")

# 只运行指定阶段
async for result in service.run(stages=["collect", "filter"]):
    ...
```

## 执行流程

```text
PipelineService.__init__()
  ├── _ensure_registered()     # 检查注册表，空则注册默认插件
  ├── bus = get_event_bus()    # 事件总线
  ├── db = get_db()            # 数据库
  └── store, vector            # CRUD + 向量

async for result in service.run(stages):
  │
  ├── 阶段1 _run_collect:
  │   ├── reg.get_collector("keyword").collect(keywords)
  │   ├── 写入 SQLite (store.add_user)
  │   └── 写入 ChromaDB (vector.add_user_profile)
  │
  ├── 阶段2 _run_filter:
  │   ├── 查询 pending 用户
  │   ├── KeywordPreFilter.evaluate()   # 免费预筛
  │   ├── LLMFilter.evaluate()          # LLM 精筛
  │   └── update_user_status → qualified/rejected
  │
  ├── 阶段3 _run_strategy:
  │   ├── 查询 qualified 用户
  │   ├── llm.json_completion(prompt)   # DeepSeek 生成策略
  │   └── store.add_strategy()          # 写入 strategies 表
  │
  ├── 阶段4 _run_outreach:
  │   ├── JOIN strategies × users (priority ASC)
  │   ├── CommentChannel.execute(target, template)
  │   ├── DMChannel.execute(target, template)
  │   ├── store.add_message()           # 写入 messages 表
  │   └── 反封号: 随机延迟 + 行为穿插
  │
  ├── 阶段5 _run_report:
  │   ├── 统计当日数据 (count_users/messages/replies)
  │   ├── UPSERT daily_reports
  │   └── 推送 Telegram（如果配置了 TOKEN）
  │
  └── 阶段6 _run_iterate:
      ├── store.get_keyword_effectiveness()
      ├── llm.json_completion(analysis prompt)
      ├── vector.add_experience()        # ChromaDB 经验记忆
      └── store.add_rule()               # SQLite 规则表
```

## 事件发布时序

```text
PIPELINE_START
  ├── COLLECT_DONE   → USER_DISCOVERED × N
  ├── FILTER_DONE     → USER_QUALIFIED × N / USER_REJECTED × N
  ├── STRATEGY_DONE
  ├── OUTREACH_DONE   → USER_CONTACTED × N
  ├── REPORT_DONE
  ├── ITERATE_DONE
PIPELINE_END
```

## 错误处理

- 每个阶段独立 try/catch
- 单阶段失败不影响后继阶段
- 错误发布 `ERROR_OCCURRED` 事件
- PipelineState 通过 yield 流式返回
