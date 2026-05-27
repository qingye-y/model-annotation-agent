# 商品审核大模型质检标注工作台 - 后端 API

> ⚠️ 本文件仅作为后端 API 快速索引，详细说明请阅读 `docs/使用者手册.md`

## 项目结构

```
/project/
├── app.py              # Flask 主入口（启动文件）
├── config.py           # 所有配置项（Cookie/密钥/实例映射）— 需修改！
├── models.py           # 数据库表模型（SQLAlchemy）
├── requirements.txt    # Python 依赖
│
├── blueprints/         # 8 个蓝图 API 模块
│   ├── auth.py         # 登录认证 / 用户管理
│   ├── data_fetch.py   # 核心：线上取数 API
│   ├── dashboard.py    # 看板统计 API
│   ├── sql_config.py   # SQL 模板配置
│   ├── dispatch.py     # 任务分发
│   ├── model_review.py # Model B 互检
│   ├── prompt_rules.py # 提示词规则
│   ├── analysis.py     # 数据分析
│   ├── model_task_history.py
│   └── task_history.py
│
├── services/           # 业务逻辑层
│   ├── fetch_service.py  # 取数核心逻辑（分层抽样/iData调用）
│   ├── stats_service.py  # 统计服务
│   └── utils.py          # 工具函数
│
└── templates/ / static/  # 前端页面和资源
```

## 快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 修改 config.py 中的 IDATA_COOKIE（第16行）
# 3. 启动
python3 app.py

# 4. 访问 http://localhost:5000
# 5. 默认账号：admin / admin123
```

## 核心 API 路由

| 模块 | 路由前缀 | 核心接口 |
|------|----------|----------|
| data_fetch | /api/data-fetch | POST 取数 / GET 批次列表 / DELETE 批次 |
| dashboard | /api/dashboard | GET stats / reason-distribution / inconsistency-rate |
| sql_config | /api/sql-config | CRUD SQL模板 / test执行 / Cookie管理 |
| dispatch | /api/dispatch | task-pool / assign / history / annotator-load |
| model_review | /api/model-review | trigger / status / review-items |
| auth | /api/auth | login / logout / users / user-preference |

详细 API 文档请参考 `docs/技术文档.md`。
服务启动后访问 http://localhost:5000

## 配置 iData Cookie

1. 登录 iData 平台
2. 打开浏览器开发者工具（F12）
3. 找到任意 API 请求，复制 Request Headers 中的 Cookie
4. 粘贴到 config.py 的 IDATA_COOKIE 常量中

```python
# config.py
IDATA_COOKIE = "_zcy_log_client_uuid=xxx; _ga=xxx; ..."
```

## API 接口文档

### 健康检查
- GET /api/health - 检查服务状态

### 认证 API (/api/auth)
- POST /api/auth/login - 用户登录
- GET /api/auth/users - 用户列表
- POST /api/auth/users - 添加用户
- PUT /api/auth/users/<username> - 更新用户
- DELETE /api/auth/users/<username> - 删除用户

### 数据拉取 API (/api/data)
- GET /api/data/instances - 获取实例列表
- GET /api/data/rules - 获取规则列表
- POST /api/data/fetch - 从 iData 拉取数据
- POST /api/data/fetch-batch - 批量拉取
- GET /api/data/tasks - 任务列表
- GET /api/data/tasks/<task_id> - 任务详情
- POST /api/data/tasks/<task_id>/annotate - 更新标注结果
- GET /api/data/stats - 数据统计
- GET /api/data/logs - 拉取日志

### 看板统计 API (/api/dashboard)
- GET /api/dashboard/overview - 概览统计
- GET /api/dashboard/by-instance - 按实例统计
- GET /api/dashboard/by-rule - 按规则统计
- GET /api/dashboard/by-user - 按标注员统计
- GET /api/dashboard/trend - 每日趋势
- GET /api/dashboard/qc-stats - 质检统计

## 示例：拉取数据

```bash
# 拉取浙江网超的通用商品审核规则数据
curl -X POST http://localhost:5000/api/data/fetch \
  -H "Content-Type: application/json" \
  -d '{"instance": "ZJWC", "rule_id": "rule_001", "fetch_type": "incremental", "date": "2026-05-10"}'
```

## 环境配置

配置文件 config.py 中预置了两个环境：

- **云环境**: query_api_url = https://idata.cai-inc.com/api/idas/inner/fetchData/getCache
- **乐采云环境**: query_api_url = https://idata.cai-inc.com/lcy_idas/api/idas/inner/fetchData/getData

支持的实例：ZJWC（浙江网超）、HWCS（浙江乐采网超）、HNLCWC（湖南乐采网超）、YNLCY（云南乐采云）、GXLCY（广西乐采云）