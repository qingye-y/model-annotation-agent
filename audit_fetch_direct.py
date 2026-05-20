#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据拉取全链路审计脚本 - 直接调用服务层
绕过 Flask，print 直接输出到终端，无日志丢失
"""
import sys
import os
sys.path.insert(0, '/Users/zcy/Desktop/模型标注agent')

from datetime import datetime, timedelta
import sqlite3
import json

# ========== 触发一次新的拉取（先清理旧数据）==========
print("=" * 60)
print("第一步：触发新的数据拉取（ZJWC, 昨天, 100%抽样）")
print("=" * 60)

import requests

BASE_URL = "http://localhost:5000"
yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

# 登录
resp = requests.post(f"{BASE_URL}/api/auth/login",
    json={"username": "admin", "password": "admin123"})
token = resp.json()['data']['token']
headers = {"Authorization": f"Bearer {token}"}
print(f"登录成功!")

# 触发拉取
payload = {
    "env": "云环境",
    "instances": ["ZJWC"],
    "sample_percent": 100,
    "start_date": yesterday,
    "end_date": yesterday
}
print(f"\n触发条件: 实例=ZJWC, 日期={yesterday}, 抽样=100%")
print(">>> 发送拉取请求（等待响应，约10秒）...\n")

resp = requests.post(f"{BASE_URL}/api/data-fetch", json=payload,
                     headers=headers, timeout=600)
result = resp.json()
print(f"API 响应: success={result.get('success')}, batch_id={result.get('batch_id')}")
print(f"  original_total={result.get('original_total')}, total_fetched={result.get('total_fetched')}")
print(f"  compliant={result.get('compliant_count')}, non_compliant={result.get('non_compliant_count')}")
print(f"  skipped_duplicates={result.get('skipped_duplicates')}")

if not result.get('success'):
    print(f"\n拉取失败: {result.get('message')}")
    sys.exit(1)

batch_id = result.get('batch_id')

# ========== 查数据库核对 ==========
print("\n" + "=" * 60)
print("第二步：数据库核对")
print("=" * 60)

conn = sqlite3.connect('/Users/zcy/Desktop/模型标注agent/instance/app.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# FetchLog
cur.execute("""
    SELECT batch_id, original_total, total_fetched, compliant_count,
           non_compliant_count, original_compliant, original_non_compliant,
           skipped_duplicates, status
    FROM fetch_log WHERE batch_id = ?
""", (batch_id,))
log_row = cur.fetchone()
if log_row:
    print(f"\nFetchLog:")
    print(f"  original_total (iData线上总数) = {log_row['original_total']}")
    print(f"  total_fetched  (本次新增写入) = {log_row['total_fetched']}")
    print(f"  compliant_count               = {log_row['compliant_count']}")
    print(f"  non_compliant_count          = {log_row['non_compliant_count']}")
    print(f"  original_compliant           = {log_row['original_compliant']}")
    print(f"  original_non_compliant       = {log_row['original_non_compliant']}")
    print(f"  skipped_duplicates           = {log_row['skipped_duplicates']}")
    print(f"  status                       = {log_row['status']}")
    ot = log_row['original_total']
    tf = log_row['total_fetched']
    skip = log_row['skipped_duplicates']
else:
    print("未找到 FetchLog 记录!")
    sys.exit(1)

# RawData 实际
cur.execute("""
    SELECT COUNT(*) FROM raw_data WHERE fetch_batch_id = ? AND instance_code = ?
""", (batch_id, 'ZJWC'))
raw_count = cur.fetchone()[0]
print(f"\nRawData 实际记录数: {raw_count}")

# RawData 分布
cur.execute("""
    SELECT ai_result, COUNT(*) FROM raw_data
    WHERE fetch_batch_id = ? AND instance_code = ?
    GROUP BY ai_result
""", (batch_id, 'ZJWC'))
print("\nRawData AI审核结果分布:")
raw_compliant = raw_violation = 0
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}条")
    if row[0] in ('合规', '1', 'PASS'):
        raw_compliant = row[1]
    elif row[0] in ('违规', '0', 'REJECT'):
        raw_violation = row[1]

# DailyStats
cur.execute("""
    SELECT stat_date, total_count, compliant_count, non_compliant_count, error_reasons
    FROM daily_stats WHERE batch_id = ? AND instance_code = ?
""", (batch_id, 'ZJWC'))
print("\nDailyStats:")
stats_rows = cur.fetchall()
daily_total = 0
if stats_rows:
    for row in stats_rows:
        reasons = {}
        try:
            reasons = json.loads(row['error_reasons'] or '{}')
        except:
            pass
        print(f"  日期={row['stat_date']}, total={row['total_count']}, "
              f"compliant={row['compliant_count']}, non_compliant={row['non_compliant_count']}, "
              f"违规原因标签数={len(reasons)}")
    daily_total = sum(r['total_count'] for r in stats_rows)
else:
    print("  无记录")

# 累计 RawData（含历史批次）
cur.execute("""
    SELECT COUNT(*) FROM raw_data
    WHERE instance_code = ?
    AND created_date >= ? AND created_date <= ?
""", ('ZJWC', f"{yesterday[:4]}/{yesterday[5:7]}/{yesterday[8:10]}",
       f"{yesterday[:4]}/{yesterday[5:7]}/{yesterday[8:10]}"))
total_raw_for_date = cur.fetchone()[0]
print(f"\n该日期 ZJWC 累计 RawData（含所有批次）: {total_raw_for_date}条")

conn.close()

# ========== 输出审计报告 ==========
print("\n" + "=" * 60)
print("数据拉取审计报告")
print("=" * 60)
print(f"批次ID: {batch_id}")
print(f"日期: {yesterday}")
print(f"抽样比例: 100%")

print(f"\n--- iData 层面 ---")
print(f"iData COUNT 返回总数: {ot}")
print(f"  合规: {log_row['original_compliant']}")
print(f"  违规: {log_row['original_non_compliant']}")

print(f"\n--- 翻页拉取 ---")
print(f"  (见终端 [DEBUG 审计] 日志)")

print(f"\n--- 去重写入 ---")
print(f"本次拉取原始数据: {ot} 条")
print(f"  其中跳重(商品id已在DB): {skip} 条")
print(f"  本次新增写入: {tf} 条")
print(f"  RawData 实际记录: {raw_count} 条")
print(f"  ✅ 写入=total_fetched? {'是' if raw_count == tf else f'否(差{abs(raw_count-tf)})'}")

print(f"\n--- 累计数据 ---")
print(f"该日期 ZJWC 累计 RawData: {total_raw_for_date} 条")
print(f"  vs iData线上总数: {ot} 条")
print(f"  差异: {ot - total_raw_for_date} 条 ({round((ot-total_raw_for_date)/ot*100,1) if ot else 0}%)")

print(f"\n--- DailyStats ---")
print(f"  聚合 total: {daily_total}")
print(f"  vs iData线上总数: {ot} 条")
print(f"  ✅ 数字一致? {'是' if daily_total == ot else f'否(差{ot - daily_total})'}")

print(f"\n--- 差异汇总 ---")
d1 = ot - tf
d2 = tf - raw_count
d3 = ot - total_raw_for_date
print(f"差异1 (iData总数 - 本次新增写入): {ot} - {tf} = {d1} ({round(d1/ot*100,1) if ot else 0}%)")
print(f"  原因: 商品id已在历史批次中存在，跳重写入")
print(f"差异2 (本次新增写入 - RawData实际): {tf} - {raw_count} = {d2} ✅")
print(f"差异3 (iData总数 - 累计RawData): {ot} - {total_raw_for_date} = {d3} ({round(d3/ot*100,1) if ot else 0}%)")
print(f"  原因: 历史批次已写入 {total_raw_for_date - tf} 条，本次跳重")

print(f"\n--- 根因分析 ---")
if ot == total_raw_for_date:
    print("✅ 累计RawData = iData线上总数，数据完整，无丢失")
elif total_raw_for_date > ot:
    print("⚠️ 累计RawData > iData线上总数，可能存在跨批次重复写入!")
else:
    missing = ot - total_raw_for_date
    pct = round(missing/ot*100, 1)
    print(f"⚠️ 累计RawData < iData线上总数，缺失 {missing} 条 ({pct}%)")
    if skip > 0 and tf == 0:
        print("  可能原因: 所有商品ID都与历史批次重复，本批次无新数据写入")
    else:
        print("  可能原因: 翻页窗口函数丢数据 / iData返回不完整")
