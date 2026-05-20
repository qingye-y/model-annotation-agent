import os

# ========== 通用配置 ==========
SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///app.db'
SQLALCHEMY_TRACK_MODIFICATIONS = False

# 数据拉取配置
FETCH_PAGE_SIZE = 500  # 每次请求的条数上限
FETCH_CONCURRENCY = 3  # 并发拉取的线程数

# ========== iData 平台认证 Cookie ==========
# 登录 iData 平台后，在浏览器开发者工具中复制完整的 Cookie 请求头
# 包含所有字段：_zcy_log_client_uuid, _ga, cna, ELEARNING_SESSION, uid,
# Authorization, token, __sso_token__, congress, online_ticket, __idaas_token__ 等
IDATA_COOKIE = os.environ.get('IDATA_COOKIE') or "_zcy_log_client_uuid=dd589ad0-2fab-11f0-8393-db541f25b0e9; _ga=GA1.1.473497680.1762493730; _ga_Z4KXEBY4VP=GS2.1.s1766111120$o5$g1$t1766111143$j37$l0$h0; cna=7f8dea107e644b638388a7f4800ca0b5; uid=C02049; redirect=; Authorization=eyJhbGciOiJIUzUxMiJ9.eyJyZWFsTmFtZSI6IuadjuWtkOeOpSIsIm5pY2tuYW1lIjoi6Z2S5LmfIiwibW9iaWxlIjoiMTMxNTUxODEwMzEiLCJlbXBsb3llZUlkIjoiQzAyMDQ5IiwiaWQiOjUwMTMzOSwic3lzQWRtaW4iOjAsImF2YXRhciI6Imh0dHBzOi8vc3RhdGljLWxlZ2FjeS5kaW5ndGFsay5jb20vbWVkaWEvbFFEUE00Ukc4cFkxZXozTkE4Zk5BOGF3bHNXaFVkbWtHMGtIYXBkQnhyQjlBQV85NjZfOTY3LmpwZyIsImV4cCI6MTc3ODk4MjYyMywiZW1haWwiOiJsaXppeXVlQGNhaS1pbmMuY29tIiwidXNlcm5hbWUiOiJsaXppeXVlIn0.aYECe1VIybKfCYeNswXa_UQntrRcFms7wHHGmFSKuyESzUqki-phdAcP90Z4LWBboC8lVRHo1eOzpI-1T9euWg"

# ========== 环境与实例配置 ==========
# 注意：两个环境的 API 地址不同！
# 必须使用 getData 接口（getCache 只返回缓存状态，不返回数据）
# 请求必须包含 datasourceType 参数（hive/spark/presto 等均可）
ENV_CONFIG = {
    "云环境": {
        "query_api_url": "https://idata.cai-inc.com/api/idas/inner/fetchData/getData",
        "instances": ["ZJWC", "HWCS", "HNLCWC"]
    },
    "乐采云环境": {
        "query_api_url": "https://idata.cai-inc.com/lcy_idas/api/idas/inner/fetchData/getData",
        "instances": ["YNLCY", "GXLCY"]
    }
}

# iData 数据源类型（getData 接口必需参数）
IDATA_DATASOURCE_TYPE = os.environ.get('IDATA_DATASOURCE_TYPE') or "hive"

# 实例中文名称映射
INSTANCE_NAMES = {
    "ZJWC": "浙江网超",
    "HWCS": "浙江乐采网超",
    "HNLCWC": "湖南乐采网超",
    "YNLCY": "云南乐采云",
    "GXLCY": "广西乐采云"
}

# 规则ID到名称的映射
RULE_CONFIG = {
    "rule_001": "通用商品审核规则",
    "rule_002": "食品类审核规则",
    "rule_003": "服装类审核规则",
    "rule_004": "美妆类审核规则",
    "rule_005": "3C数码审核规则"
}