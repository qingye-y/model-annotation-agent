#!/usr/bin/env python3
"""
数据拉取全链路审计脚本
触发一次 ZJWC 数据拉取，打印全链路 [DEBUG 审计] 日志，
最后查询数据库对比数字，输出完整审计报告。
"""
import requests
import json
import time
from datetime import datetime, timedelta

BASE_URL = "http://localhost:5000"

def wait_for_batch_complete(batch_id, timeout=300, headers=None):
    """轮询等待批次完成"""
    start = time.time()
    while time.time() - start < timeout:
        resp = requests.get(f"{BASE_URL}/api/data-fetch/status/{batch_id}", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('status') in ('completed', 'failed'):
                return data
        time.sleep(3)
    return None

def main():
    print("=" * 60)
    print("数据拉取全链路审计")
    print("=" * 60)

    # 1. 登录获取 session
    login_resp = requests.post(f"{BASE_URL}/api/auth/login",
        json={"username": "admin", "password": "admin123"})
    if not login_resp.ok or not login_resp.json().get('data', {}).get('token'):
        print(f"登录失败: {login_resp.text}")
        return
    token = login_resp.json()['data']['token']
    headers = {"Authorization": f"Bearer {token}"}
    print("登录成功!")

    # 触发拉取：选 ZJWC，昨天日期，100% 抽样
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    print(f"\n触发条件: 实例=ZJWC, 日期={yesterday}, 抽样=100%")

    payload = {
        "env": "云环境",
        "instances": ["ZJWC"],
        "sample_percent": 100,
        "start_date": yesterday,
        "end_date": yesterday
    }

    print(f"\n请求体: {json.dumps(payload, ensure_ascii=False)}")
    print("\n>>> 发送拉取请求...\n")

    resp = requests.post(f"{BASE_URL}/api/data-fetch", json=payload, headers=headers, timeout=600)
    result = resp.json()
    print(f"API 响应: {json.dumps(result, ensure_ascii=False)}")

    if not result.get('success'):
        print(f"\n拉取失败: {result.get('message')}")
        return

    batch_id = result.get('batch_id')
    print(f"\n批次ID: {batch_id}")
    print("拉取已同步完成（同步模式，等待约10秒...）")
    # 同步API：响应返回即表示处理完成
    print(f"批次状态: completed (同步模式)")

    # 拉取完成后，查询数据库对比
    print("\n" + "=" * 60)
    print("数据库核对")
    print("=" * 60)

    import sqlite3
    conn = sqlite3.connect('/Users/zcy/Desktop/模型标注agent/instance/app.db')
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
        print(f"  original_total (iData线上总数) = {log_row[1]}")
        print(f"  total_fetched  (实际写入)     = {log_row[2]}")
        print(f"  compliant_count               = {log_row[3]}")
        print(f"  non_compliant_count          = {log_row[4]}")
        print(f"  original_compliant           = {log_row[5]}")
        print(f"  original_non_compliant       = {log_row[6]}")
        print(f"  skipped_duplicates           = {log_row[7]}")
        print(f"  status                       = {log_row[8]}")
        original_total = log_row[1]
        total_fetched = log_row[2]
    else:
        print("未找到 FetchLog 记录")
        original_total = 0
        total_fetched = 0

    # RawData 实际条数
    cur.execute("""
        SELECT COUNT(*) FROM raw_data WHERE fetch_batch_id = ? AND instance_code = ?
    """, (batch_id, 'ZJWC'))
    raw_count = cur.fetchone()[0]
    print(f"\nRawData 实际记录数: {raw_count}")

    # RawData 按 AI审核结果 统计
    cur.execute("""
        SELECT ai_result, COUNT(*) FROM raw_data
        WHERE fetch_batch_id = ? AND instance_code = ?
        GROUP BY ai_result
    """, (batch_id, 'ZJWC'))
    print("\nRawData AI审核结果分布:")
    for row in cur.fetchall():
        print(f"  {row[0]}: {row[1]}条")

    # DailyStats
    cur.execute("""
        SELECT stat_date, total_count, compliant_count, non_compliant_count, error_reasons
        FROM daily_stats WHERE batch_id = ? AND instance_code = ?
    """, (batch_id, 'ZJWC'))
    print("\nDailyStats:")
    stats_rows = cur.fetchall()
    if stats_rows:
        for row in stats_rows:
            reasons = json.loads(row[4] or '{}') if row[4] else {}
            print(f"  日期={row[0]}, total={row[1]}, compliant={row[2]}, non_compliant={row[3]}, 违规原因标签数={len(reasons)}")
        daily_total = sum(r[1] for r in stats_rows)
        print(f"\nDailyStats 聚合 total: {daily_total}")
    else:
        print("  无记录")
        daily_total = 0

    conn.close()

    # 差异汇总
    print("\n" + "=" * 60)
    print("数据拉取审计报告")
    print("=" * 60)
    print(f"SQL 是否正确: 请检查终端 [DEBUG 审计] 日志中的 SQL")
    print(f"iData COUNT 返回: original_total={original_total}, compliant={log_row[5] if log_row else 'N/A'}, non_compliant={log_row[6] if log_row else 'N/A'}")
    print(f"翻页拉取明细条数: (见终端日志)")
    print(f"实际写入 RawData: {raw_count}")
    print(f"FetchLog.total_fetched: {total_fetched}")
    print(f"DailyStats 聚合 total: {daily_total}")

    if log_row:
        diff1 = original_total - raw_count
        diff2 = total_fetched - raw_count
        print(f"\n--- 差异分析 ---")
        print(f"差异1: iData线上总数 - RawData实际写入 = {original_total} - {raw_count} = {diff1} ({round(diff1/original_total*100,1) if original_total else 0}%)")
        print(f"差异2: FetchLog.total_fetched - RawData实际 = {total_fetched} - {raw_count} = {diff2}")
        if diff1 != 0 or diff2 != 0:
            print(f"⚠️  存在差异！需进一步排查差异原因")
        else:
            print(f"✅ 数字一致，无差异")

if __name__ == '__main__':
    main()
