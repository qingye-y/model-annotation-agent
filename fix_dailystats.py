#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DailyStats 数据修复脚本 v2
1. 清理膨胀批次的 DailyStats 记录
2. 用 GROUP BY 日期一次查询，按天写入正确的 DailyStats
3. 验证新数据对齐 iData 基准值
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app
from models import db, DailyStats
from services.fetch_service import build_sql, execute_sql_query, fetch_error_reasons_online
from datetime import datetime

INSTANCES = ['ZJWC', 'HWCS', 'HNLCWC']
ENV = '云环境'
START_DATE = '20260501'
END_DATE = '20260518'

print("=" * 60)
print("DailyStats 数据修复")
print(f"实例: {INSTANCES}  |  日期: {START_DATE} ~ {END_DATE}")
print("=" * 60)

with app.app_context():
    # ========== 第一步：清理旧 DailyStats ==========
    print("\n第一步：清理旧 DailyStats...")
    rc = DailyStats.query.filter(
        DailyStats.instance_code.in_(INSTANCES),
        DailyStats.stat_date >= START_DATE,
        DailyStats.stat_date <= END_DATE
    ).delete(synchronize_session='fetch')
    db.session.commit()
    print(f"  已删除 {rc} 条旧记录")

    # ========== 第二步：逐实例查询 DAY×COUNT 分组数据 ==========
    batch_id = f"FIX-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    print(f"\n第二步：查询 iData COUNT(DISTINCT) 按日期分组...\n批次: {batch_id}")

    all_baselines = {}  # {instance: {date_str: {total, compliant, non_compliant}}}

    for instance in INSTANCES:
        print(f"\n{'─' * 50}")
        print(f"实例: {instance}")
        
        detail_sql = build_sql(instance, START_DATE, END_DATE)
        
        # 一次性查询：按(创建日期, AI审核结果)分组
        count_by_date_sql = f"""
            SELECT `创建日期`, `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
            FROM ({detail_sql}) t
            GROUP BY `创建日期`, `AI审核结果`
            ORDER BY `创建日期`
        """
        
        try:
            result = execute_sql_query(count_by_date_sql, instance, ENV)
        except Exception as e:
            print(f"  ❌ 查询失败: {e}")
            continue
        
        if not isinstance(result, list) or not result:
            print(f"  ⚠️ 无数据返回")
            continue
        
        # 解析：按日期聚合
        daily_totals = {}
        for row in result:
            if not isinstance(row, dict):
                continue
            raw_date = str(row.get('创建日期', '') or '').strip()
            status = str(row.get('AI审核结果', '') or '').strip()
            cnt = int(row.get('cnt', 0) or 0)
            
            # 标准化日期: '2026/05/18' -> '20260518'
            date_str = raw_date.replace('/', '').replace('-', '')
            if len(date_str) != 8:
                continue
            
            daily_totals.setdefault(date_str, {'total': 0, 'compliant': 0, 'non_compliant': 0})
            daily_totals[date_str]['total'] += cnt
            if status in ('合规', '1', 'PASS'):
                daily_totals[date_str]['compliant'] = cnt
            elif status in ('违规', '0', 'REJECT'):
                daily_totals[date_str]['non_compliant'] = cnt
        
        all_baselines[instance] = daily_totals
        
        # 打印每日数据
        instance_total = 0
        for date_str in sorted(daily_totals.keys()):
            d = daily_totals[date_str]
            instance_total += d['total']
            print(f"  {date_str}: total={d['total']:>7}, compliant={d['compliant']:>6}, non_compliant={d['non_compliant']:>5}")
        print(f"  {'─' * 35}")
        print(f"  {instance} 汇总: {instance_total}")

    # ========== 第三步：写入 DailyStats ==========
    print(f"\n{'=' * 60}")
    print("第三步：写入 DailyStats...")
    
    write_count = 0
    for instance in INSTANCES:
        daily_totals = all_baselines.get(instance, {})
        for date_str, stats in daily_totals.items():
            if stats['total'] > 0:
                ds = DailyStats(
                    stat_date=date_str,
                    instance_code=instance,
                    batch_id=batch_id,
                    total_count=stats['total'],
                    compliant_count=stats['compliant'],
                    non_compliant_count=stats['non_compliant'],
                    error_reasons='{}'
                )
                db.session.add(ds)
                write_count += 1
    
    db.session.commit()
    print(f"  写入 {write_count} 条记录")

    # ========== 第四步：重新获取违规原因分布 ==========
    print(f"\n{'=' * 60}")
    print("第四步：重新获取违规原因分布...")
    
    for instance in INSTANCES:
        try:
            reasons_data = fetch_error_reasons_online(ENV, instance, START_DATE, END_DATE)
            if reasons_data and reasons_data.get('by_date'):
                by_date = reasons_data['by_date']
                updated = 0
                for date_str, reasons in by_date.items():
                    ds_record = DailyStats.query.filter_by(
                        batch_id=batch_id, instance_code=instance, stat_date=date_str
                    ).first()
                    if ds_record and reasons:
                        ds_record.error_reasons = json.dumps(reasons, ensure_ascii=False)
                        updated += 1
                db.session.commit()
                print(f"  {instance}: 更新 {updated} 条违规原因")
            else:
                print(f"  {instance}: 无违规原因")
        except Exception as e:
            print(f"  {instance}: 获取失败 - {e}")
            db.session.rollback()

    # ========== 第五步：验证 ==========
    print(f"\n{'=' * 60}")
    print("第五步：验证（DailyStats vs iData 基准）")
    print(f"{'=' * 60}")
    
    ok = 0; fail = 0
    for instance in INSTANCES:
        expected = all_baselines.get(instance, {})
        for date_str in sorted(expected.keys()):
            ds = DailyStats.query.filter_by(
                batch_id=batch_id, instance_code=instance, stat_date=date_str
            ).first()
            actual = ds.total_count if ds else 0
            exp = expected[date_str]['total']
            if actual == exp:
                ok += 1
            else:
                fail += 1
                print(f"  ❌ {instance} {date_str}: DailyStats={actual} ≠ iData={exp}")

    print(f"\n验证: ✅ {ok} 天正确, ❌ {fail} 天不匹配")
    if fail == 0:
        print("🎉 DailyStats 数据已完全修复，去刷新看板即可！")
    else:
        print("⚠️ 仍有不匹配，需人工排查")
    print(f"批次: {batch_id}")

