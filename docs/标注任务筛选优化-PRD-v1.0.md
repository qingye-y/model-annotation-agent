# 标注任务筛选优化 — PRD & 技术方案 v1.0

版本：v1.0 | 日期：2026-05-26 | 状态：**待开发**

---

## 一、背景

当前标注列表页的筛选项存在以下问题：

1. **部分筛选项仅在客户端过滤**，数据量大时筛选不准，翻页后失效
2. **存在僵尸筛选**：`filterAnnotateNote` 有 DOM 但无逻辑
3. **审核规则和实例为下拉选择**，无法直观看到全部选项，且无联动关系
4. **标注员缺少批次号筛选**，无法快速定位具体批次
5. **明细页带有"审核规则"筛选**，但每个任务已限定规则，该筛选多余

**本次优化目标**：统一走后端过滤、简化列表页筛选、增强联动、补齐缺失维度。

---

## 二、需求范围

| 页面 | 是否改动 |
|------|---------|
| 标注列表页（任务分组视图 + 商品明细视图） | ✅ 改动 |
| 批次详情页（batch_detail.html） | ❌ 不改动 |

---

## 三、功能详情

### 3.1 标注列表页 — 筛选改造

#### 3.1.1 筛选项最终清单

| 筛选项 | 组件类型 | 选项/说明 |
|--------|---------|---------|
| 审核规则 | 标签按钮组（平铺） | 全部 / 动态规则列表（来自 instance→rule 反向映射） |
| 实例 | 标签按钮组（平铺） | 全部 / 动态实例列表（显示中文名，如"浙江网超"） |
| 标注状态 | 下拉选择 | 全部 / 待标注 / 正确 / 错误 / 忽略（映射值：`''` / `pending` / `correct` / `error` / `ignore`） |
| 批次号 | 文本输入框 | 精确匹配，占位提示"输入批次号" |

#### 3.1.2 审核规则 ↔ 实例联动规则

**点击规则标签**（例如"浙江网超审核规则"）：
- 该标签高亮，其余规则标签取消高亮
- 实例标签组立即过滤：只展示该规则对应的实例（来自 instance→rule 反向查找）
- 若当前已选中的实例不在过滤后的集合中，自动将实例重置为"全部"

**点击实例标签**（例如"浙江网超"）：
- 该标签高亮，其余实例标签取消高亮
- 规则标签组立即过滤：只展示该实例对应的规则
- 若当前已选中的规则不在过滤后的集合中，自动将规则重置为"全部"

**点击"全部"标签**：恢复显示所有选项，两个维度均重置。

**输入批次号后回车**：自动触发数据重新加载。

#### 3.1.3 移除项

- 删除"AI审核结果"下拉筛选（列表页不再需要）
- 删除"关键词搜索"输入框（移到明细页）
- 删除"更多筛选"展开按钮及隐藏区域（`toggleMoreFilters` 相关代码全部移除）
- 删除僵尸筛选 `filterAnnotateNote`

---

### 3.2 标注明细页 — 筛选改造

#### 3.2.1 筛选项清单

**外露筛选项（常驻）**：

| 筛选项 | 组件 | 选项 |
|--------|------|------|
| AI审核结果 | 下拉 | 全部 / 合规 / 违规 |
| 标注状态 | 下拉 | 全部 / 待标注 / 正确 / 错误 / 忽略 |
| 实例 | 下拉 | 全部 / 动态（中文名） |
| 标注人 | 下拉 | 全部 / 动态（从标注员列表获取） |
| 关键词搜索 | 文本输入 | 商品名称或ID模糊匹配 |

**隐藏筛选项（"更多筛选"展开）**：

| 筛选项 | 组件 | 选项 |
|--------|------|------|
| 类目 | 下拉 | 全部 / 动态（从数据中提取） |

#### 3.2.2 移除项

- 删除"审核规则"筛选（因为明细页已处于特定任务上下文，规则唯一）

#### 3.2.3 从列表页跳转联动

从任务分组视图点击进入明细页时，URL 携带参数：

```
/annotation-list?view=detail&dispatch_batch_no=xxx&instance=xxx
```

明细页初始化时自动读取这些参数，设置对应筛选项并触发查询。标注员可手动修改筛选。

---

## 四、验收标准

| # | 验收项 | 预期 |
|---|--------|------|
| AC-1 | 列表页规则/实例标签显示 | 标签平铺，实例显示中文名 |
| AC-2 | 点击规则标签 | 实例标签过滤为关联实例，数据刷新 |
| AC-3 | 点击实例标签 | 规则标签过滤为关联规则，数据刷新 |
| AC-4 | 选择规则+实例后切换"全部" | 另一个维度恢复全部选项 |
| AC-5 | 输入批次号 | 数据精确过滤 |
| AC-6 | 标注状态下拉 | 选项为全部/待标注/正确/错误/忽略，筛选准确 |
| AC-7 | 明细页无规则筛选 | 筛选栏不显示审核规则 |
| AC-8 | 明细页支持标注人、关键词筛选 | 选择标注人或输入关键词，数据准确过滤 |
| AC-9 | 列表页点击分组跳转明细 | 自动带上 instance 和 dispatch_batch_no 参数，明细页筛选项默认选中 |

---

## 五、技术方案

### 5.1 后端改造

**文件**：`blueprints/dispatch.py`

#### 5.1.1 `api_my_tasks` 扩展参数

在现有 `page/per_page` 解析后追加（位置：行 693 之后）：

```python
# dispatch_batch_no：批次号精确匹配（已在行 691 定义，此处补充逻辑）
# check_result：标注结果（correct/error/ignore），映射到数据库中文值
check_result = request.args.get('check_result', '').strip()
if check_result == 'correct':
    base.append(RawData.check_result == '正确')
elif check_result == 'error':
    base.append(RawData.check_result == '错误')
elif check_result == 'ignore':
    base.append(RawData.check_result == '忽略')
# 注意：check_result 有值时，跳过原有的 status 逻辑（见 5.1.2）

# ai_result：AI审核结果（合规/违规）
ai_result = request.args.get('ai_result', '').strip()
if ai_result == '合规':
    base.append(RawData.ai_result.in_(['合规', '1', 'PASS']))
elif ai_result == '违规':
    base.append(RawData.ai_result.in_(['违规', '0', 'REJECT']))

# annotator：标注人精确匹配
annotator = request.args.get('annotator', '').strip()
if annotator:
    base.append(RawData.annotator == annotator)

# keyword：商品名称或ID模糊搜索
keyword = request.args.get('keyword', '').strip()
if keyword:
    from sqlalchemy import or_
    base.append(or_(
        RawData.product_name.contains(keyword),
        RawData.product_id.contains(keyword)
    ))
```

#### 5.1.2 原有 `status` 参数兼容

原有 `status` 参数（`pending`/`done`）保留不变，但 `check_result` 参数有值时应跳过（避免冲突）。

#### 5.1.3 `api_my_task_groups` 扩展参数

同上，在行 821 附近追加 `dispatch_batch_no`、`check_result` 筛选逻辑（用于批次号搜索和标注状态过滤）。

### 5.2 前端改造

#### 5.2.1 标注列表页 HTML 结构

在原有筛选区域替换为：

```html
<!-- 规则标签行 -->
<div class="filter-tags-row" id="ruleTagsRow">
  <span class="filter-tag-label">规则：</span>
  <div class="filter-tags" id="ruleTags"></div>
</div>
<!-- 实例标签行 -->
<div class="filter-tags-row" id="instanceTagsRow">
  <span class="filter-tag-label">实例：</span>
  <div class="filter-tags" id="instanceTags"></div>
</div>
<!-- 状态 + 批次号 -->
<div style="display:flex; align-items:center; gap:16px; margin-bottom:12px;">
  <div class="filter-item">
    <select id="tgFilterStatus" onchange="onFilterChange()">
      <option value="">全部状态</option>
      <option value="pending">待标注</option>
      <option value="correct">正确</option>
      <option value="error">错误</option>
      <option value="ignore">忽略</option>
    </select>
  </div>
  <div class="filter-item">
    <input type="text" id="tgFilterBatchNo" placeholder="输入批次号"
           onkeydown="if(event.key==='Enter')onFilterChange()" style="width:180px;">
  </div>
</div>
```

#### 5.2.2 JS 核心逻辑

```javascript
// 全局状态
let ruleInstanceMap = {};      // {rule_name: [instance_code, ...]}
let instanceNameMap = {};      // {instance_code: "中文名"}
let selectedRule = '';
let selectedInstance = '';

async function loadFilterMeta() {
  // 从后端获取 instance→rule 映射
  const resp = await fetch('/api/config/instance-rule-mapping');
  const data = await resp.json();
  // data.mapping: {instance_code: rule_name}
  const mapping = data.mapping || {};
  // 构建反向映射
  ruleInstanceMap = {};
  instanceNameMap = {};
  for (var inst in mapping) {
    var rule = mapping[inst];
    if (!ruleInstanceMap[rule]) ruleInstanceMap[rule] = [];
    ruleInstanceMap[rule].push(inst);
    instanceNameMap[inst] = data.instance_names ? data.instance_names[inst] : inst;
  }
  renderRuleTags();
  renderInstanceTags();
}

function renderRuleTags(filterInstance = '') {
  var container = document.getElementById('ruleTags');
  container.innerHTML = '<button class="filter-tag ' + (selectedRule === '' ? 'active' : '') + '" data-rule="">全部</button>';
  var allRules = Object.keys(ruleInstanceMap);
  allRules.forEach(function(rule) {
    var btn = document.createElement('button');
    btn.className = 'filter-tag' + (selectedRule === rule ? ' active' : '');
    btn.dataset.rule = rule;
    btn.textContent = rule;
    btn.onclick = function() { selectRule(rule); };
    container.appendChild(btn);
  });
}

function selectRule(rule) {
  selectedRule = rule;
  document.querySelectorAll('#ruleTags .filter-tag').forEach(function(b) { b.classList.remove('active'); });
  var btn = document.querySelector('#ruleTags [data-rule="' + rule + '"]');
  if (btn) btn.classList.add('active');
  renderInstanceTags(rule);
  if (selectedInstance && rule && ruleInstanceMap[rule] && !ruleInstanceMap[rule].includes(selectedInstance)) {
    selectedInstance = '';
  }
  onFilterChange();
}

function renderInstanceTags(filterRule = '') {
  var container = document.getElementById('instanceTags');
  container.innerHTML = '<button class="filter-tag ' + (selectedInstance === '' ? 'active' : '') + '" data-instance="">全部</button>';
  var instances = Object.keys(instanceNameMap);
  if (filterRule && ruleInstanceMap[filterRule]) {
    instances = ruleInstanceMap[filterRule];
  }
  instances.forEach(function(code) {
    var btn = document.createElement('button');
    btn.className = 'filter-tag' + (selectedInstance === code ? ' active' : '');
    btn.dataset.instance = code;
    btn.textContent = instanceNameMap[code] || code;
    btn.onclick = function() { selectInstance(code); };
    container.appendChild(btn);
  });
}

function selectInstance(code) {
  selectedInstance = code;
  document.querySelectorAll('#instanceTags .filter-tag').forEach(function(b) { b.classList.remove('active'); });
  var btn = document.querySelector('#instanceTags [data-instance="' + code + '"]');
  if (btn) btn.classList.add('active');
  // 过滤规则
  var relatedRules = [];
  if (code) {
    for (var r in ruleInstanceMap) {
      if (ruleInstanceMap[r].includes(code)) relatedRules.push(r);
    }
  }
  renderRuleTagsForInstance(code, relatedRules);
  onFilterChange();
}

function renderRuleTagsForInstance(instance, allowedRules) {
  var container = document.getElementById('ruleTags');
  var allRules = Object.keys(ruleInstanceMap);
  container.innerHTML = '<button class="filter-tag ' + (selectedRule === '' ? 'active' : '') + '" data-rule="">全部</button>';
  var displayRules = instance ? allowedRules : allRules;
  displayRules.forEach(function(rule) {
    var btn = document.createElement('button');
    btn.className = 'filter-tag' + (selectedRule === rule ? ' active' : '');
    btn.dataset.rule = rule;
    btn.textContent = rule;
    btn.onclick = function() { selectRule(rule); };
    container.appendChild(btn);
  });
}

function onFilterChange() {
  var params = {
    rule_name: selectedRule,
    instance: selectedInstance,
    check_result: (document.getElementById('tgFilterStatus') || {}).value || '',
    dispatch_batch_no: (document.getElementById('tgFilterBatchNo') || {}).value.trim() || ''
  };
  loadTaskGroups(1, params);
}
```

#### 5.2.3 标注明细页调整

- 删除 `filterRule` 下拉及关联 JS
- 修改 `filterAnnotateStatus` 选项为：全部/待标注/正确/错误/忽略
- `fetchTasks` 参数增加：`check_result`、`ai_result`、`annotator`、`keyword`
- 添加 URL 参数读取逻辑（`dispatch_batch_no` 和 `instance`），自动设置筛选并首次加载

```javascript
// 页面初始化时读取 URL 参数
(function() {
  var urlParams = new URLSearchParams(window.location.search);
  var batchNo = urlParams.get('dispatch_batch_no');
  var instance = urlParams.get('instance');
  if (batchNo) {
    var el = document.getElementById('tgFilterBatchNo');
    if (el) el.value = batchNo;
  }
  if (instance) {
    var el2 = document.getElementById('filterInstance');
    if (el2) el2.value = instance;
  }
})();
```

### 5.3 样式补充

```css
.filter-tags-row {
  display: flex;
  align-items: center;
  margin-bottom: 12px;
}
.filter-tag-label {
  font-size: 13px;
  font-weight: 500;
  color: #555;
  margin-right: 8px;
  white-space: nowrap;
}
.filter-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.filter-tag {
  padding: 4px 12px;
  border: 1px solid #d0d0d0;
  border-radius: 16px;
  background: #fff;
  color: #555;
  font-size: 13px;
  cursor: pointer;
  transition: all 0.2s;
}
.filter-tag:hover {
  border-color: var(--primary);
  color: var(--primary);
}
.filter-tag.active {
  background: var(--primary);
  border-color: var(--primary);
  color: #fff;
}
```

---

## 六、执行步骤

### Step 1：后端接口扩展

修改 `blueprints/dispatch.py`：
- `api_my_tasks`：追加 `check_result`、`ai_result`、`annotator`、`keyword` 筛选逻辑
- `api_my_task_groups`：追加 `dispatch_batch_no` 和 `check_result` 参数处理

### Step 2：前端列表页改造

修改 `templates/annotation_list.html`：
1. 删除原 `tgFilterRule`/`tgFilterInstance` 下拉及"更多筛选"按钮/区域
2. 添加规则标签行、实例标签行、标注状态下拉、批次号输入框
3. 实现 `loadFilterMeta`/`renderRuleTags`/`selectRule`/`renderInstanceTags`/`selectInstance`/`onFilterChange`
4. 删除 `toggleMoreFilters` 及相关引用

### Step 3：前端明细页改造

同一文件：
1. 删除 `filterRule` 下拉控件及处理逻辑
2. 修改 `filterAnnotateStatus` 选项
3. `fetchTasks` 增加 `check_result`/`ai_result`/`annotator`/`keyword` 参数
4. 添加 URL 参数读取，自动设置筛选

### Step 4：CSS 补充

`static/common.css` 追加标签样式

### Step 5：联调验证

重启服务，逐一验证 AC-1 ~ AC-9。

---

## 七、不改动范围

- 批次详情页（`batch_detail.html`）的任何筛选逻辑
- 标注详情弹窗的图片展示和商品链接功能
- 标注操作的判定流程和键盘快捷键

---

## 八、版本记录

| 版本 | 日期 | 修改内容 | 状态 |
|------|------|---------|------|
| v1.0 | 2026-05-26 | 初始版本，定义完整需求和技术方案 | 待开发 |
