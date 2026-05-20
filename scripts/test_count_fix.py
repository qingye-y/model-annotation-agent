#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比 COUNT(DISTINCT app_id) vs COUNT(DISTINCT goods_id)"""
import sys
sys.path.insert(0, '/Users/zcy/Desktop/模型标注agent')

from app import app
from services.fetch_service import build_sql, execute_sql_query

TARGET_INSTANCE = 'ZJWC'
ENV = '云环境'
START_DATE = '20260518'
END_DATE = '20260518'

with app.app_context():
    detail_sql = build_sql(TARGET_INSTANCE, START_DATE, END_DATE)
    print("detail_sql ready, len=%d" % len(detail_sql))
    print()

    # 方法1: DISTINCT app_id + GROUP BY AI审核结果
    sql1 = (
        "SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt "
        "FROM (%s) t GROUP BY `AI审核结果`"
    ) % detail_sql

    # 方法2: DISTINCT goods_id + GROUP BY AI审核结果
    sql2 = (
        "SELECT `AI审核结果`, COUNT(DISTINCT `商品id`) as cnt "
        "FROM (%s) t GROUP BY `AI审核结果`"
    ) % detail_sql

    # 方法3: 总数 DISTINCT app_id
    sql3 = (
        "SELECT COUNT(DISTINCT `审核id`) as cnt "
        "FROM (%s) t"
    ) % detail_sql

    # 方法4: 总数 DISTINCT goods_id
    sql4 = (
        "SELECT COUNT(DISTINCT `商品id`) as cnt "
        "FROM (%s) t"
    ) % detail_sql

    for label, sql in [
        ("[A] DISTINCT app_id + GROUP BY", sql1),
        ("[B] DISTINCT goods_id + GROUP BY", sql2),
        ("[C] COUNT(DISTINCT app_id) 总数", sql3),
        ("[D] COUNT(DISTINCT goods_id) 总数", sql4),
    ]:
        print("%s..." % label)
        try:
            result = execute_sql_query(sql, TARGET_INSTANCE, ENV)
            if isinstance(result, list):
                total = 0
                compliant = violation = 0
                for r in result:
                    if isinstance(r, dict):
                        cnt = int(r.get('cnt', 0) or 0)
                        status = r.get('AI审核结果', 'total')
                        total += cnt
                        print("  %s: %d" % (status, cnt))
                        if status in ('合规', '1', 'PASS'):
                            compliant = cnt
                        elif status in ('违规', '0', 'REJECT'):
                            violation = cnt
                print("  TOTAL: %d (compliant=%d, violation=%d)" % (total, compliant, violation))
            else:
                print("  result=%s" % result)
        except Exception as e:
            print("  ERROR: %s" % e)
        print()

    print("=" * 60)
    print("iData direct query baseline: 5588")
    print("Conclusion: whichever method gives total closest to 5588 is the right dimension")
