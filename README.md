# 商品审核大模型质检标注工作台

> 快速启动指南 — 详细说明请阅读 `docs/使用者手册.md`

---

## 一、项目简介

本工作台面向**商品审核大模型质检**的全流程标注工具，覆盖：

1. **机审任务中心** — 从 iData 数据平台自动拉取 AI 审核数据，分层抽样入库
2. **批次详情** — 查看、筛选、导出批次数据
3. **数据看板** — 违规率/不一致率/Top 违规原因实时展示
4. **标注任务** — 标注员执行质检标注，Model B 互检
5. **任务调度** — 规则配置、任务分发、标注员管理
6. **质检中心** — 质检员复核修正
7. **Badcase 管理** — 错误样本归因分析
8. **系统配置** — SQL 模板管理、Cookie 管理、规则配置

---

## 二、快速启动

### 2.1 环境要求

| 要求 | 说明 |
|------|------|
| Python | 3.8 及以上 |
| 网络 | 可访问 iData 数据平台（idata.cai-inc.com）|
| iData 账号 | 需要有效的 Cookie 认证 |

### 2.2 安装依赖

```bash
# 进入项目目录
cd 模型标注agent

# 安装 Python 依赖
pip install -r requirements.txt
```

### 2.3 配置（首次使用必须）

**① 修改 config.py 中的敏感信息：**

```python
# config.py 第 4 行：修改密钥
SECRET_KEY = 'your-own-secret-key-here'

# config.py 第 16 行：替换为你的 iData Cookie
IDATA_COOKIE = 'your-idata-cookie-here'
```

Cookie 获取方法：
1. 登录 iData（idata.cai-inc.com）
2. 按 F12 打开开发者工具 → Network（网络）标签
3. 任意点击一个请求，复制 Request Headers 中的完整 `Cookie:` 值
4. 粘贴到 config.py 中

**② 如需更换数据库路径：**

```python
# config.py 第 5 行（可选）
SQLALCHEMY_DATABASE_URI = 'sqlite:///app.db'
```

### 2.4 启动服务

```bash
python3 app.py
```

输出示例：
```
* Running on http://127.0.0.1:5000/
* Running on http://localhost:5000/
```

### 2.5 访问系统

```
浏览器打开：http://localhost:5000
```

| 账号 | 密码 | 角色 |
|------|------|------|
| admin | admin123 | 管理员 |
| liziyue | admin123 | 标注员 |

> ⚠️ 首次启动后请立即修改 admin 密码！

---

## 三、核心操作流程

### 3.1 拉取数据（机审任务中心 → 拉取数据）

1. 左侧菜单点击「**机审任务中心**」
2. 选择环境（云环境 / 乐采云环境）
3. 勾选实例（可多选）
4. 选择日期范围（默认近7天）
5. 设置抽样比例（默认 5%）
6. 点击「**立即拉取**」
7. 等待完成后自动展示统计结果

### 3.2 查看看板（数据看板）

- 顶部展示 KPI 卡片：总审核数、合规数、违规数、违规率
- 违规原因饼图：Top 10 原因 + 其他可展开
- 每日趋势折线图：支持按日期和实例筛选

### 3.3 批次详情（机审任务中心 → 批次列表 → 查看详情）

- 概览卡片：线上总数、本次拉取数、合规/违规数
- 明细筛选：按审核结果、实例、关键词、互检状态筛选
- 导出 CSV：支持原始全量 / 违规数据 / 互检差异

### 3.4 标注任务（标注任务）

1. 管理员在「任务调度」→「规则调度」中生成标注任务
2. 标注员登录后在「**标注任务**」中看到自己的任务
3. 点击商品 → 弹出详情 → 选择正确/错误 → 填写备注 → 提交

---

## 四、文件结构速查

```
模型标注agent/
├── app.py                    # 🔴 Flask 主入口，启动文件
├── config.py                 # 🔴 配置（Cookie/密钥/实例映射）— 需修改
├── models.py                 # 数据库模型定义
├── requirements.txt          # Python 依赖列表
│
├── blueprints/              # 后端 API 模块
│   ├── data_fetch.py         # 核心取数 API
│   ├── dashboard.py          # 看板统计 API
│   ├── sql_config.py         # SQL 配置 API
│   ├── auth.py              # 登录认证 API
│   ├── dispatch.py          # 任务分发 API
│   ├── model_review.py       # 模型互检 API
│   ├── prompt_rules.py      # 提示词规则 API
│   └── analysis.py          # 数据分析 API
│
├── services/                # 业务逻辑层
│   ├── fetch_service.py      # 取数核心逻辑（分层抽样/iData调用）
│   ├── stats_service.py      # 统计服务
│   └── utils.py             # 工具函数
│
├── templates/               # 前端 HTML 页面（15个）
│   ├── index.html           # 主框架（侧边导航）
│   ├── login.html           # 登录页
│   ├── dashboard.html        # 数据看板
│   ├── model_task.html       # 机审任务中心
│   ├── batch_detail.html     # 批次详情
│   ├── dispatch_center.html   # 任务调度中心
│   ├── annotation_list.html  # 标注任务列表
│   ├── qc_center.html        # 质检中心
│   ├── badcase_center.html   # Badcase 管理
│   ├── sql_config.html       # SQL 配置页
│   ├── settings.html        # 系统配置
│   └── ...                  # 更多页面
│
├── static/                  # 前端静态资源
│   ├── common.css           # 全局样式
│   ├── common.js            # 公共 JS 函数
│   └── config.js            # 前端配置（标注标签/规则等）
│
├── prompt_rules/            # 审核规则文档
│   ├── 浙江网超审核规则.md
│   ├── 浙江乐采网超审核规则.md
│   └── 其他乐采网超审核规则.md
│
└── docs/                   # 完整文档（详细阅读此处）
    ├── 使用者手册.md         # 👈 详细使用说明（必读）
    ├── 安装指南.md          # 👈 零基础安装指南（发给不懂技术的人）
    ├── 给AI助手的项目说明.md # 👈 复制给 AI，AI 就能回答问题
    ├── 产品PRD.md           # 整体产品需求文档
    ├── 技术文档.md          # 整体技术架构文档
    ├── 机审任务中心_完整文档.md # 机审任务中心：PRD + 技术方案（合集）
    ├── 数据库表结构说明.md
    └── ...                  # 其他模块 PRD 和技术方案
```

---

## 五、常见问题

### Q1：启动报错 `ModuleNotFoundError`

```bash
# 缺少依赖，执行：
pip install -r requirements.txt
```

### Q2：拉取数据提示 `401 Unauthorized`

**原因**：iData Cookie 已过期。

**解决**：
1. 重新登录 iData（idata.cai-inc.com）
2. F12 → Network → 复制新的 Cookie
3. 更新 `config.py` 中的 `IDATA_COOKIE` 值
4. 重启服务

### Q3：数据库文件不存在

首次启动时 `app.db` 会自动创建在项目根目录。如需重建，删除 `app.db` 后重启即可。

### Q4：端口 5000 被占用

修改 `app.py` 最后一行：

```python
app.run(host='0.0.0.0', port=5001)  # 改为你想要的端口
```

### Q5：中文显示乱码

确保 `config.py` 未被修改为非 UTF-8 编码，Python 3 默认 UTF-8。

---

## 六、升级与维护

| 操作 | 方法 |
|------|------|
| 更新代码 | 替换 `app.py`、`blueprints/`、`services/`、`templates/`、`static/` |
| 备份数据 | 复制 `app.db` 文件 |
| 恢复数据 | 替换 `app.db` 后重启 |
| 查看日志 | `flask.log`（如已配置）|
| 初始化管理员 | 删除 `app.db` 后重启，或在账号管理页面创建 |

---

> 📖 **详细说明请阅读 `docs/使用者手册.md`**
>
> 🤖 **AI 助手使用：请阅读 `docs/给AI助手的项目说明.md`**
>
> 🔧 **机审模块文档：`docs/机审任务中心_完整文档.md`（PRD + 技术方案合集）**
