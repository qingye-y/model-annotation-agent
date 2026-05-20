# -*- coding: utf-8 -*-
"""统计服务模块"""

import json
from datetime import datetime, timedelta
from config import ENV_CONFIG


def get_idata_cookie():
    """获取 iData 的 Cookie（无参数版本，与蓝图兼容）

    从数据库获取 Cookie，优先数据库，没有则返回空字符串
    """
    from models import SqlConfig

    # 从数据库读取 key='IDATA_COOKIE' 的配置
    config = SqlConfig.query.filter_by(key='IDATA_COOKIE').first()
    if config and config.value:
        return config.value

    # 回退到空字符串（与蓝图版本一致）
    return ''


def get_env_config(env):
    """获取环境配置"""
    return ENV_CONFIG.get(env, {})


def get_default_sql_template(instance_code):
    """获取默认 SQL 模板"""
    from models import SqlTemplate

    template = SqlTemplate.query.filter(
        SqlTemplate.instances.like(f'%{instance_code}%')
    ).first()

    return template


def get_env_by_instance(instance_code):
    """根据实例获取环境"""
    lcy_instances = ['YNLCY', 'GXLCY']
    if instance_code in lcy_instances:
        return '乐采云环境'
    return '云环境'


def query_idata(sql, instance, env=None):
    """查询 iData"""
    import requests
    from config import IDATA_COOKIE as DEFAULT_COOKIE, IDATA_DATASOURCE_TYPE

    if env is None:
        env = get_env_by_instance(instance)

    env_config = get_env_config(env)
    query_api_url = env_config.get('query_api_url')

    if not query_api_url:
        return {'error': f'未找到环境 {env} 的 API URL'}

    cookie = get_idata_cookie() or DEFAULT_COOKIE

    # 构建请求：以完整的 cookie 字符串作为 HTTP Cookie header（与原始成功代码一致）
    # payload 中不传 cookie 字段
    headers = {'Content-Type': 'application/json', 'Cookie': cookie}

    payload = {
        'sql': sql,
        'instance': instance,
        'datasourceType': IDATA_DATASOURCE_TYPE
    }

    try:
        resp = requests.post(
            query_api_url,
            json=payload,
            headers=headers,
            timeout=300
        )
        resp.raise_for_status()
        result = resp.json()

        # iData 返回格式：{success: true, data: {values: [...], count: N, headers: [...]}}
        if isinstance(result, dict) and result.get('success') is False:
            error_msg = result.get('error', result.get('message', 'iData 接口返回错误'))
            return {'error': error_msg}

        # 正确解析 data 中的 values 数组
        if isinstance(result, dict) and 'data' in result:
            data_obj = result['data']
            if isinstance(data_obj, dict) and 'values' in data_obj:
                return data_obj['values']
            return data_obj
        elif 'data' in result:
            return result['data']
        elif 'result' in result:
            return result['result']
        return result

    except requests.exceptions.RequestException as e:
        return {'error': str(e)}


def get_daily_stats_sql(template, instance, start_date, end_date, year='2026'):
    """获取 DailyStats 查询 SQL"""
    if template and template.sql_text:
        sql = template.sql_text
    else:
        sql = "SELECT * FROM daily_stats WHERE 1=1"

    return sql


def extract_violation_type(reject_reason):
    """提取违规类型"""
    if not reject_reason:
        return '其他'

    reason_lower = reject_reason.lower()

    # 类型映射
    type_map = {
        '主图': ['主图', '首图', '封面图'],
        '详情': ['详情', '详细图', '描述'],
        'SKU': ['sku', 'SKU', '规格图'],
        '名称': ['名称', '标题', '商品名称'],
        '描述': ['描述', '说明'],
        '价格': ['价格', '售价', '定价'],
        '规格': ['规格', '型号'],
        '资质': ['资质', '许可', '证明'],
        '侵权': ['侵权', '盗图', '假冒'],
        '虚假': ['虚假', '夸大', '欺诈'],
    }

    for vtype, keywords in type_map.items():
        for kw in keywords:
            if kw in reason_lower:
                return vtype

    return '其他'


def get_reason_distribution_sql(template, instance, start_date, end_date, year='2026'):
    """获取违规原因分布 SQL"""
    if template and template.sql_text:
        sql = template.sql_text
    else:
        sql = "SELECT reject_reason FROM raw_data WHERE 1=1"

    return sql


def aggregate_daily_stats(stats_records, start_date, end_date):
    """聚合 DailyStats 统计数据

    Args:
        stats_records: DailyStats 查询结果列表
        start_date: 开始日期 YYYYMMDD
        end_date: 结束日期 YYYYMMDD

    Returns:
        dict: 聚合后的统计数据
    """
    total_count = 0
    compliant_count = 0
    non_compliant_count = 0
    inconsistent_count = 0
    by_date_map = {}
    by_instance_map = {}
    violation_reason_map = {}

    for record in stats_records:
        rec_total = record.total_count or 0
        rec_compliant = record.compliant_count or 0
        rec_non_compliant = record.non_compliant_count or 0
        rec_inconsistent = record.inconsistent_count or 0

        total_count += rec_total
        compliant_count += rec_compliant
        non_compliant_count += rec_non_compliant
        inconsistent_count += rec_inconsistent

        date_str = record.stat_date
        if date_str not in by_date_map:
            by_date_map[date_str] = {'total': 0, 'compliant': 0, 'non_compliant': 0, 'inconsistent': 0}
        by_date_map[date_str]['total'] += rec_total
        by_date_map[date_str]['compliant'] += rec_compliant
        by_date_map[date_str]['non_compliant'] += rec_non_compliant
        by_date_map[date_str]['inconsistent'] += rec_inconsistent

        inst = record.instance_code
        if inst not in by_instance_map:
            by_instance_map[inst] = {'total': 0, 'compliant': 0, 'non_compliant': 0, 'inconsistent': 0}
        by_instance_map[inst]['total'] += rec_total
        by_instance_map[inst]['compliant'] += rec_compliant
        by_instance_map[inst]['non_compliant'] += rec_non_compliant
        by_instance_map[inst]['inconsistent'] += rec_inconsistent

        if record.error_reasons:
            try:
                reasons = json.loads(record.error_reasons)
                if isinstance(reasons, dict):
                    for tag, count in reasons.items():
                        violation_reason_map[tag] = violation_reason_map.get(tag, 0) + count
            except (json.JSONDecodeError, TypeError):
                pass

    violation_rate = round(non_compliant_count / total_count * 100, 2) if total_count > 0 else 0
    inconsistent_rate = round(inconsistent_count / total_count * 100, 2) if total_count > 0 else 0

    # 构建日期范围数组
    date_range = []
    try:
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        current = start_dt
        while current <= end_dt:
            date_range.append(current.strftime('%Y%m%d'))
            current += timedelta(days=1)
    except Exception as e:
        print(f"[ERROR] 日期解析失败: {e}")

    by_date = []
    for date_str in date_range:
        data = by_date_map.get(date_str, {'total': 0, 'compliant': 0, 'non_compliant': 0, 'inconsistent': 0})
        total = data['total']
        non_compliant = data['non_compliant']
        inconsistent = data['inconsistent']
        vr = round(non_compliant / total * 100, 2) if total > 0 else 0
        ir = round(inconsistent / total * 100, 2) if total > 0 else 0
        by_date.append({
            'date': date_str,
            'total': total,
            'compliant': data['compliant'],
            'non_compliant': non_compliant,
            'inconsistent_count': inconsistent,
            'violation_rate': vr,
            'inconsistent_rate': ir
        })

    # Top 违规原因
    top_violation_reasons = []
    if violation_reason_map:
        sorted_reasons = sorted(violation_reason_map.items(), key=lambda x: x[1], reverse=True)[:10]
        total_violations = sum(violation_reason_map.values())
        for tag, count in sorted_reasons:
            percentage = round(count / total_violations * 100, 1) if total_violations > 0 else 0
            top_violation_reasons.append({
                'name': tag,
                'value': count,
                'percentage': percentage
            })

    return {
        'total_count': total_count,
        'compliant_count': compliant_count,
        'non_compliant_count': non_compliant_count,
        'inconsistent_count': inconsistent_count,
        'violation_rate': violation_rate,
        'inconsistent_rate': inconsistent_rate,
        'by_date': by_date,
        'by_instance': by_instance_map,
        'top_violation_reasons': top_violation_reasons
    }


def annotated_count():
    """已标注数量"""
    from models import Annotation
    return Annotation.query.filter_by(is_submitted=True).count()