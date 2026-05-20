# -*- coding: utf-8 -*-
"""Services package - 桥接层

这个文件重新导出 blueprints 中的业务逻辑函数，保持向后兼容。
新代码可以直接 from services import xxx 使用。
"""

# 从 blueprints 重新导出（保持兼容）
# 注意：这里需要延迟导入避免循环依赖

def get_services():
    """返回可用的服务函数映射"""
    return {
        'to_beijing_time': 'services.utils',
        'build_sql': 'blueprints.data_fetch',
        'execute_sql_query': 'blueprints.data_fetch',
        'extract_violation_keywords': 'blueprints.data_fetch',
        'extract_error_reason': 'blueprints.data_fetch',
        'generate_daily_stats': 'blueprints.data_fetch',
        'update_daily_stats_inconsistency': 'blueprints.data_fetch',
    }


# 实际导入 - 由于循环依赖，这些直接使用 blueprints 中的函数
def to_beijing_time(dt):
    """导入自 utils"""
    from services.utils import to_beijing_time as _func
    return _func(dt)


def build_sql(instance, start_date, end_date):
    """导入自 fetch_service"""
    from services.fetch_service import build_sql as _func
    return _func(instance, start_date, end_date)


def execute_sql_query(sql, instance, env=None):
    """导入自 fetch_service"""
    from services.fetch_service import execute_sql_query as _func
    return _func(sql, instance, env)


def extract_violation_keywords(reject_reason):
    """导入自 fetch_service"""
    from services.fetch_service import extract_violation_keywords as _func
    return _func(reject_reason)


def extract_error_reason(reject_reason):
    """导入自 fetch_service"""
    from services.fetch_service import extract_error_reason as _func
    return _func(reject_reason)


def generate_daily_stats(instance, start_date, end_date, batch_id, original_total=0, original_compliant=0, original_non_compliant=0):
    """导入自 fetch_service"""
    from services.fetch_service import generate_daily_stats as _func
    return _func(instance, start_date, end_date, batch_id, original_total, original_compliant, original_non_compliant)


def update_daily_stats_inconsistency(fetch_log):
    """导入自 fetch_service"""
    from services.fetch_service import update_daily_stats_inconsistency as _func
    return _func(fetch_log)


def get_instance_rule_mapping():
    """导入自 fetch_service"""
    from services.fetch_service import get_instance_rule_mapping as _func
    return _func()


def query_idata(sql, instance, env=None):
    """导入自 stats_service"""
    from services.stats_service import query_idata as _func
    return _func(sql, instance, env)


def get_daily_stats_sql(template, instance, start_date, end_date, year='2026'):
    """导入自 stats_service"""
    from services.stats_service import get_daily_stats_sql as _func
    return _func(template, instance, start_date, end_date, year)