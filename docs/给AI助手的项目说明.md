# 给 AI 助手的项目说明

> 把下面「项目背景」到「常见问题」的全部内容复制粘贴给 AI，让它帮你解答使用中的问题
> 版本：v1.6.0 | 日期：2026-05-27

---

## 项目背景

这是一个**商品审核大模型质检标注工作台**，用 Flask + SQLite 搭建。

**它的作用是**：
1. 从 iData 数据平台自动拉取 AI 审核数据（商品审核记录）
2. 标注员对 AI 审核结果进行人工质检标注
3. Model B 双模型互检，标记不一致样本
4. 质检员复核修正错误标注
5. Badcase 管理：错误样本归因分析

**技术栈**：Flask 后端 + Jinja2 前端模板 + SQLite 数据库

---

## 文件结构

```
模型标注agent/
├── app.py              # Flask 主入口，运行这个文件来启动整个系统
├── config.py           # ⚠️ 配置文件，需要修改 Cookie 和密钥
├── models.py           # 数据库表结构定义
├── requirements.txt   # Python 依赖列表
│
├── blueprints/         # 后端 API 模块
│   ├── data_fetch.py  # 核心：线上取数 API
│   ├── dashboard.py   # 看板统计 API
│   ├── sql_config.py  # SQL 配置 API
│   ├── dispatch.py    # 任务分发
│   ├── model_review.py # Model B 互检
│   ├── auth.py        # 登录认证
│   └── ...            # 其他 API
│
├── services/           # 业务逻辑层
│   └── fetch_service.py # 取数核心逻辑
│
├── templates/          # 前端页面（HTML 文件，通过浏览器访问）
│   ├── index.html     # 主框架（侧边导航）
│   ├── dashboard.html # 数据看板
│   ├── model_task.html # 机审任务中心
│   ├── batch_detail.html # 批次详情
│   ├── annotation_list.html # 标注任务
│   ├── dispatch_center.html # 任务调度中心
│   └── ...
│
└── static/             # 前端样式和 JS
```

---

## 快速启动步骤

1. **安装依赖**：在终端运行 `pip install -r requirements.txt`
2. **修改配置**：打开 `config.py`，改两处：
   - `SECRET_KEY`：改成随机字符串
   - `IDATA_COOKIE`：换成有效的 iData Cookie
3. **启动服务**：在终端运行 `python3 app.py`
4. **访问系统**：浏览器打开 `http://localhost:5000`
5. **登录账号**：`admin` / `admin123`

---

## config.py 关键配置说明

```
SECRET_KEY = 'xxx'          # Flask 会话密钥，随便改
IDATA_COOKIE = '...'        # ⚠️ iData 认证 Cookie，必须换成有效的
ENV_CONFIG = {...}          # 环境配置，一般不改
INSTANCE_NAMES = {...}       # 实例中文名映射，一般不改
```

**Cookie 获取方法**：登录 iData（idata.cai-inc.com）→ F12 → Network → 任意请求 → Headers → 复制完整 Cookie

---

## 核心页面和功能

| 页面 | 路径 | 功能 |
|------|------|------|
| 机审任务中心 | /model-task | 拉取 iData 数据、查看批次列表 |
| 批次详情 | /batch-detail/<id> | 查看单批次数据明细、导出 |
| 数据看板 | /dashboard | 违规率/不一致率/Top原因/趋势图 |
| 标注任务 | /annotation-list | 标注员执行质检标注 |
| 任务调度中心 | /dispatch-center | 生成任务、分配给标注员 |
| 质检中心 | /qc-center | 质检员复核 |
| SQL 配置 | /sql-config | 在线修改取数 SQL |
| 账号管理 | /account-management | 增删改查用户 |

---

## 数据库说明

- 数据库文件：`app.db`（SQLite，启动后自动创建）
- 主要数据表：
  - `fetch_log`：取数批次记录
  - `raw_data`：原始商品数据（28字段）
  - `daily_stats`：每日统计快照
  - `annotation`：标注记录
  - `user`：用户账号

---

## iData 数据源

| 实例编码 | 中文名 | 所属环境 |
|----------|--------|----------|
| ZJWC | 浙江网超 | 云环境 |
| HWCS | 浙江乐采网超 | 云环境 |
| HNLCWC | 湖南乐采网超 | 云环境 |
| YNLCY | 云南乐采云 | 乐采云环境 |
| GXLCY | 广西乐采云 | 乐采云环境 |

iData API 地址：
- 云环境：`https://idata.cai-inc.com/api/idas/inner/fetchData/getData`
- 乐采云：`https://idata.cai-inc.com/lcy_idas/api/idas/inner/fetchData/getData`

---

## 常见问题

**Q：启动报错 `ModuleNotFoundError: No module named 'flask'`**
A：运行 `pip install -r requirements.txt`

**Q：拉取数据提示 `401 Unauthorized` 或 `Cookie 已过期`**
A：iData Cookie 失效，重新获取并更新 `config.py` 第 16 行

**Q：端口 5000 被占用**
A：修改 `app.py` 最后一行端口号，如 `port=5001`

**Q：数据拉取成功但看板显示为空**
A：检查日期范围筛选是否包含拉取数据的日期

**Q：登录提示用户名或密码错误**
A：默认账号是 `admin` / `admin123`，注意全小写

---

## 参考文档

详细说明请阅读以下文件（在项目的 `docs/` 目录下）：
- `docs/使用者手册.md` — 完整使用说明
- `docs/给完全不懂技术的人的安装指南.md` — 从零安装教程
- `docs/产品PRD.md` — 产品需求文档
- `docs/技术文档.md` — 技术架构文档
- `docs/数据库表结构说明.md` — 数据库字段详解

---

## 如果遇到文档没有覆盖的问题

可以提供以下信息来更快获得帮助：
1. **做了什么操作**（截图或描述步骤）
2. **看到了什么错误**（完整错误信息）
3. **预期应该是什么**
4. **`config.py` 里 `IDATA_COOKIE` 那一行**（开头和结尾几个字符就行，不需要完整内容）

