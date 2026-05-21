# 批次详情页 — 互检筛选 PRD

> **版本**：v1.0 | 日期：2026-05-21 | 编写：小B  
> 状态：**待确认**

---

## 一、背景

机审任务中心 → 批次详情页（`/batch-detail/<batch_id>`）的筛选栏目前只有3项：

| 已有筛选项 | 值 |
|-----------|-----|
| 审核结果 | 全部 / 合规 / 违规 |
| 实例 | 全部 / ZJWC / HWCS / ... |
| 关键词 | 自由文本 |

**缺两项刚需**：互检状态、模型A/B差异。用户在互检完成后无法快速过滤出不一致的数据，需要逐行翻找。

---

## 二、需求

在现有筛选栏中新增两个下拉框：

### 2.1 互检状态

| 选项值 | 含义 | 后端对应 |
|--------|------|---------|
| 全部（默认） | 不筛选 | 不加条件 |
| 已互检 | modelB 已处理过的记录 | `modelb_reviewed = True` |
| 未互检 | modelB 尚未处理 | `modelb_reviewed = False` |

### 2.2 AB差异

| 选项值 | 含义 | 后端对应 |
|--------|------|---------|
| 全部（默认） | 不筛选 | 不加条件 |
| 一致 | A和B判定相同 | `modelb_consistent = True` |
| A合规B违规 | AI放行但模型B驳回 | `ai_result='合规' AND modelb_result='违规'` |
| A违规B合规 | AI驳回但模型B放行 | `ai_result='违规' AND modelb_result='合规'` |

> 「全部」= 一致 + A合规B违规 + A违规B合规。不再设独立的「不一致」选项，需要看所有不一致时直接不筛选即可看到全貌。

### 2.3 模型B审核结果

| 选项值 | 含义 | 后端对应 |
|--------|------|---------|
| 全部（默认） | 不筛选 | 不加条件 |
| 合规 | 模型B判定为合规 | `modelb_result IN ('合规', '1', 'PASS')` |
| 违规 | 模型B判定为违规 | `modelb_result IN ('违规', '0', 'REJECT')` |

> 仅对已互检数据生效。未互检数据 modelb_result 为空，选合规/违规后不显示。

---

## 三、改造范围

### 3.1 后端（`blueprints/data_fetch.py`）

`GET /api/task-batches/<batch_id>/items` 新增两个 query param：

| 参数 | 取值 | 说明 |
|------|------|------|
| `review_status` | `reviewed` / `not_reviewed` | 互检状态 |
| `diff_status` | `consistent` / `a_pass_b_reject` / `a_reject_b_pass` | AB差异 |
| `modelb_result` | `合规` / `违规` | 模型B审核结果 |

在行644-657的筛选段追加逻辑：

```python
# 互检状态筛选（在现有筛选后追加）
review_status = request.args.get('review_status', '')
if review_status == 'reviewed':
    filtered_query = filtered_query.filter(RawData.modelb_reviewed == True)
elif review_status == 'not_reviewed':
    filtered_query = filtered_query.filter(RawData.modelb_reviewed == False)

# AB差异筛选
diff_status = request.args.get('diff_status', '')
if diff_status == 'consistent':
    filtered_query = filtered_query.filter(RawData.modelb_consistent == True)
elif diff_status == 'a_pass_b_reject':
    filtered_query = filtered_query.filter(
        RawData.ai_result.in_(['合规', '1', 'PASS']),
        RawData.modelb_result.in_(['违规', '0', 'REJECT'])
    )
elif diff_status == 'a_reject_b_pass':
    filtered_query = filtered_query.filter(
        RawData.ai_result.in_(['违规', '0', 'REJECT']),
        RawData.modelb_result.in_(['合规', '1', 'PASS'])
    )

# 模型B审核结果筛选
modelb_result = request.args.get('modelb_result', '')
if modelb_result == '合规':
    filtered_query = filtered_query.filter(RawData.modelb_result.in_(['合规', '1', 'PASS']))
elif modelb_result == '违规':
    filtered_query = filtered_query.filter(RawData.modelb_result.in_(['违规', '0', 'REJECT']))
```

### 3.2 前端（`templates/batch_detail.html`）

在行220的「实例」select 之后追加两个筛选下拉框：

```html
<!-- 在 filterInstance 之后追加 -->
<div class="filter-item">
  <label>互检状态</label>
  <select class="form-select" id="filterReviewStatus">
    <option value="">全部</option>
    <option value="reviewed">已互检</option>
    <option value="not_reviewed">未互检</option>
  </select>
</div>
<div class="filter-item">
  <label>AB差异</label>
  <select class="form-select" id="filterDiffStatus">
    <option value="">全部</option>
    <option value="consistent">一致</option>
    <option value="a_pass_b_reject">A合规B违规</option>
    <option value="a_reject_b_pass">A违规B合规</option>
  </select>
</div>
<div class="filter-item">
  <label>模型B结果</label>
  <select class="form-select" id="filterModelbResult">
    <option value="">全部</option>
    <option value="合规">合规</option>
    <option value="违规">违规</option>
  </select>
</div>
```

`loadData()` 函数（行585-596）追加参数：

```javascript
var reviewStatus = document.getElementById('filterReviewStatus').value;
var diffStatus = document.getElementById('filterDiffStatus').value;
var modelbResult = document.getElementById('filterModelbResult').value;
if (reviewStatus) url += '&review_status=' + encodeURIComponent(reviewStatus);
if (diffStatus) url += '&diff_status=' + encodeURIComponent(diffStatus);
if (modelbResult) url += '&modelb_result=' + encodeURIComponent(modelbResult);
```

`resetFilters()` 函数追加三行重置：
```javascript
document.getElementById('filterReviewStatus').value = '';
document.getElementById('filterDiffStatus').value = '';
document.getElementById('filterModelbResult').value = '';
```

---

## 四、验收标准

| # | 验证项 | 如何验证 | 预期 |
|---|--------|---------|------|
| AC-1 | 互检状态筛选显示 | 打开批次详情页 | 筛选栏显示「互检状态」下拉框，三个选项 |
| AC-2 | AB差异筛选显示 | 同上 | 显示「AB差异」下拉框，五个选项 |
| AC-3 | 筛选「已互检」 | 选互检状态=已互检 → 查询 | 只显示 modelb_reviewed=True 的记录 |
| AC-4 | 筛选「未互检」 | 选互检状态=未互检 → 查询 | 只显示 modelb_reviewed=False 的记录 |
| AC-5 | 筛选「A合规B违规」 | 选该项 → 查询 | 只显示 ai_result=合规, modelb_result=违规 的记录 |
| AC-6 | 筛选「A违规B合规」 | 选该项 → 查询 | 只显示 ai_result=违规, modelb_result=合规 的记录 |
| AC-7 | 两个筛选组合 | 互检=已互检 + AB差异=不一致 | 取交集，不报错 |
| AC-8 | 重置恢复 | 点击重置 → 再查询 | 回到全部数据 |
| AC-9 | 与其他筛选共存 | 审核结果=违规 + AB差异=A合规B违规 | 取交集，结果准确 |
| AC-10 | 模型B结果筛选 | 选模型B结果=违规 → 查询 | 只显示 modelb_result=违规 的记录 |

---

## 五、不改动范围

- 不影响互检流程本身（触发/中止/进度查询）
- 不影响导出功能
- 不影响批次概览卡片