# 机审不一致率跳转优化 PRD v1.2

> **文档版本**：v1.2 | **日期**：2026-05-21 | **审查完善**：小B

---

## 版本记录

| 版本 | 日期 | 变更摘要 |
|------|------|---------|
| v1.1 | 2026-05-21 | 初版 |
| v1.2 | 2026-05-21 | 补充：当前代码状态分析、实施步骤、验收标准回归；确认后端已过滤 review_status，仅缺前端筛选+跳转参数 |

---

## 1. 背景

数据看板的"机审不一致率"卡片，点击后跳转到机审任务中心。但存在两个问题：

1. 卡片数字可能包含了未互检的批次
2. 跳转后展示的是全部批次，而非仅已互检的批次

用户期望：卡片数字和跳转结果必须对应，都只针对已完成互检的数据。

---

## 2. 当前代码状态（v1.2 新增）

| 检查项 | 状态 | 位置 |
|--------|------|------|
| 不一致率 API 已过滤 review_status | ✅ | `dashboard.py:484` `FetchLog.review_status == 'completed'` |
| 看板卡片有点击事件 | ✅ | `dashboard.html:299` `onclick="jumpToModelTask()"` |
| 跳转函数存在 | ✅ | `dashboard.html:1516` `window.jumpToModelTask` |
| 跳转 URL 含 review_status 参数 | ❌ 缺失 | 当前只传 start_date/end_date |
| model_task 有互检状态筛选下拉框 | ❌ 缺失 | 当前筛选栏无此选项 |
| model_task 读取 URL 参数筛选 | ❌ 缺失 | 未解析 review_status |
| 批次列表 API 支持 review_status 筛选 | ❌ 缺失 | data_fetch.py 任务列表接口未接受此参数 |

---

## 3. 需求描述

### 3.1 卡片数据来源

"机审不一致率"卡片数字必须只统计已完成互检的批次。

| 条件 | 是否计入 |
|------|---------|
| review_status = 'completed' | ✅ 计入 |
| review_status != 'completed'（未互检/互检中） | ❌ 不计入 |
| 无已互检数据时 | 卡片显示"暂无互检数据"，不显示 0% |

### 3.2 跳转行为

| 项目 | 说明 |
|------|------|
| 触发 | 数据看板"机审不一致率"卡片点击 |
| 当前行为 | 跳转到机审任务中心，展示全部批次 |
| 期望行为 | 跳转后只展示已互检的批次，筛选栏默认选中"已互检" |

---

## 4. 改动范围

### 4.1 看板卡片数据

**文件**：`blueprints/dashboard.py`

> ✅ 已实现：`api_inconsistency_rate()` 行484 已过滤 `review_status == 'completed'`。无需改动。

### 4.2 看板跳转 URL

**文件**：`templates/dashboard.html` `jumpToModelTask()` 函数（行1516）

追加参数 `review_status=completed`：

```javascript
var params = '?start_date=' + startDate + '&end_date=' + endDate + '&review_status=completed';
```

### 4.3 机审中心增加筛选

**文件**：`templates/model_task.html`

**筛选栏**增加"互检状态"下拉框：

| 选项 | 值 |
|------|-----|
| 全部（默认） | `all` |
| 已互检 | `completed` |
| 未互检 | `pending` |

**页面加载时**读取 URL 参数：若 `review_status=completed`，默认选中"已互检"并自动触发筛选。

### 4.4 后端支持筛选

**文件**：`blueprints/data_fetch.py` 批次列表接口

增加可选参数 `review_status`：

| 参数值 | 查询条件 |
|--------|---------|
| `completed` | `review_status = 'completed'` |
| `pending` | `review_status != 'completed'` |
| 不传或 `all` | 不筛选 |

---

## 5. 实施步骤（v1.2 新增）

| 步骤 | 操作 | 文件 | 风险 |
|------|------|------|------|
| 1 | jumpToModelTask() 参数加 `&review_status=completed` | `dashboard.html:1527` | 低 |
| 2 | data_fetch.py 批次列表 API 加 `review_status` query param | `data_fetch.py` | 低 |
| 3 | model_task.html 筛选栏加"互检状态"下拉框 | `model_task.html` | 中 |
| 4 | model_task.html 页面加载时解析 URL 参数，自动选中筛选 | `model_task.html` | 中 |

---

## 6. 验收标准

### 6.1 功能验收

| # | 验证点 | 预期 |
|---|--------|------|
| AC-1 | 看板"机审不一致率"卡片 | 数字只来自已互检批次 |
| AC-2 | 无已互检数据时 | 卡片显示"暂无互检数据" |
| AC-3 | 有已互检数据时 | 卡片显示正确百分比 |
| AC-4 | 点击卡片跳转 | 机审中心 URL 含 `review_status=completed`，只显示已互检批次 |
| AC-5 | 互检状态筛选框默认选中"已互检" | 跳转后筛选栏自动选中"已互检" |
| AC-6 | 手动切换筛选为"全部" | 显示所有批次 |
| AC-7 | 手动切换筛选为"未互检" | 只显示未互检的批次 |
| AC-8 | 从导航栏直接进入机审中心 | 筛选栏默认"全部" |

### 6.2 回归验收

| # | 验证点 | 预期 |
|---|--------|------|
| REG-1 | 批次列表其他筛选正常 | 审核结果/实例/关键词筛选不受影响 |
| REG-2 | 数据拉取正常 | 弹窗拉取不受影响 |
