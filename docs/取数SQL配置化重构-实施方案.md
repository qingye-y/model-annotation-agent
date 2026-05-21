# 取数 SQL 配置化重构 — 实施方案

> **最新版本**：v1.4 | 日期：2026-05-21 | 编写：青也 + 小B  
> 状态：**迭代中**（待完成：§8 前端补充 + §10 占位符溯源）  

---

## 版本记录

| 版本 | 日期 | 变更摘要 | 变更人 |
|------|------|---------|--------|
| v1.3 | 2026-05-21 | **新增 §12 验收标准**（含 14 条可执行验证项 + 功能/回归分级） | 青也 |
| v1.2 | 2026-05-21 | 新增 §8 补充需求（4项）、§10 占位符规范化（{detail_sql}→${detail_sql}）、§11 状态确认表 | 青也 |
| v1.1 | 2026-05-21 | 确认实施完成：旧模块删除、FetchPipeline 创建、S2-S5 接入管道 | — |
| v1.0 | 2026-05-20 | 初版方案 | 青也 + 小B |

---

## 版本记录

| 版本 | 日期 | 变更摘要 | 变更人 |
|------|------|---------|--------|
| v1.4 | 2026-05-21 | **重写 §10**：原方案未解决核心问题（用户不知道 `{detail_sql}` 引用哪个模板）。改为显式标注引用来源（params_json 写明 S1 模板名+id）、UI 区分系统注入参数 | 青也 |
| v1.3 | 2026-05-21 | 新增 §12 验收标准（14条功能验收 + 6条回归验收 + 验收流程图 + 验证顺序） | 青也 |
| v1.2 | 2026-05-21 | 新增 §8 补充需求（4项前端缺失） + §10 占位符规范化 + §11 状态确认表 | 青也 |
| v1.1 | 2026-05-21 | 确认实施：旧模块删除、FetchPipeline 创建、S2-S5 接入管道读取 | — |
| v1.0 | 2026-05-20 | 初版方案 | 青也 + 小B |

---

## 一、背景

当前线上取数流程涉及 **6 条 SQL**，其中 **5 条硬编码在 Python 代码中**，无法通过界面修改：

| # | SQL 用途 | 当前位置 | 可改？ |
|---|---------|---------|--------|
| S1 | 明细取数（28列主查询） | `app.py` → SqlTemplate 表 | ✅ 界面可改 |
| S2 | COUNT 总数统计 | `fetch_service.py` 行812，inline 字符串 | ❌ 改代码 |
| S3 | 合规数据翻页抽样 | `fetch_service.py` 行929，inline 字符串 | ❌ 改代码 |
| S4 | 违规数据翻页抽样 | `fetch_service.py` 行963，inline 字符串 | ❌ 改代码 |
| S5 | 违规原因分布聚合 | `fetch_service.py` 行1051，inline CASE WHEN | ❌ 改代码 |
| S6 | 每日分组 COUNT | `data_fetch.py` 行291，inline 字符串 | ❌ 改代码 |

**目标**：把 S2-S6 五条 SQL 也存入 SqlTemplate 表，在界面中可见、可改、可开关，取数时按配置的管道顺序执行。

---

## 二、总体方案

```
┌────────────────────────────────────────────────────────────┐
│                    sql_config.html                         │
│  ┌─────────────────────┐  ┌──────────────────────────┐    │
│  │  SQL 模板列表 (已有)  │  │  取数管道配置 (新增 Tab)   │    │
│  │  - S1 明细 query     │  │  云环境：S2→S3→S4→S5→S6  │    │
│  │  - S2 COUNT query    │  │  乐采云：S2→S3→S4→S5→S6  │    │
│  │  - S3 抽样 query     │  │  [编辑排序] [启用/禁用]    │    │
│  │  - ...               │  │                          │    │
│  └─────────────────────┘  └──────────────────────────┘    │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼ 读写
┌────────────────────────────────────────────────────────────┐
│                     SQLite 数据库                           │
│  ┌────────────────┐        ┌────────────────────────────┐  │
│  │ sql_template   │        │ fetch_pipeline (新表)       │  │
│  │ + category     │◄───────│ env / order / template_id   │  │
│  └────────────────┘        └────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
                              │
                              ▼ 运行时读取
┌────────────────────────────────────────────────────────────┐
│                  fetch_service.py                          │
│  get_pipeline_sqls(env) → 按 order 返回 SQL 列表            │
│  不再硬编码 SQL，改为从 pipeline 读取                        │
└────────────────────────────────────────────────────────────┘
```

**核心思路**：
1. 删除 `sql_fragments` / `stat_sqls` 两个冗余模块
2. `SqlTemplate` 加 `category` 字段区分 SQL 用途
3. 新建 `FetchPipeline` 表，存「环境 × 执行序号 → SqlTemplate ID」映射
4. 服务层从管道读取 SQL 执行

---

## 三、删除清单

### 3.1 文件删除

```
rm blueprints/sql_fragments.py
rm blueprints/stat_sqls.py
rm templates/sql_fragments.html
rm templates/stat_sqls.html
```

### 3.2 models.py — 删除三个模型类

删除以下三个 class 定义（约行136-171）：

```python
# 删除 ↓
class SqlFragment(db.Model):   # 行136-146
class FetchPlan(db.Model):     # 行149-157
class StatSql(db.Model):       # 行160-171
```

### 3.3 app.py — 删除注册和路由

```python
# 行33-34 删除 ↓
from blueprints.sql_fragments import sql_fragments_bp
from blueprints.stat_sqls import stat_sqls_bp

# 行43-44 删除 ↓
app.register_blueprint(sql_fragments_bp)
app.register_blueprint(stat_sqls_bp)

# 行63-65 删除 ↓
@app.route('/stat_sqls.html') ...

# 行112-114 删除 ↓  
@app.route('/sql_fragments.html') ...

# 行231-237 删除 ↓
from blueprints.sql_fragments import init_preset_fragments, init_default_plan
init_preset_fragments()
init_default_plan()
from blueprints.stat_sqls import init_default_stat_sqls
init_default_stat_sqls()
```

### 3.4 index.html — 删除导航菜单

删除行412-419：
```html
<!-- 删除 ↓ -->
<div class="menu-item menu-child" data-page="sql_fragments.html" ...>SQL片段管理</div>
<div class="menu-item menu-child" data-page="stat_sqls.html" ...>统计SQL配置</div>
```

### 3.5 fetch_service.py — 删除 build_sql_from_plan 函数

删除行38-121（`build_sql_from_plan` 整个函数）。

### 3.6 data_fetch.py — 删除 plan_id 引用

```python
# 行40 删除 ↓
plan_id = data.get('plan_id')

# 行155 改为 ↓（去掉 plan_id=plan_id）
result = fetch_data_from_idata(env, instance, start_date, end_date, sample_percent)

# 行157 同上
```

### 3.7 数据库表删除

```sql
DROP TABLE IF EXISTS sql_fragment;
DROP TABLE IF EXISTS fetch_plan;
DROP TABLE IF EXISTS stat_sql;
```

---

## 四、新增内容

### 4.1 models.py — SqlTemplate 增加 category 字段

在 `SqlTemplate` 类中新增一行：

```python
category = db.Column(db.String(50), default='detail')  # detail/count/sample/reason/daily
```

**字段取值说明**：

| category | 含义 | 对应 SQL |
|----------|------|---------|
| `detail` | 明细取数主查询 | 原有的 S1（已有的 SqlTemplate 默认值） |
| `count` | COUNT 聚合统计 | S2：SELECT AI审核结果, COUNT(DISTINCT 审核id) |
| `daily` | 每日分组统计 | S6：SELECT 创建日期, AI审核结果, COUNT(DISTINCT 审核id) |
| `sample` | 分层抽样查询 | S3/S4：ROW_NUMBER + 随机行号筛选 |
| `reason` | 违规原因聚合 | S5：CASE WHEN 标签映射 |

> 兼容性：存量数据 `category` 字段为 NULL → 默认视为 `'detail'`。

### 4.2 models.py — 新增 FetchPipeline 表

```python
class FetchPipeline(db.Model):
    """取数管道配置表 — 定义各环境的 SQL 执行顺序"""
    __tablename__ = 'fetch_pipeline'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    env = db.Column(db.String(50), nullable=False, comment='环境名：云环境/乐采云环境')
    sort_order = db.Column(db.Integer, nullable=False, default=0, comment='执行序号，1→2→3...')
    sql_template_id = db.Column(db.Integer, db.ForeignKey('sql_template.id'), nullable=False, comment='关联的SQL模板')
    step_name = db.Column(db.String(100), comment='步骤名，如"COUNT总计"、"合规抽样"')
    enabled = db.Column(db.Boolean, default=True, comment='是否启用此步骤')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sql_template = db.relationship('SqlTemplate', backref='pipelines')
```

### 4.3 app.py — 初始化预设管道

在 `app.py` 的 `with app.app_context():` 块中，**替换**原有的 fragment/plan/stat_sql 初始化代码为新逻辑：

```python
# ========== 初始化取数管道SQL模板（S2-S6）==========
from models import SqlTemplate, FetchPipeline
from config import ENV_CONFIG

# S2-S6 的SQL内容从 fetch_service.py 中提取，见附录
PIPELINE_SQLS = {
    'count': {
        'name': '取数-COUNT总数统计',
        'category': 'count',
        'sql_text': """SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
FROM ({detail_sql}) t
WHERE 1=1
GROUP BY `AI审核结果`"""
    },
    'daily': {
        'name': '取数-每日分组COUNT',
        'category': 'daily',
        'sql_text': """SELECT `创建日期`, `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
FROM ({detail_sql}) t
GROUP BY `创建日期`, `AI审核结果`
ORDER BY `创建日期`"""
    },
    'sample_compliant': {
        'name': '取数-合规数据翻页抽样',
        'category': 'sample',
        'sql_text': """SELECT * FROM (
  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '合规'
) tmp
WHERE tmp.rn IN ({positions})"""
    },
    'sample_non_compliant': {
        'name': '取数-违规数据翻页抽样',
        'category': 'sample',
        'sql_text': """SELECT * FROM (
  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '违规'
) tmp
WHERE tmp.rn IN ({positions})"""
    },
    'reason': {
        'name': '取数-违规原因分布聚合',
        'category': 'reason',
        'sql_text': """SELECT `创建日期`, violation_tag, SUM(cnt) as cnt
FROM (
  SELECT `审核id`, `创建日期`,
    CASE
      WHEN t.`AI拒绝原因` LIKE '%国旗%' OR ... THEN '特殊资质缺失'
      WHEN t.`AI拒绝原因` LIKE '%水印%' OR ... THEN '水印'
      -- 完整 CASE WHEN 见附录 B
      ELSE '其他'
    END as violation_tag,
    COUNT(*) as cnt
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '违规'
  GROUP BY `审核id`, `创建日期`, violation_tag
) tagged
GROUP BY `创建日期`, violation_tag
ORDER BY `创建日期`, cnt DESC"""
    }
}

# 插入模板（幂等：检查 name 不存在才插入）
for key, info in PIPELINE_SQLS.items():
    existing = SqlTemplate.query.filter_by(name=info['name']).first()
    if not existing:
        template = SqlTemplate(
            name=info['name'],
            env='云环境',  # 不区分环境，共用
            instances='ZJWC,HWCS,HNLCWC,YNLCY,GXLCY',
            api_url='',     # 管道SQL不需要独立api_url
            sql_text=info['sql_text'],
            category=info['category']
        )
        db.session.add(template)
        db.session.flush()  # 获取 id
        info['_id'] = template.id
        print(f"已创建管道SQL模板: {info['name']} (id={template.id})")
    else:
        info['_id'] = existing.id

# 插入管道配置（幂等：环境+sort_order 唯一）
PIPELINE_STEPS = [
    {'env': '云环境', 'order': 1, 'key': 'count', 'name': 'COUNT总数'},
    {'env': '云环境', 'order': 2, 'key': 'daily', 'name': '每日分组COUNT'},
    {'env': '云环境', 'order': 3, 'key': 'sample_compliant', 'name': '合规抽样'},
    {'env': '云环境', 'order': 4, 'key': 'sample_non_compliant', 'name': '违规抽样'},
    {'env': '云环境', 'order': 5, 'key': 'reason', 'name': '违规原因聚合'},
    {'env': '乐采云环境', 'order': 1, 'key': 'count', 'name': 'COUNT总数'},
    {'env': '乐采云环境', 'order': 2, 'key': 'daily', 'name': '每日分组COUNT'},
    {'env': '乐采云环境', 'order': 3, 'key': 'sample_compliant', 'name': '合规抽样'},
    {'env': '乐采云环境', 'order': 4, 'key': 'sample_non_compliant', 'name': '违规抽样'},
    {'env': '乐采云环境', 'order': 5, 'key': 'reason', 'name': '违规原因聚合'},
]

for step in PIPELINE_STEPS:
    existing_pipe = FetchPipeline.query.filter_by(
        env=step['env'], sort_order=step['order']
    ).first()
    if not existing_pipe:
        pipe = FetchPipeline(
            env=step['env'],
            sort_order=step['order'],
            sql_template_id=PIPELINE_SQLS[step['key']]['_id'],
            step_name=step['name'],
            enabled=True
        )
        db.session.add(pipe)

db.session.commit()
print("取数管道初始化完成")
```

### 4.4 fetch_service.py — 新增管道读取函数

在 `fetch_service.py` 顶部添加：

```python
def get_pipeline_steps(env):
    """获取指定环境的取数管道步骤列表（按 sort_order 排列）

    Returns:
        list of dict: [{'sort_order': 1, 'step_name': 'COUNT', 'sql_template': SqlTemplate}, ...]
    """
    from models import FetchPipeline

    steps = FetchPipeline.query.filter_by(env=env, enabled=True)\
        .order_by(FetchPipeline.sort_order).all()
    return steps
```

### 4.5 fetch_service.py — 改造 fetch_data_from_idata

将行804、812、929、964 的硬编码 SQL 改为从管道读取。核心改造逻辑：

```python
def fetch_data_from_idata(env, instance, start_date, end_date, sample_percent, excluded_audit_ids=None):
    # ... 前面不变 ...
    
    # 旧：detail_sql = build_sql_from_plan(plan_id, ...)  → 删除
    # 新：直接使用 build_sql（不依赖 plan_id）
    detail_sql = build_sql(instance, start_date_fmt, end_date_fmt)

    # ========== S2: COUNT 总数统计 ==========
    # 旧：count_sql = f"""SELECT ... FROM ({detail_sql}) ..."""    → 删除
    # 新：从管道读取
    count_template = get_pipeline_sql_by_category(env, 'count')
    if count_template:
        count_sql = replace_params(count_template.sql_text, {
            'detail_sql': detail_sql
        })
        # 拼接 excluded_sql
        if excluded_sql:
            count_sql = count_sql.replace('WHERE 1=1', f'WHERE 1=1 {excluded_sql}')
    else:
        # 兜底：如果没有管道配置，用原有逻辑
        count_sql = f"""SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
            FROM ({detail_sql}) t WHERE 1=1 {excluded_sql}
            GROUP BY `AI审核结果`"""
    
    # ... COUNT 执行逻辑不变 ...

    # ========== S3/S4: 抽样 ==========
    # 同理，compliant_sql 和 non_compliant_sql 从管道读取
    sample_template_c = get_pipeline_sql_by_category(env, 'sample', ai_result='合规')
    sample_template_n = get_pipeline_sql_by_category(env, 'sample', ai_result='违规')
    # 替换 {detail_sql} 和 {positions} 占位符后执行

    # ========== S5: 违规原因 ==========
    # fetch_error_reasons_online() 同理，从管道读取 reason 模板
```

> ⚠️ 具体改造建议：在 `fetch_service.py` 中新增辅助函数 `get_pipeline_sql_by_category(env, category, **kwargs)`，返回匹配 category 的第一个已启用 SqlTemplate。S2-S5 在原位置替换为调用此函数。

### 4.6 data_fetch.py — 改造 S6 每日 COUNT

行291-297 的 inline `daily_count_sql` 改为：

```python
# 旧：daily_count_sql = f"""SELECT `创建日期`, ... FROM ({detail_sql}) ..."""
# 新：
daily_template = get_pipeline_sql_by_category(env, 'daily')
if daily_template:
    daily_count_sql = replace_params(daily_template.sql_text, {'detail_sql': detail_sql})
else:
    # 兜底
    daily_count_sql = f"""SELECT `创建日期`, `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
        FROM ({detail_sql}) t GROUP BY `创建日期`, `AI审核结果` ORDER BY `创建日期`"""
```

---

## 五、前端改造（sql_config.html）

在 `sql_config.html` 中新增一个 **Tab 切换**：SQL模板列表 / 取数管道配置。

### 5.1 Tab 切换 UI

```html
<!-- 在页面 header 下方添加 -->
<div class="tab-bar">
  <button class="tab-btn active" onclick="switchTab('templates')">SQL模板列表</button>
  <button class="tab-btn" onclick="switchTab('pipeline')">取数管道配置</button>
</div>
<div id="tab-templates" class="tab-content"> ... 原有的模板列表 ... </div>
<div id="tab-pipeline" class="tab-content" style="display:none">
  <!-- 管道配置面板 -->
</div>
```

### 5.2 管道配置面板功能

按环境分两组卡片（云环境 / 乐采云环境），每组显示步骤列表：

| 序号 | 步骤名 | SQL模板 | 状态 | 操作 |
|------|--------|---------|------|------|
| 1 | COUNT总数 | 取数-COUNT总数统计 | ✅启用 | [禁用] [上移/下移] |
| 2 | 每日分组 | 取数-每日分组COUNT | ✅启用 | [禁用] |
| 3 | 合规抽样 | 取数-合规数据翻页抽样 | ✅启用 | [禁用] |
| ... | ... | ... | ... | ... |

- **启用/禁用**：toggle 某一步是否参与取数管道
- **上移/下移**：调整执行顺序
- **修改模板**：下拉选择已有的 SqlTemplate（按 category 筛选）

### 5.3 后端 API

新增两个 API（建议在 `sql_config.py` blueprint 中添加）：

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/fetch-pipeline?env=云环境` | 获取指定环境的管道配置 |
| PUT | `/api/fetch-pipeline/batch` | 批量更新管道（排序/启用/模板关联） |

---

## 六、实施步骤（按顺序）

| 步骤 | 操作 | 风险 | 验证方式 |
|------|------|------|---------|
| 1 | **备份 app.db** | 无 | `cp instance/app.db instance/app.db.bak` |
| 2 | 删除 `sql_fragments` + `stat_sqls` 四文件 | 低 | `ls` 确认文件不存在 |
| 3 | 修改 `models.py`：删除三个旧类，新增 `FetchPipeline`，`SqlTemplate` 加 `category` | 中 | 启动不报错 |
| 4 | 修改 `app.py`：删除旧注册/路由，新增管道初始化 | 中 | 启动日志显示"取数管道初始化完成" |
| 5 | 修改 `index.html`：删除两个菜单项 | 低 | 侧边栏不再显示 |
| 6 | 修改 `fetch_service.py`：删除 `build_sql_from_plan`，新增 `get_pipeline_sql_by_category`，改造 4 处内联 SQL | 高 ⚠️ | 拉取一次数据，确认各步骤执行正常 |
| 7 | 修改 `data_fetch.py`：删除 `plan_id` 引用，改造 S6 SQL | 中 | DailyStats 写入正常 |
| 8 | 改造 `sql_config.html`：新增管道 Tab | 中 | 界面正常渲染，可修改排序/启用状态 |
| 9 | **全流程回归测试** | 高 | 拉取数据 → 看板刷新 → 互检 → 导出，全链路正常 |

---

## 七、风险提示

| 风险 | 级别 | 缓解 |
|------|------|------|
| 管道SQL模板的 `{detail_sql}` 占位符替换出错 | 🔴高 | 使用 `replace_params()` 统一替换，保留兜底逻辑 |
| 抽样SQL的 `{positions}` 占位符替换问题 | 🔴高 | 抽样步骤需要额外处理 `{positions}` 参数，非标准 `${param}` 格式 |
| 旧数据库有 `sql_fragment`/`fetch_plan`/`stat_sql` 表但模型已删 | 🟡中 | 先 DROP TABLE，或保留表但删模型 |
| 删除 `build_sql_from_plan` 后某处仍有引用 | 🟡中 | 全局搜索 `build_sql_from_plan\|plan_id\|SqlFragment\|FetchPlan\|StatSql` 确认无残留 |

---

## 十二、验收标准（v1.3 新增）

> 以下验收标准分两级：🟢 **功能验收**（逐个功能点可测）、🟡 **回归验收**（全链路无影响）。

### 12.1 🟢 功能验收

| # | 验证项 | 如何验证 | 预期结果 |
|---|--------|---------|---------|
| AC-1 | 旧模块已删除 | 访问 `/sql_fragments.html`、`/stat_sqls.html` | 返回 404；侧边栏无对应菜单项 |
| AC-2 | 管道 SQL 模板已入库 | 进入 sql_config → SQL 模板 Tab → 筛选 category="count" | 看到"取数-COUNT总数统计"等 5 条模板 |
| AC-3 | 取数管道步骤可见 | 进入 sql_config → 取数管道 Tab | 云环境/乐采云环境各显示 5 个步骤，序号 1-5 |
| AC-4 | 管道步骤可启用/禁用 | 切换某一步的启用开关 → 保存 → 刷新页面 | 状态保留 |
| AC-5 | 管道步骤可排序 | 点击两个步骤的"上移/下移"按钮 → 保存 → 刷新 | 顺序变更保留 |
| AC-6 | COUNT SQL 带实例筛选 | 触发一次拉取 → 查看后端日志打印的 COUNT SQL | SQL 中包含 `instance_code = 'ZJWC'` |
| AC-7 | **新增模板按钮** | 进入 sql_config → SQL 模板 Tab | 顶部有"+ 新增模板"按钮 |
| AC-8 | **新增模板可填充 category** | 点击新增 → 弹窗表单 | category 下拉有 5 个选项（detail/count/sample/reason/daily） |
| AC-9 | **新增模板后可见** | 填写表单 → 提交 → 刷新列表 | 新模板出现在对应 category 筛选下 |
| AC-10 | **管道添加步骤按钮** | 进入 sql_config → 取数管道 Tab | 每个环境组底部有"+ 添加步骤"按钮 |
| AC-11 | **管道添加步骤弹窗** | 点击"+ 添加步骤" | 弹窗显示 SqlTemplate 下拉选择（按 category 分组） |
| AC-12 | **category 筛选按钮组** | 进入 sql_config → SQL 模板 Tab | 顶部有：全部 / 明细取数 / COUNT统计 / 抽样 / 违规原因 / 每日分组 |
| AC-13 | **params_json 已填充** | 查看任一条管道 SQL 模板的参数列表 | `detail_sql` 参数显示来源描述，含"S1明细模板"名称和 id |
| AC-14 | **系统注入参数有视觉区分** | 编辑管道 SQL 模板 → 查看参数列表 | 系统注入参数（`detail_sql`、`positions`）灰色底色 + 🔒 图标，与普通参数区分 |
| AC-15 | **SQL 编辑区有引用提示** | 编辑管道 SQL 模板 → 查看 SQL 编辑区顶部 | 显示提示条："此模板含系统自动注入占位符，引用源见参数列表" |

### 12.2 🟡 回归验收

| # | 验证项 | 如何验证 | 预期结果 |
|---|--------|---------|---------|
| REG-1 | 数据拉取正常 | 在机审任务中心触发一次拉取（云环境 ZJWC，最近1天，10%抽样） | 返回批次记录，状态"已完成"，总数 > 0 |
| REG-2 | DailyStats 写入正常 | 拉取完成后 → 看板刷新 → 检查当日统计数据 | 总数/合规/违规与批次记录一致 |
| REG-3 | 看板违规原因图正常 | 看板 → 饼图 | 数据正常渲染，数字 > 0 |
| REG-4 | 互检正常 | 在批次详情页触发互检 → 等待完成 | 批次状态变为"已互检"，不一致数 ≥ 0 |
| REG-5 | 导出正常 | 在批次详情页选择"导出本地抽样明细" | 下载 CSV，行数 = 抽样条数 |
| REG-6 | 系统启动无报错 | 重启 `python app.py` | 控制台无 exception，显示"取数管道初始化完成" |

### 12.3 验收流程

```
1. 代码部署 → 重启服务 → 确认启动无报错 (REG-6)
                    ↓
2. SQL模板页 → 逐项验证 AC-1 ~ AC-5, AC-7 ~ AC-14
                    ↓
3. 触发取数 → 验证 AC-6 + REG-1/2
                    ↓
4. 看板验证 → 验证 REG-3
                    ↓
5. 互检验证 → 验证 REG-4 → 导出验证 REG-5
```

---

## 附录 A：SQL 模板中 `{detail_sql}` 占位符说明

管道 SQL（S2-S6）中包含 `{detail_sql}` 占位符（注意：花括号 `{}` 而非 SQL 的 `${}` 格式），它在运行时被替换为 S1 明细 SQL 的完整字符串。

替换时使用 Python 的 `.format()` 或 `str.replace()`，**不要用 SQL 拼接**（可能导致注入）。建议在 `replace_params()` 函数中统一处理：

```python
# 在 replace_params() 中增加对 {detail_sql} 的处理
sql = sql.replace('{detail_sql}', str(params.get('detail_sql', '')))
sql = sql.replace('{positions}', str(params.get('positions', '')))
```

## 附录 B：S5 违规原因完整 CASE WHEN

S5 模板的 `sql_text` = 保持 `fetch_service.py` 行1051-1110 的完整 CASE WHEN，此处省略（太长），直接从 `fetch_service.py` 复制即可。需确认模板字段 TEXT 类型能容纳完整 SQL（约 2000 字符，SqlTemplate.sql_text 为 Text 类型，足够）。

---

## 八、待补充需求（2026-05-21 页面评审发现）

> 已确认：取数管线已切换到新逻辑（`get_pipeline_sql_by_category()` 实际被调用），COUNT SQL 已通过 `{detail_sql}` 继承实例筛选。

4 项前端功能缺失，需补充：

### 8.1 SQL 模板 — 支持手动增加

**现状**：后端有 `POST /api/sql-config`（行136），前端 `sql_config.html` 缺"新增模板"按钮和表单弹窗。

**需要**：
- 在 SQL 模板 Tab 顶部加"**+ 新增模板**"按钮
- 弹窗表单：模板名称 / 环境 / 实例 / 分类(category) / API URL / SQL内容 / 参数定义
- category 下拉选项：`detail`(明细取数) / `count`(COUNT统计) / `sample`(抽样) / `reason`(违规原因) / `daily`(每日分组)
- 提交调用 `POST /api/sql-config`

### 8.2 取数管道 — 支持增加步骤

**现状**：后端缺 `POST /api/fetch-pipeline`，前端只能调整已有5步的顺序和启用状态。

**需要后端新增**：
```
POST /api/fetch-pipeline
请求: { "env": "云环境", "sql_template_id": 99, "step_name": "自定义步骤", "sort_order": 6 }
返回: { "success": true, "id": 11 }
```

**需要前端新增**：
- 每个环境组右下角加"**+ 添加步骤**"按钮
- 点击弹窗：下拉选择已有 SqlTemplate（按 category 分组显示）+ 输入步骤名
- 新增步骤自动追加到末尾（sort_order = max + 1）

### 8.3 SQL 模板列表 — 支持按 category 分组/筛选

**现状**：所有 SQL 模板平铺展示，无分组。

**需要**：
- 顶部加 **category 筛选按钮组**：全部 | 明细取数 | COUNT统计 | 抽样 | 违规原因 | 每日分组
- 或改为**可折叠分组**：每个 category 一个折叠区，展开显示该分类下的模板
- 推荐用按钮组方式（更易实现，与现有卡片网格布局兼容）

### 8.4 SQL 编写辅助小工具

在 SQL 模板编辑弹窗中增加一个 **"辅助工具" 折叠面板**，包含：

| 工具 | 功能 |
|------|------|
| **批量替换** | 选中 SQL 文本 → 替换所有 `${xxx}` → `{xxx}` 或其他格式，支持正则 |
| **占位符检查** | 扫描 SQL 中的 `${xxx}` 和 `{xxx}`，列出所有占位符及出现次数 |
| **格式化** | 点击"格式化 SQL" → 自动缩进、关键字大写（仅美化，不改变语义） |
| **快速插入** | 下拉选择预设片段插入光标位置（如 `COUNT(DISTINCT \`审核id\`)`、`ROW_NUMBER() OVER(ORDER BY ...)`、CASE WHEN 模板等） |

实现方式：纯前端 JS 实现，不依赖后端。在弹窗底部加"🧰 辅助工具"展开按钮。

---

## 九、当前代码状态确认（2026-05-21）

| 检查项 | 状态 | 位置 |
|--------|------|------|
| 旧模块已删除（sql_fragments/stat_sqls） | ✅ 已删 | 四文件不存在 |
| FetchPipeline 模型已存在 | ✅ | models.py |
| 管道 API 已实现（GET/PUT/batch） | ✅ | sql_config.py 行780-887 |
| 管道 API 缺 POST (创建) | ❌ | 见 §8.2 |
| S2-S5 已从管道读取 SQL | ✅ | fetch_service.py 行795/920/967 |
| COUNT SQL 实例筛选 | ✅ | build_sql() 行189 `r.instance_code = '${instance}'` |
| 前端 SQL 模板 Tab + 取数管道 Tab | ✅ | sql_config.html 行463-489 |
| 前端缺"新增模板"按钮 | ❌ | 见 §8.1 |
| 前端缺"添加管道步骤"按钮 | ❌ | 见 §8.2 |
| 前端缺 category 分组视图 | ❌ | 见 §8.3 |
| 前端缺 SQL 辅助工具 | ❌ | 见 §8.4 |
| 占位符格式不统一（`{...}` vs `${...}`） | ❌ | 见 §10 |

---

## 十、管道 SQL 占位符溯源（v1.4 重写）

> ⚠️ **v1.2 的 `${}` 格式统一方案没有解决核心问题**。  
> 核心问题是：用户打开一条管道 SQL（如 COUNT 模板），看到 `FROM ({detail_sql})`，**不知道 `detail_sql` 引用的是哪个模板、哪段代码**。即使改成 `${detail_sql}`，困惑依旧。

### 10.1 现状确认

```
数据库实测（2026-05-21）：

管道SQL模板 S2~S6:
  - sql_text 含 {detail_sql} / {positions}
  - params_json = None（全部为空！）

明细SQL模板 S1（id=1, id=2）:
  - sql_text 含 ${start_date} / ${instance} 等
  - params_json = [{"name":"start_date",...}]（有定义）

→ 用户在界面打开 S2-S6，参数区空白，看不懂 {detail_sql} 是什么
```

### 10.2 解决方案：显式标注引用来源

**不改占位符格式**（`{detail_sql}` / `{positions}` 保持花括号，与 `${param}` 语义不同——前者是系统自动注入的 SQL 片段，后者是用户填写的参数），但必须让它在界面上**可追溯**。

#### 10.2.1 补充 params_json（核心）

每条管道 SqlTemplate 写入 `params_json`，显式声明被引用的模板：

**count / daily / reason 模板**：

```json
[
  {
    "name": "detail_sql",
    "required": true,
    "system_injected": true,
    "description": "← 引用模板「商品审核数据取数-云环境」(id=1) 或「商品审核数据取数-乐采云环境」(id=2) 的展开 SQL。系统根据拉取环境自动选择"
  }
]
```

**sample 模板**（额外多一个 `positions`）：

```json
[
  {
    "name": "detail_sql",
    "required": true,
    "system_injected": true,
    "description": "← 引用模板「商品审核数据取数-云环境」(id=1) 或「商品审核数据取数-乐采云环境」(id=2) 的展开 SQL"
  },
  {
    "name": "positions",
    "required": true,
    "system_injected": true,
    "description": "← 系统根据 MD5(instance+date_range+固定种子) 计算的随机行号列表，格式：1,15,23,44,..."
  }
]
```

> `system_injected: true` 标记表示此参数**不由用户填写**，前端渲染时可用灰色/斜体区分。

#### 10.2.2 UI 层增强

在 sql_config.html 编辑弹窗的参数列表区域：

- **系统注入参数**用灰色底色 + 🔒 图标区分
- 每个参数的 `description` 显示为 tooltip 或小字说明
- SQL 编辑区顶部加一条提示条：

> 💡 此 SQL 模板包含系统自动注入的占位符（`{detail_sql}` / `{positions}`），它们引用的源模板见右侧参数列表。

#### 10.2.3 初始化/迁移

启动时做两件事：

```python
# 1. 给已有管道模板补充 params_json
for tpl in SqlTemplate.query.filter(
    SqlTemplate.category.in_(['count', 'daily', 'sample', 'reason']),
    SqlTemplate.params_json == None  # 或 == '[]'
).all():
    # 找到 S1 明细模板（同环境）
    detail_tpl = SqlTemplate.query.filter(
        SqlTemplate.category == 'detail',
        SqlTemplate.env == tpl.env
    ).first()
    detail_ref = f"商品审核数据取数-{tpl.env}(id={detail_tpl.id})" if detail_tpl else "S1明细模板"
    
    params = [
        {"name": "detail_sql", "required": True, "system_injected": True,
         "description": f"← 引用模板「{detail_ref}」的展开SQL，系统自动注入"}
    ]
    if tpl.category == 'sample':
        params.append(
            {"name": "positions", "required": True, "system_injected": True,
             "description": "← 系统计算的随机抽样行号列表"}
        )
    tpl.params_json = json.dumps(params, ensure_ascii=False)
db.session.commit()
```

#### 10.2.4 远期目标（可选）

在取数管道 Tab 中，每步显示依赖关系图：

```
步骤1: S1 明细查询 (id=1)
   │  展开后的 SQL
   ├──→ 步骤2: COUNT (id=3) 引用 → {detail_sql}
   ├──→ 步骤3: 合规抽样 (id=5) 引用 → {detail_sql} + {positions}
   └──→ 步骤4: 违规抽样 (id=6) 引用 → {detail_sql} + {positions}
```
