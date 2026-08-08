# 04 — Plugin 层详解

> 关联: [索引](00-索引.md) | [Core层](03-Core层.md) | [Pipeline](05-Pipeline.md)
> 最后更新: 2026-08-02

三类插件：Collector（搜集）、Channel（触达）、Filter（筛选）。

## 4.1 Collector 搜集插件

| 插件 | 类名 | source 字段 | 策略 |
| --- | --- | --- | --- |
| 关键词搜索 | `KeywordCollector` | `keyword_search` / 结构化证据 | 普通用户搜索；获客模式 Hermes 视频/评论优先、用户搜索辅助 |
| 推荐流 | `RecommendationCollector` | `recommendation` | 用户主页推荐区 |
| 竞品分析 | `CompetitorCollector` | `competitor` | 竞品粉丝/关注列表 |

**统一返回格式：**
```python
[{"tiktok_id": "...", "username": "...", "bio": "...",
  "follower_count": 0, "source": "keyword_search", "source_keyword": "wholesale"}]
```

**文件位置：** `tiktok_bot_core/plugins/collectors/{keyword,recommendation,competitor}_collector.py`

`KeywordCollector` 有两条兼容路径：普通配置保持旧的直接用户搜索返回格式；带
`acquisition_mode=true` 时必须注入 `HermesEvidenceAgent`，共用同一个预算 Tracker，
依次执行视频/评论取证和直接用户辅助。生产 DOM 查询使用 `query_all_limited()`，DOM
产生的 URL 在导航前按 TikTok/抖音域名白名单校验。Collector 回传由受限能力生成的
pages/LLM/duration/visited-video 权威指标；缺失或不一致时 Pipeline 整批 fail closed。

**添加新 Collector：**
```python
class MyCollector(CollectorPlugin):
    name = "my_source"
    async def collect(self, config: dict) -> list[dict]:
        return [...]

register().register_collector(MyCollector())
```

## 4.2 Channel 触达插件

| 插件 | channel_type | execute(target, content) |
| --- | --- | --- |
| `CommentChannel` | `comment` | 在用户最新视频下发评论 |
| `DMChannel` | `dm` | 通过 Message 按钮发私信 |

**执行流程：**
1. 导航到目标页面
2. 查找目标元素（CSS 选择器 `[data-e2e="..."]`）
3. 模拟人工操作（延迟 + 行为穿插）
4. 返回成功/失败

**文件位置：** `tiktok_bot_core/plugins/channels/{comment,dm}_channel.py`

**添加新 Channel：**
```python
class FollowChannel(ChannelPlugin):
    name = "follow"
    channel_type = "follow"
    async def execute(self, target, content, config) -> bool:
        browser = await get_browser()
        await browser.click('[data-e2e="follow-btn"]')
        return True
```

## 4.3 Filter 筛选插件

| 插件 | 策略 | LLM 成本 |
| --- | --- | --- |
| `KeywordPreFilter` | bio 命中商业关键词 | 零 |
| `LLMFilter` | 调用 DeepSeek 判断 | 高 |
| `CompositeFilter` | 先预筛再 LLM | 中（推荐） |

**预筛关键词：**
```python
COMMERCIAL_KEYWORDS = [
    "importer", "wholesaler", "distributor", "retailer",
    "brand", "supplier", "manufacturer", "factory",
    "export", "import", "trade", "supply",
]
```

**返回格式：**
```python
{"score": 0.85, "category": "buyer", "reason": "...", "is_potential": True}
```

**文件位置：** `tiktok_bot_core/plugins/filters/{llm,composite}_filter.py`
