# 企业评估模块交互重构设计

**日期：** 2026-06-18
**状态：** 已确认
**范围：** 前端 AssessView + 后端 /api/v1/risk/assess

---

## 1. 当前问题

| 问题 | 说明 |
|------|------|
| 评估/刷新按钮分裂 | 两个按钮调同一接口仅 force 参数不同，用户困惑 |
| force_refresh 阻塞 | 刷新等待几十秒，超时可能报 504 |
| 刷新按钮按需消失 | 只在 is_stale 时显示，用户想主动刷新找不到 |
| 输入框与 URL 不同步 | 浮动面板选中不更新 URL |
| 联想下拉 setTimeout hack | onBlur 150ms 延迟不可靠 |
| 状态过载 | loading / refreshing / error / is_stale / cache_age_hours 杂糅 |

## 2. 设计目标

- **一个"评估"按钮**，两种路径：有缓存秒出 + 后台静默刷新，无缓存等待
- **输入框**：改动不自动评估，保留上次结果
- **URL 同步**：所有入口（侧边栏 / 浮动面板 / 手动输入）统一更新 URL
- **状态简化**：前端去掉 force_refresh 概念
- **超时不报错**：force_refresh 改为后台异步

## 3. 交互设计

### 3.1 搜索区

```
┌─────────────────────────────────────────────┐
│  🔍 海康威视                          [评估] │
│  ⏱ 更新于 3 小时前  [刷新]                  │
└─────────────────────────────────────────────┘
```

- 输入框改动 → 不主动评估，保留上一次评估结果
- Enter 或点击"评估" → 触发查询
- 联想下拉：输入时从监控清单本地过滤，点击选中 → 自动评估
- 数据新鲜 (<2h)：显示"更新于 X 分钟前"（灰色）
- 数据过期 (>2h)：显示"更新于 X 小时前 [刷新]"（Amber 色）
- 点击"刷新"：立即返回缓存数据 + 后台异步拉新 + 完成后自动替换

### 3.2 浮动面板

- 点击企业 → 更新 URL `/assess/{name}` + 填入输入框 + 触发评估
- 保持一致：输入框 = URL = 评估结果

### 3.3 联想下拉

- 用 onMouseDown + preventDefault 替代 setTimeout hack
- 输入为空时显示完整监控清单

## 4. 后端改造

### 4.1 force_refresh 异步化

```python
# /api/v1/risk/assess

if request.force_refresh and recent:
    # 后台异步执行，立即返回缓存
    background_tasks.add_task(_refresh_and_save, request.company_name)
    return resp

# force_refresh 无缓存时，等待新数据
if request.force_refresh and not recent:
    fresh = await asyncio.wait_for(
        asyncio.to_thread(assess_risk, request),
        timeout=120,
    )
    return fresh
```

### 4.2 去掉的错误路径

- force_refresh 有缓存时不再阻塞等待 → 永不超时
- force_refresh 无缓存时保留 120s 超时 → 仍可能 504（极少数情况）

## 5. 前端状态简化

### 状态变量

```typescript
data: RiskResult | null        // 当前展示的数据
meta: {                        // 缓存元信息
  updatedAt: string            // ISO 时间戳
  isStale: boolean             // 是否过期 (>2h)
} | null
querying: 'idle' | 'fresh' | 'background'
// idle = 空闲
// fresh = 全新评估中（无缓存，需等待）
// background = 后台刷新中（不影响当前数据展示）
```

### 去掉的状态

- `force_refresh` 参数
- `refreshing` 单独布尔值
- `error` 字符串（改用 `data === null && querying === 'idle'` 判断失败）

### 按钮表现

| 场景 | 按钮文字 | 状态 |
|------|------|------|
| 空闲 + 无数据 | "评估" | 可点击 |
| 空闲 + 有数据 | "评估" | 可点击 |
| 全新评估中 | "评估中…" | disabled + spinner |
| 后台刷新中 | "评估" | 可点击（新查询打断旧刷新） |

## 6. 验证方式

- 输入"海康威视" → 点评估 → 有缓存秒出 → 显示"更新于 X 分钟前"
- 数据过期 → 显示 Amber "更新于 X 小时前 [刷新]" → 点刷新 → 数据不变，X 分钟后自动更新
- 浮动面板选企业 → URL 变为 `/assess/企业名`
- 手动改输入框 → 评估结果不变 → 按 Enter 重新评估
- 首次查询无缓存 → 显示 loading → 超时 120s → 报错（仅此情况）
