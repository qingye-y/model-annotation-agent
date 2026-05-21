# 商品审核大模型质检标注工作台 — PRD v2.3

**版本**：v2.3（基于 v2.2 持续完善）
**日期**：2026-05-20
**产品经理**：李子玥（青也）
**审查协助**：小B
**文档状态**：持续完善中
**关联技术文档**：`docs/技术文档.md`
**Git**: `github.com:qingye-y/model-annotation-agent.git` | tag: `v1.0.0`

> 📌 **开发协作说明**：本项目采用**多线并行开发**模式。青也（产品经理/主开发者）负责本仓库的核心模块；质检修正中心、Badcase 管理中心、智能分析中心三个模块由**其他同事并行开发**，后续整合进本仓库。本文档覆盖全产品视图，协作模块部分以整合时的约定为准。

---

## 0. 版本修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|------|------|---------|--------|
| v1.0 | 2026-05-10 | 初版 PRD，定义核心业务流程 | PM |
| v2.0 | 2026-05-20 | 大幅修订，补充功能详情、数据模型 | PM |
| v2.1 | 2026-05-20 | 对照代码库全量审查：补充缺失模块（智能分析中心、SQL配置、登录体系）、修正数据模型字段（补16个）、补充架构说明、API参考、风险评估、偏差清单 | 小B |
| v2.2 | 2026-05-20 | 标注模块协作关系；项目文档交叉引用；违规分类19→24类；饼图展开明细；项目目录结构 | 小B |
| v2.3 | 2026-05-20 | 发布 v1.0.0：SQL d.pt 修复、分批抽样(偏差<0.1%)、删除联动、批次详情页；Git 仓库 + 标签体系 | 小B |

---

## 1. 背景与目标

### 1.1 背景

公司上线了商品审核大模型（模型A），用于自动审核商家提交的商品信息。为持续监控模型效果、发现 Badcase 并反哺模型优化，需要建立一个集**数据拉取 → 机审互检 → 人工标注 → 质检修正 → Badcase 产出**为一体的质量监控工作台。

当前覆盖 **5 个业务实例**（ZJWC / HWCS / HNLCWC / YNLCY / GXLCY），横跨**云环境**和**乐采云环境**两套 iData 数据源。

### 1.2 目标

1. **标准化的质检标注流程**：标注员高效地对模型A的审核结果进行"正确/错误/忽略"判定。
2. **竞品大模型（模型B）交叉验证**：自动过滤双模型一致数据，不一致数据才进入人工标注，节省人力。
3. **管理员灵活调度与质检**：任务分配、抽检修正、Badcase 数据集产出。
4. **所有关键配置可视化**：标签、提示词、实例关联、SQL模板、Cookie 均可在前端管理，无需改代码或直接操作数据库。

### 1.3 平台定位

**质检监控系统**。核心产出是 Badcase 数据集，供算法团队优化模型A。本平台不做线上审核替代，不做训练数据标注。

---

## 2. 用户角色与权限

| 角色 | 数量 | 权限描述 | 数据库标识 |
|------|------|---------|-----------|
| **管理员** | 唯一（预置 admin） | 全局数据看板、机审任务管理、标注任务分配、质检修正、Badcase 管理、用户管理（增删改查/重置密码）、系统配置（标签/提示词/实例关联/SQL模板/Cookie/模型B） | `role='admin'` |
| **标注员** | 多人 | 查看并标注分配给自己的任务（正确/错误/忽略），查看被管理员修正的记录（被通知时） | `role='annotator'` |

**认证机制**：Flask-Login（Session 页面认证）+ PyJWT（API Token，7天有效期）。默认管理员账号 `admin / admin123`，首次启动自动创建。

---

## 3. 技术架构

> ⚠️ **v2.1 新增**：原 PRD 缺少架构说明。

```
┌────────────────────────────────────────────────────────────────┐
│                     前端层（Jinja2 SPA）                         │
│  /templates/ 17个HTML页面  |  /static/ common.css + config.js  │
│  ApexCharts (CDN) 图表  |  SheetJS Excel 导入导出               │
├────────────────────────────────────────────────────────────────┤
│                     路由层 (Flask, 端口 5000)                    │
│  app.py (主入口)                                                │
│  7个蓝图: auth | data_fetch | dashboard | sql_config           │
│           model_review | prompt_rules | analysis                │
├────────────────────────────────────────────────────────────────┤
│                     服务层                                       │
│  services/stats_service.py (统计聚合)                           │
│  services/fetch_service.py (数据拉取 + 违规关键词提取)           │
│  services/utils.py (工具函数)                                   │
├────────────────────────────────────────────────────────────────┤
│                     数据层 (SQLAlchemy ORM)                      │
│  SQLite 3 (app.db)  +  自动列迁移                                │
│  6个核心模型: User | RawData | FetchLog | DailyStats           │
│               SqlConfig | SqlTemplate | Annotation | QcRecord  │
├────────────────────────────────────────────────────────────────┤
│                     外部依赖                                     │
│  iData API (线上数据拉取, Cookie 认证, 两套环境URL)              │
│  模型B API (OpenAI 兼容接口, 可选, 支持Mock降级)                │
└────────────────────────────────────────────────────────────────┘
```

**关键设计决策**：

| 决策点 | 选择 | 原因 |
|--------|------|------|
| 数据库 | SQLite 3（本地文件 app.db） | 原型阶段零部署成本，后续迁移至 PostgreSQL |
| 后端框架 | Flask 2.3.3 | 轻量，蓝图架构天然支持模块化 |
| 用户认证 | Flask-Login + PyJWT 双机制 | Session 用于页面，JWT 用于 API 调用 |
| 模型B互检 | 异步 daemon 线程 + abort_flag 中止 | 互检耗时长（批量调用），线程异步避免阻塞 HTTP 响应 |
| 数据抽样 | 固定种子分层随机 + 去重 | 可复现性 + 增量拉取避免重复数据 |
| 配置存储 | SqlConfig 表 (key-value) + prompt_rules/ 目录 (Markdown) | 高频变动配置用数据库，规则文档用文件便于版本管理 |
| 前端图表 | ApexCharts (CDN) | 无需构建打包，模板直出 |

---

## 4. 业务流程总览

```
数据入库 ──→ 机审互检 ──→ 任务分配 ──→ 人工标注 ──→ 质检修正
                                                        │
                                                        ▼
                                                  Badcase 产出
```

### 5步主流程：

1. **数据入库**：管理员在「机审任务中心」触发，通过 iData API 按环境、实例、日期范围、抽样比例拉取商品数据。上层抽样逻辑按合规/违规分层随机，控制样本违规率与实际一致；底层 SQL 去重避免重复拉取。拉取后写入 RawData 表，同步更新 FetchLog 和 DailyStats 快照。

2. **机审互检**：模型B 按差异化提示词策略复核抽样数据。一致 → `modelb_consistent=True`（自动归档）；不一致 → `modelb_consistent=False`（作为待标注任务源）。

3. **任务分配**：管理员在「调度中心」按审核规则分组，将不一致任务分配给标注员。支持三种策略：平均分配、按剩余额度比例分配、清空分配。

4. **人工标注**：标注员在「标注列表」页对任务进行判定（正确/错误/忽略）。选"错误"时强制选择错误标签（从系统配置动态读取）。

5. **质检修正**：管理员抽检已标注任务，直接修正结果（写入 QcRecord 表），可选推送给标注员通知。

6. **Badcase 产出**：所有最终确认"错误"的记录自动进入 Badcase 池，管理员可补充解决方案并导出。

---

## 5. 功能模块详情

### 5.1 登录与权限体系

> ⚠️ **v2.1 新增**：原 PRD 未独立列出。

| 功能 | 状态 | 文件 |
|------|------|------|
| 登录页（用户名+密码） | ✅ 已实现 | `templates/login.html` |
| API 登录，返回 JWT Token（7天有效） | ✅ 已实现 | `POST /api/auth/login` |
| 获取当前用户信息 | ✅ 已实现 | `GET /api/auth/me` |
| 用户列表（管理员） | ✅ 已实现 | `GET /api/auth/users` |
| 添加/更新/删除用户 | ✅ 已实现 | `POST/PUT/DELETE /api/auth/users/<id>` |
| 重置用户密码 | ✅ 已实现 | `POST /api/auth/users/<id>/reset-password` |
| 退出登录 | ✅ 已实现 | `/logout` |

**页面路由**：`/` → 自动跳转主页（需登录），`/login` → 登录页。

---

### 5.2 首页数据看板

**文件**：`blueprints/dashboard.py`、`templates/dashboard.html`  
**主 API**：`GET /api/dashboard/stats`

#### 5.2.1 数据源

- **统一从 DailyStats 快照表读取**，反映线上全量统计（非仅本地抽样数据）
- DailyStats 在每次数据拉取完成后由 `generate_daily_stats()` 按 实例×日期×违规原因 三维度写入
- 违规原因分布支持从 iData 线上实时查询（`/api/dashboard/reason-distribution`），也可从 DailyStats 的 `error_reasons` JSON 字段聚合

#### 5.2.2 实时指标卡片

| 指标 | 数据源 | 状态 |
|------|--------|------|
| 审核总数 | DailyStats 聚合 | ✅ |
| 违规率 (non_compliant/total) | DailyStats 汇总 | ✅ |
| AI审核准确率 | ⚠️ **待完善**（当前返回 null，需接入标注结果统计） | 🔴 |
| 机审不一致率 | FetchLog（互检完成批次）聚合 | ✅ |

**交互**：点击卡片可跳转至机审任务中心，筛选联动。

#### 5.2.3 趋势图

- 近7天违规率趋势（支持切换近30天）
- 近7天不一致率趋势
- 数据源：DailyStats 按日期聚合，缺失日期补零

#### 5.2.4 违规原因分布

- 数据来源：线上全量违规数据（`/api/dashboard/reason-distribution`），通过聚合 SQL 实时查询 iData
- 分类规则：基于提示词 V24 与线上 5 万条样本数据分析得出的 **24 类规则**（类目错放、图文不一致、水印、无关信息、引流、多主体、主图主体等），优先级从高到低。实际统计分布见项目规划文档 `项目规划.md`
- 展示逻辑（v2.2 更新）：**动态分组** — 占比 ≥ 2% 的类型独立展示，< 2% 的全部归入"其他"。点击图例"其他"或饼图对应扇区，展开明细列表（显示被合并的所有类型及各自数量和占比）

#### 5.2.5 其他统计 API

| API | 功能 | 状态 |
|-----|------|------|
| `GET /api/dashboard/by-instance` | 按实例统计 | ✅ |
| `GET /api/dashboard/by-user` | 按标注员统计（进度、配额使用率） | ✅ |
| `GET /api/dashboard/trend` | 每日趋势（RawData 创建时间维度） | ✅ |
| `GET /api/dashboard/logs` | 拉取日志（最近20条 FetchLog） | ✅ |
| `GET /api/dashboard/inconsistency-rate` | 机审不一致率（支持日期+实例筛选） | ✅ |
| `GET /api/overview` | 概览统计 | ✅ |

---

### 5.3 机审任务中心

**文件**：`blueprints/data_fetch.py`、`templates/model_task.html`  
**主 API**：`POST /api/data-fetch`

#### 5.3.1 数据拉取弹窗

| 参数 | 说明 |
|------|------|
| 环境 | 云环境 / 乐采云环境（自动从 ENV_CONFIG 读取对应 API URL） |
| 实例 | 支持单实例或多实例（逗号分隔或数组），从 SqlTemplate 或 ENV_CONFIG 获取列表 |
| 日期范围 | start_date ~ end_date（YYYYMMDD 格式），默认最近7天 |
| 抽样比例 | 1% ~ 100%，支持全量拉取 |
| SQL 模板 | 支持 template_id 指定 SQL 模板（可选，默认按实例自动匹配） |

#### 5.3.2 抽样规则

- **分层随机抽样**：分别从合规/违规数据中按比例随机抽取，保证样本违规率与线上一致
- **固定种子**：使用日期+实例的 MD5 作为随机种子，保证同一批次重复拉取结果可复现
- **增量拉取**：重复拉取同一日期时，自动跳过已入库的数据（按 audit_id 去重），统计 skipped_duplicates

#### 5.3.3 批次列表

- 按天（或按日期范围）生成任务批次（FetchLog），每行展示：批次号、数据量、合规/违规、不一致数量/率、涉及实例数、状态
- 支持实例筛选按钮组（全部 / 按实例 + 环境分组）
- 支持状态筛选（拉取状态 + 互检状态）

#### 5.3.4 批次详情页

**文件**：`templates/batch_detail.html`  
**路由**：`/batch-detail/<batch_id>`

- 入口："明细"按钮
- 布局：左上有返回按钮；顶部批次概览卡片（总数/合规/违规/不一致/实例/日期范围）；主体为明细数据表格（分页 + 筛选）
- 截断字段（如AI拒绝原因）支持 tooltip 悬停查看完整内容
- 页面内可直接触发互检、中止或删除批次

#### 5.3.5 操作栏

| 按钮 | 功能 |
|------|------|
| 明细 | 跳转批次详情页 |
| 更多（下拉菜单） | 触发互检 / 中止互检 / 删除批次 |
| 导出（详情页内） | 三种导出模式（下拉菜单选择） |

#### 5.3.6 导出功能

| 导出模式 | 说明 |
|---------|------|
| 导出本地抽样明细 | 全部 28 个字段（含模型B结果字段） |
| 导出违规数据 | 仅 AI 判定为违规的记录 |
| 导出互检差异数据 | 模型A/B 结果不一致的记录，含双方审核结果 |

#### 5.3.7 异常处理

- 401 认证失败 → 错误提示框内提供快捷链接，点击可跳转「系统配置 → 数据源配置 → Cookie 管理」

---

### 5.4 标注任务管理

**文件**：`templates/annotation_list.html`、`templates/batch_detail.html`（详情弹窗）  
**后端模型**：`Annotation`、`RawData`

| 功能 | 实现状态 | 说明 |
|------|---------|------|
| 任务列表（按标注员筛选） | ✅ 已实现 | 支持按状态（全部/待标注/已标注）筛选 |
| 标注详情弹窗 | ✅ 已实现 | 左侧商品图片滑动（支持放大），右侧模型A/B审核结果对比 |
| 判定按钮 | ✅ 已实现 | 三个按钮：正确/错误/忽略 |
| 错误时强制选标签 | ⚠️ 待接入 | 标签应从系统配置动态读取（当前可能有硬编码） |
| 键盘快捷操作 | ✅ 已实现 | ← → 切换任务，ESC 关闭弹窗 |
| 后端 API 对接 | ⚠️ 部分 | 标注结果写入 Annotation 表，但前端可能有 localStorage 模拟残留 |

#### 5.4.1 标注流程

1. 标注员在标注列表页看到分配给自己的任务（按审核规则分组）
2. 点击任一任务打开详情弹窗
3. 查看商品信息（图片、类目、AI审核结果、模型B复核结果）
4. 做出判定：
   - **正确**：模型A判定无误
   - **错误**：模型A判定有误 → 强制选择错误标签 → 提交
   - **忽略**：无法判断或边缘情况 → 自动跳过
5. 提交后：写入 Annotation 表，更新 RawData 的 check_result/annotation 字段

---

### 5.5 任务调度与人员管理

**文件**：`templates/dispatch_center.html`、`templates/account_management.html`

| 功能 | 实现状态 |
|------|---------|
| 标注员列表（含当前工作量/配额） | ✅ |
| 按规则分组分配任务 | ✅ |
| 平均分配 / 按剩余额度比例分配 / 清空分配 | ✅ |
| 标注员增删改查 | ✅（account_management.html） |
| 每日额度调整 | ✅（PUT /api/auth/users/<id>） |
| 负载概览（实时显示标注员工作量） | ✅ |

**配额机制**：
- 每个标注员有 `daily_quota` 字段（默认 200）
- 分配逻辑：按剩余额度 = daily_quota - 当日已标注数来计算可分配量
- 管理员可在人员管理页面随时调整

---

### 5.6 质检修正中心

> 🔄 **协作模块**：此模块由其他同事并行开发，后续整合。以下为产品功能定义，具体实现以整合时交付为准。

**文件**：`templates/qc_center.html`  
**后端模型**：`QcRecord`

#### 5.6.1 功能

管理员抽检已标注任务，发现标注错误时直接修正。修正结果写入 QcRecord 表，可选推送给标注员通知。

#### 5.6.2 数据结构

QcRecord 记录含：原始标注结果 + 修正后结果 + 解决方案 + 质检批次号 + 通知状态。

#### 5.6.3 设计理念

业务方强调：仅标注员标记的"错误"不一定准确，需与质检员确认后的 Badcase 明确区分。因此数据流为：

```
标注员标记 → 标注员错误列表（待确认）
     ↓ 管理员抽检
质检确认后的错误 → Badcase 列表（最终确认）
```

---

### 5.7 Badcase 管理中心

> 🔄 **协作模块**：此模块由其他同事并行开发，后续整合。

**文件**：`templates/badcase_center.html`

| 功能 | 说明 |
|------|------|
| 集中展示 | 所有最终确认为"错误"的记录（质检确认后） |
| 整合逻辑 | 支持将相同问题的记录整合，基于备注信息初步划分 |
| 解决方案 | 管理员可补充微调建议（solution 字段） |
| 导出 | 勾选导出，可视化前台页面输出 |

---

### 5.8 智能分析中心

> 🔄 **协作模块**：此模块由其他同事并行开发，后续整合。当前仓库中有基础页面和 Mock API 骨架，整合时需接入真实数据库查询。

> ⚠️ **v2.1 新增**：此模块在 v2.0 PRD 中完全缺失。

**文件**：`blueprints/analysis.py`、`templates/analysis_center.html`  
**API prefix**：`/api/analysis`

#### 5.8.1 功能说明

面向管理员的数据分析工具，帮助发现标注质量问题、知识沉淀和学习。

#### 5.8.2 子模块

| 子模块 | API | 状态 | 说明 |
|--------|-----|------|------|
| 标注员问题分析 | `GET /api/analysis/annotator-issues` | ⚠️ Mock | 标注员统计卡片、修正率趋势、错误类型分布 |
| 标注员详细错误案例 | `GET /api/analysis/annotator-detail` | ⚠️ Mock | 按标注员名筛选错误案例明细 |
| 知识库搜索 | `GET /api/analysis/search` | ⚠️ Mock | 跨表搜索（标注备注/质检方案/提示词/配置） |
| 知识库内容源 | `GET /api/analysis/knowledge-sources` | ⚠️ Mock | 知识源列表配置 |
| 概览数据 | `GET /api/analysis/overview` | ⚠️ Mock | 总标注量、修正率、趋势 |

**待办**：以上 5 个 API 当前均为 Mock 数据，需要接入真实数据库查询。

---

### 5.9 系统设置

原 PRD 中系统设置描述过于简略。实际代码中有 **5 个子配置页面**。

#### 5.9.1 数据源配置

**文件**：`templates/sql_config.html`、`blueprints/sql_config.py`

| 功能 | 状态 |
|------|------|
| SQL 模板 CRUD | ✅ |
| Cookie 管理（增删改、测试连接） | ✅ |
| 实例规则关联配置 | ✅ |
| 模型B 配置（API URL / Key / 模型名 / 供应商） | ✅ |
| 用户偏好存储（如上次选择的 SQL 模板） | ✅ |
| 通用 key-value 配置项管理 | ✅ |
| SQL 模板测试执行（返回前10条预览） | ✅ |

**Cookie 安全**：
- 敏感 key（IDATA_COOKIE、MODELB_API_KEY）返回时自动掩码处理（前5 + **** + 后5）
- Cookie 存储于数据库而非配置文件，避免代码泄露

#### 5.9.2 提示词规则管理

**文件**：`blueprints/prompt_rules.py`、`templates/rule_config.html`

| 功能 | 状态 |
|------|------|
| 规则文件 CRUD（Markdown 格式） | ✅ |
| 文件重命名 | ✅ |
| 旧 .txt 文件自动迁移为 .md | ✅ |
| 默认规则初始化（浙江网超/浙江乐采网超/其他乐采网超） | ✅ |
| 实例与规则关联映射 | ✅（在 sql_config 中配置） |

#### 5.9.3 展示配置

**文件**：`templates/settings.html`

系统级展示参数配置（页面标题、主题、分页条数等）。

#### 5.9.4 标签配置

**文件**：`templates/label_config.html`

管理员可查看、增删改系统标注标签（错误标签、备注标签等）。

#### 5.9.5 模型B 配置

**文件**：`templates/modelb_config.html`、`blueprints/model_review.py`（配置部分）

| 功能 | 状态 |
|------|------|
| API URL / Key / 模型名称 / 供应商 | ✅ |
| 连接测试（发送探活请求） | ✅ |
| Key 掩码显示 + 更新 | ✅ |
| 支持未配置时 Mock 模式降级 | ✅ |

---

## 5.10 项目启动与目录结构

> ⚠️ **v2.2 新增**：实用参考信息。

### 5.10.1 启动方式

```bash
cd /Users/zcy/Desktop/模型标注agent
pip install -r requirements.txt
python3 app.py
```

访问 `http://localhost:5000`，默认管理员账号：`admin` / `admin123`

### 5.10.2 项目目录结构

```
模型标注agent/
├── app.py                    # Flask 主入口
├── config.py                 # 全局配置（iData Cookie、环境 URL、实例映射）
├── models.py                 # 数据库模型（8个表）
├── requirements.txt          # Python 依赖
├── blueprints/               # 蓝图路由（7个）
│   ├── auth.py               #   登录认证 + 用户管理
│   ├── data_fetch.py         #   数据拉取核心逻辑
│   ├── dashboard.py          #   看板统计
│   ├── sql_config.py         #   系统配置管理
│   ├── model_review.py       #   模型B互检
│   ├── prompt_rules.py       #   提示词规则
│   └── analysis.py           #   智能分析（Mock）
├── services/                 # 服务层（3个）
│   ├── fetch_service.py      #   数据拉取 + 违规关键词提取
│   ├── stats_service.py      #   统计聚合
│   └── utils.py              #   工具函数
├── templates/                # 前端页面（17个 HTML）
├── static/                   # 静态资源（common.css, config.js）
├── prompt_rules/             # 提示词规则文件（Markdown）
├── instance/                 # SQLite 数据库存储
└── .workbuddy/memory/        # 每日工作日志
```

### 5.10.3 关联文档清单

| 文档 | 路径 | 说明 |
|------|------|------|
| README-backend.md | 根目录 | 后端 API 文档 + 项目结构 |
| README.md | 根目录 | 项目简介 + 数据源表格 |
| 项目规划.md | 根目录 | 项目规划与进度总览（含违规分类24类统计） |
| 数据库表结构说明.md | 根目录 | iData 源表结构说明 |
| 工作总结_20260519.md | 根目录 | 最近一次迭代总结 |
| 提示词与违规关键词关联文档.md | 根目录 | 提示词 V24 与违规关键词映射 |
| 线上全量违规原因探查报告.md | 根目录 | 5实例50000条样本探查结果 |
| violation_report.md | 根目录 | 违规分析报告 |

---

## 6. 数据模型

### 6.1 完整字段表（对照 models.py 修正）

> ⚠️ **v2.1 修正**：原 PRD 数据模型省略了 6 个实际存在的重要字段，以下为完整版本。

#### RawData 表（本地抽样数据，核心表）

| 字段 | 类型 | 说明 | v2.0 是否有 |
|------|------|------|------------|
| id | Integer PK | 自增主键 | ✅ |
| supplier_id | String(100) | 供应商 ID | ✅ |
| label | String(200) | 标签（发布渠道） | ✅ |
| ai_audit_id | String(100) | AI 审核 ID | ✅ |
| audit_id | String(100) | 审核单 ID | ✅ |
| product_id | String(100) | 商品 ID | ✅ |
| ai_result | String(20) | AI 审核结果：合规/违规 | ✅ |
| audit_result | String(50) | 审核单最终状态（通过/驳回/撤回） | ✅ |
| human_reject_item | String(200) | 人审拒绝项（只读参考） | ✅ |
| reject_reason | String(Text) | 拒绝原因（人审） | ✅ |
| human_comment | String(Text) | 人审意见 | ✅ |
| ai_reject_reason | String(Text) | AI 拒绝原因 | ✅ |
| ai_explain | String(Text) | AI 拒绝解释 | ✅ |
| shop_name | String(200) | 店铺名称 | ✅ |
| product_name | String(500) | 商品名称 | ✅ |
| category | String(200) | 类目 | ✅ |
| main_image | String(Text) | 主图（JSON数组URL） | ✅ |
| detail_image | String(Text) | 详情图 | ✅ |
| sku_image | String(Text) | SKU 图 | ✅ |
| spu_image | String(Text) | SPU 图 | ✅ |
| product_link | String(Text) | 商品链接 | ✅ |
| check_result | String(50) | 标注结果（正确/错误） | ✅ |
| annotation | String(Text) | 标注备注 | ✅ |
| instance_code | String(50) | 实例编码 | ✅ |
| created_date | String(50) | 创建日期 | ✅ |
| annotator | String(100) | 标注人 | ✅ |
| random_num | Float | 随机数（排序用） | ✅ |
| change_category | String(200) | 变更类别 | ✅ |
| gmt_created | String(50) | 原始创建时间 | ✅ |
| **fetch_batch_id** | String(100) | 所属拉取批次号 | ❌ 缺失 |
| **source** | String(20) | 数据来源：fetch / upload | ❌ 缺失 |
| **modelb_result** | String(20) | 模型B 审核结果 | ❌ 缺失 |
| **modelb_reason** | String(200) | 模型B 审核原因（简短） | ❌ 缺失 |
| **modelb_detail** | Text | 模型B 审核详细说明 | ❌ 缺失 |
| **modelb_consistent** | Boolean | 双模型是否一致 | ❌ 缺失 |
| **modelb_reviewed** | Boolean | 是否已互检 | ❌ 缺失 |
| **computed_error_reason** | String(200) | 从AI拒绝原因提取的简短原因标签 | ❌ 缺失 |
| created_at | DateTime | 入库时间 | ✅ |

#### FetchLog 表（拉取批次记录）

| 字段 | 类型 | 说明 | v2.0 是否有 |
|------|------|------|------------|
| id | Integer PK | 自增主键 | ✅ |
| batch_id | String(100) | 批次号 | ✅ |
| env | String(50) | 环境 | ✅ |
| instances | String(500) | 实例列表 | ✅ |
| sample_percent | Integer | 抽样比例 | ✅ |
| total_fetched | Integer | 抽样后拉取条数 | ✅ |
| original_total | Integer | 线上原始总数 | ❌ |
| original_compliant | Integer | 线上原始合规数 | ❌ |
| original_non_compliant | Integer | 线上原始违规数 | ❌ |
| compliant_count | Integer | 本地合规数 | ✅ |
| non_compliant_count | Integer | 本地违规数 | ✅ |
| inconsistent_count | Integer | 双模型不一致数 | ❌ |
| fetch_time | DateTime | 拉取时间 | ✅ |
| status | String(20) | 拉取状态 | ✅ |
| review_status | String(20) | 互检状态 | ❌ |
| abort_flag | Boolean | 互检中止标志 | ❌ |
| source | String(20) | 数据来源 | ❌ |
| data_start_date | String(10) | 数据覆盖开始日期 | ❌ |
| data_end_date | String(10) | 数据覆盖结束日期 | ❌ |
| skipped_duplicates | Integer | 跳过的重复数 | ❌ |

#### DailyStats 表（每日统计快照）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| stat_date | String(10) | 统计日期 |
| instance_code | String(50) | 实例编码 |
| total_count | Integer | 审核总数 |
| compliant_count | Integer | 合规数 |
| non_compliant_count | Integer | 违规数 |
| inconsistent_count | Integer | 不一致数 |
| inconsistent_rate | Float | 不一致率 |
| error_reasons | Text | 违规原因 JSON（{tag: count}） |
| batch_id | String(100) | 关联批次号 |
| created_at / updated_at | DateTime | 时间戳 |
| **唯一约束** | (stat_date, instance_code) | |

#### User 表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| username | String(100) UNIQUE | 用户名 |
| password_hash | String(200) | 密码哈希 |
| role | String(20) | admin / annotator |
| name | String(100) | 姓名 |
| daily_quota | Integer | 每日标注额度（默认200） |
| is_active | Boolean | 是否启用 |

#### SqlConfig 表（通用 key-value 配置）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| key | String(100) UNIQUE | 配置键 |
| value | Text | 配置值（JSON 或 字符串） |
| created_at / updated_at | DateTime | 时间戳 |

当前系统使用的 key 列表：IDATA_COOKIE、MODELB_API_URL、MODELB_API_KEY、MODELB_MODEL_NAME、MODELB_SUPPLIER、INSTANCE_RULE_MAPPING、USER_PREF_* 系列

#### SqlTemplate 表（SQL 模板）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| name | String(200) | 模板名称 |
| env | String(50) | 环境 |
| instances | String(500) | 实例列表 |
| api_url | String(500) | API 地址 |
| sql_text | Text | SQL 语句 |
| params_json | Text | 参数定义 JSON |
| modelb_enabled | Boolean | 是否启用模型B互检 |
| modelb_prompt | Text | 模型B专用提示词 |
| created_at / updated_at | DateTime | 时间戳 |

#### Annotation 表（标注记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| raw_data_id | FK → RawData | 关联原始数据 |
| annotator_id | FK → User | 标注员 |
| result | String(20) | 标注结果 |
| error_tag | String(200) | 错误标签 |
| note | Text | 备注 |
| is_submitted | Boolean | 是否已提交 |
| created_at / updated_at | DateTime | 时间戳 |

#### QcRecord 表（质检修正记录）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | Integer PK | 自增 |
| raw_data_id | FK → RawData | 关联原始数据 |
| annotation_id | FK → Annotation | 原始标注 |
| annotator_id | FK → User | 被质检的标注员 |
| qc_user_id | FK → User | 质检人（admin） |
| original_result | String(20) | 原标注结果 |
| original_note | Text | 原备注 |
| corrected_result | String(20) | 修正后结果 |
| corrected_note | Text | 修正后备注 |
| solution | Text | 解决方案 |
| is_notified | Boolean | 是否已通知标注员 |
| batch_id | String(100) | 质检批次号 |
| created_at / updated_at | DateTime | 时间戳 |

---

## 7. API 接口参考

### 7.1 认证 API（auth）

| Method | Path | 说明 | 鉴权 |
|--------|------|------|------|
| POST | `/api/auth/login` | 登录，返回 JWT | 公开 |
| POST | `/api/auth/logout` | 登出 | login_required |
| GET | `/api/auth/me` | 当前用户信息 | login_required |
| GET | `/api/auth/users` | 用户列表 | admin |
| POST | `/api/auth/users` | 添加用户 | admin |
| PUT | `/api/auth/users/<id>` | 更新用户 | admin / 本人 |
| DELETE | `/api/auth/users/<id>` | 停用用户 | admin |
| POST | `/api/auth/users/<id>/reset-password` | 重置密码 | admin |

### 7.2 数据拉取 API（data_fetch）

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/data-fetch` | 触发数据拉取（支持多实例/日期范围/模板） |
| GET | `/api/data/tasks` | 任务批次列表 |
| GET | `/api/data/tasks/<id>` | 批次详情 |
| GET | `/api/data/logs` | 拉取日志 |
| GET | `/api/data/instances` | 实例列表 |
| GET | `/api/data/rules` | 规则列表 |
| POST | `/api/data/export` | 导出数据 |

### 7.3 看板统计 API（dashboard）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/dashboard/stats` | 主统计接口（DailyStats 聚合） |
| GET | `/api/dashboard/overview` | 概览 |
| GET | `/api/dashboard/by-instance` | 按实例 |
| GET | `/api/dashboard/by-user` | 按标注员 |
| GET | `/api/dashboard/trend` | 趋势 |
| GET | `/api/dashboard/logs` | 日志 |
| GET | `/api/dashboard/reason-distribution` | 违规原因分布 |
| GET | `/api/dashboard/inconsistency-rate` | 不一致率（日期筛选） |

### 7.4 模型B互检 API（model_review）

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/model-review/trigger` | 触发互检 |
| GET | `/api/model-review/status/<batch_id>` | 互检进度 |
| PUT | `/api/model-review/abort/<batch_id>` | 中止互检 |
| POST | `/api/config/modelb-test` | 测试模型B连接 |
| GET | `/api/config/modelb` | 获取模型B完整配置 |
| POST | `/api/config/modelb` | 保存模型B配置 |

### 7.5 系统配置 API（sql_config）

| Method | Path | 说明 |
|--------|------|------|
| GET/POST | `/api/sql-config` | SQL 模板列表/新增 |
| GET/PUT/DELETE | `/api/sql-config/<id>` | 模板详情/更新/删除 |
| POST | `/api/sql-config/test` | 测试 SQL 执行 |
| GET | `/api/sql-config/<id>/params` | 获取模板参数 |
| GET/POST | `/api/config/cookie` | Cookie 管理 |
| POST | `/api/config/cookie-test` | Cookie 连接测试 |
| GET/PUT | `/api/config/<key>` | 通用配置项读写 |
| GET/PUT | `/api/config/instance-rule-mapping` | 实例规则关联 |
| GET/POST | `/api/config/user-preference/<key>` | 用户偏好 |

### 7.6 提示词规则 API（prompt_rules）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/prompt-rules` | 规则列表 |
| GET/POST | `/api/prompt-rules/<name>` | 规则内容/创建 |
| PUT | `/api/prompt-rules/<name>` | 更新规则 |
| DELETE | `/api/prompt-rules/<name>` | 删除规则 |
| PUT | `/api/prompt-rules/<name>/rename` | 重命名 |

### 7.7 智能分析 API（analysis）

| Method | Path | 说明 | 状态 |
|--------|------|------|------|
| GET | `/api/analysis/annotator-issues` | 标注员问题分析 | ⚠️ Mock |
| GET | `/api/analysis/annotator-detail` | 标注员详细案例 | ⚠️ Mock |
| GET | `/api/analysis/search` | 知识库搜索 | ⚠️ Mock |
| GET | `/api/analysis/knowledge-sources` | 知识源列表 | ⚠️ Mock |
| GET | `/api/analysis/overview` | 概览数据 | ⚠️ Mock |

---

## 8. 非功能需求

### 8.1 兼容性

- 支持主流浏览器最新版（Chrome 90+、Edge 90+、Safari 15+）
- 不支持 IE 11

### 8.2 性能指标

| 指标 | 目标值 | 现状 |
|------|--------|------|
| 数据拉取响应时间 | < 60s（10000条以内） | ✅ |
| 互检完成时间 | < 5min（1000条） | ✅（Mock模式秒级，真实API取决于带宽） |
| 页面首次加载 | < 3s | ✅ |
| 看板数据查询 | < 2s | ✅（DailyStats 聚合） |
| 并发用户数 | 5（当前阶段） | ✅（SQLite 限制） |

### 8.3 可扩展性

- **后端蓝图架构**：每个模块独立蓝图，新增功能只需新增蓝图注册
- **前后端分离（API 优先）**：所有数据操作通过 fetch API 调用，前端可独立替换
- **数据库无关**：基于 SQLAlchemy ORM，迁移到 PostgreSQL/MySQL 只需修改连接串

### 8.4 数据一致性

- 看板数据必须来自 DailyStats 快照，与线上全量数据保持逻辑一致
- 拉取完成 → 自动生成 DailyStats 快照 → 看板读取快照（非实时查 RawData）

### 8.5 用户体验

- 详情弹窗支持键盘快捷操作（← → ESC）
- 截断字段提供 tooltip 查看详情
- 关键操作有二次确认和明确的状态反馈
- 三种导出模式（本地全量 / 仅违规 / 互检差异）
- 数据拉取实时进度展示（轮询机制）

### 8.6 可复现性

- 数据抽样采用固定种子（MD5(日期+实例)）+ 分层随机，确保结果可被验证
- 增量拉取按 audit_id 去重，避免重复

### 8.7 安全性

- Cookie/API Key 等敏感配置存储在数据库而非代码中
- 返回时自动掩码（前5 + **** + 后5）
- 用户密码使用 werkzeug generate_password_hash 加密存储
- 所有管理 API 均有 admin 角色校验

---

## 9. 风险与缓解

> ⚠️ **v2.1 新增**：原 PRD 完全缺失风险评估。

| 风险 | 严重度 | 影响 | 缓解措施 |
|------|--------|------|---------|
| Cookie 过期导致数据拉取失败 | 🔴 高 | 全系统无法获取新数据 | 401 错误时提供快捷链接跳转 Cookie 管理页；已实现 cookie-test API 可定期验证 |
| SQLite 并发写入瓶颈 | 🟡 中 | 多用户同时操作时性能下降 | 短期：使用 WAL 模式；长期：迁移到 PostgreSQL |
| 模型B API 不可用/限流 | 🟡 中 | 互检无法完成，所有数据进入不一致状态 | 已实现 Mock 模式降级（自动）；支持 abort 中止操作 |
| iData API 限流或变更 | 🔴 高 | 拉取失败 | 默认限制并发线程数=3；支持按日期范围拆分拉取 |
| 单管理员瓶颈 | 🟡 中 | 管理员不可用时系统瘫痪 | 短期：预留多管理员架构（User.role 已支持扩展）；长期：引入角色权限矩阵 |
| 模型B 真实调用成本 | 🟢 低 | 大规模互检成本高 | Mock 模式降低开发测试成本；上线后可配置抽样互检比例 |
| 数据隐私泄露 | 🔴 高 | Cookie / API Key 泄露 | 敏感配置掩码返回、数据库存储；后续需增加 HTTPS + 正式登录体系 |
| 前端状态不一致 | 🟡 中 | 数据拉取中断后页面状态卡死 | 启动时自动清理 >30min 的卡死任务；互检支持 abort_flag 中止 |

---

## 10. 后续迭代计划

### P0（核心闭环 — 当前最优先）

| 任务 | 说明 |
|------|------|
| 标注模块全面接入后端 API | 消除 localStorage 模拟，Annotation 表全链路打通 |
| 看板准确率指标接入 | 接入标注结果统计，计算 AI 审核真实准确率 |
| iData 28 字段完整性修复 | 解决取数字段不全问题 |

### P1（完善增强）

| 任务 | 说明 |
|------|------|
| 智能分析中心接入真实数据 | 替换全部 5 个 Mock API |
| 模型B 从 Mock → 真实 API | 配置后切换为真实模型调用 |
| SQLite → 服务器数据库 | 迁移至 PostgreSQL，建立正式登录与权限体系 |
| 报表导出增强 | PDF/Excel 导出 + 定时自动报告 |
| 细粒度权限控制 | 多管理员支持、标注组管理 |

### P2（体验优化）

| 任务 | 说明 |
|------|------|
| 前端状态管理重构 | 当前为纯 JS + Jinja2，后续可考虑 Vue/React |
| WebSocket 实时通知 | 标注任务分配、质检结果推送 |
| 自动拉取调度 | Cron 定时拉取 + 互检，减少人工操作 |
| 标注一致性校验 | 同商品多人标注交叉验证 |

---

## 11. 附录：代码审查发现的 PRD 偏差清单

| # | 偏差类型 | v2.0 PRD 描述 | 实际代码 | 建议 |
|---|---------|-------------|---------|------|
| 1 | **缺失模块** | 未提及"智能分析中心" | `analysis.py` + `analysis_center.html` 已有基础骨架（5个API，当前Mock）。⚠️ 此模块由其他同事开发，后续整合。 | v2.1 已补充 §5.8 |
| 2 | **缺失模块** | 未提及"登录与权限体系" | `auth.py` + `login.html` + User 表完整实现 | v2.1 已补充 §5.1 |
| 3 | **缺失模块** | 未提及"SQL配置管理" | `sql_config.py` + `sql_config.html`，支持 Cookie/SQL模板/实例关联/用户偏好 | v2.1 已补充 §5.9.1 |
| 4 | **数据模型残缺** | 字段列不完整 | 代码中 RawData 表有 8 个额外字段（modelb_result/reason/detail/consistent/reviewed, computed_error_reason, fetch_batch_id, source） | v2.1 已补全 §6 |
| 5 | **数据模型残缺** | 未列 FetchLog 的 10 个新字段 | original_total/compliant/non_compliant, inconsistent_count, review_status, abort_flag, source, data_start/end_date, skipped_duplicates | v2.1 已补全 |
| 6 | **实现状态过时** | "标注模块当前使用 localStorage 模拟" | Annotation 表 + API 已实现，但前端可能有残留 | 需逐一确认并清理 |
| 7 | **实现状态过时** | "模型B接入"列为后续迭代 | 已完整实现（Mock + 真实API + 中止 + 进度查询） | v2.1 已修正为 P1 |
| 8 | **缺少技术架构** | 无架构说明 | Flask蓝图 + SQLAlchemy + SQLite 架构已稳定 | v2.1 已补充 §3 |
| 9 | **缺少风险评估** | 无风险评估 | 8 个已识别风险 | v2.1 已补充 §9 |
| 10 | **缺少 API 参考** | 无 API 文档 | 76+ API 端点 | v2.1 已补充 §7 |
| 11 | **系统设置过于简略** | 仅3行描述 | 5 个独立页面（SQL配置/提示词规则/展示/标签/模型B） | v2.1 已展开 §5.9 |
| 12 | **字段名不一致** | "解决方案"字段描述不准确 | 实际在 QcRecord.solution 中 | 已对齐 |
| 13 | **v2.2 新增** | 协作关系未标注 | QC修正/Badcase管理/智能分析由其他同事开发 | v2.2 已在文档头和 §5.6-5.8 标注 |

---

> **文档维护说明**：本文档 v2.2 基于 2026-05-20 代码库全量审查 + 项目规划文档交叉校验。后续代码变更或协作模块整合时，请同步更新本文档对应章节。建议每次迭代后在版本修订记录中登记变更。
