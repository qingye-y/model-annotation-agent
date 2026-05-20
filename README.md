# model-annotation-agent

商品审核大模型质检标注工作台

## 技术栈

- Flask 后端 + Jinja2 模板 + ApexCharts 前端
- 蓝图架构：dashboard / task_history / sql_config / data_fetch
- FetchLog 和 DailyStats 模型，T+1 数据报表

## 主要功能

- 批量任务管理（批量拉取、抽样、导出）
- 批次详情页（批次概览、明细数据、导出）
- 看板统计（按日期/实例维度、违规原因分析）
- 互检差异对比
- 数据导出（原始全量 / 违规数据 / 互检差异）

## 数据源

| 实例 | 名称 | 环境 |
|------|------|------|
| ZJWC | 浙江网超 | 云环境 |
| HWCS | 浙江乐采网超 | 云环境 |
| HNLCWC | 湖南乐采网超 | 云环境 |
| YNLCY | 云南乐采云 | 乐采云环境 |
| GXLCY | 广西乐采云 | 乐采云环境 |
