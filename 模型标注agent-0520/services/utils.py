# -*- coding: utf-8 -*-
"""工具函数模块"""

from datetime import datetime, timedelta


def to_beijing_time(dt):
    """将 UTC datetime 转换为北京时间（UTC+8）

    参数：dt - datetime 对象（可以是 naive 或 aware）
    返回：格式化的北京时�"YYYY-MM-DD HH:MM:SS" 字符串
    """
    if not dt:
        return ''

    # 如果是 aware datetime（有时区信息），先转为 UTC 再加 8 小时
    if dt.tzinfo is not None:
        from datetime import timezone
        dt = dt.replace(tzinfo=None) - timezone.utc.utcoffset(dt)

    # UTC + 8 = 北京时间
    beijing = dt + timedelta(hours=8)
    return beijing.strftime('%Y-%m-%d %H:%M:%S')


def format_date_to_yyyymmdd(date_str):
    """将日期字符串转换为 YYYYMMDD 格式

    支持：YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD
    """
    if not date_str:
        return ''

    # 移�除分隔符
    date_str = date_str.replace('-', '').replace('/', '')

    # 已经是 8 位数字
    if len(date_str) == 8 and date_str.isdigit():
        return date_str

    return date_str


def format_date_from_yyyymmdd(date_str):
    """将 YYYYMMDD 转换为 YYYY-MM-DD 格式"""
    if not date_str or len(date_str) != 8:
        return date_str
    return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"