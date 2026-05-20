#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据拉取全链路审计脚本 v2 - 服务层直调版
直接调用服务层，绕过 Flask，所有 debug print 直接输出到终端
"""
import sys
import os
import json
sys.path.insert(0, '/Users/zcy/Desktop/模型标注agent')

os.environ['FLASK_APP'] = 'app.py'

from datetime import datetime, timedelta
import random

# 初始化 Flask app（用于 db 操作，不启动 HTTP 服务）
from app import app
from models import db, RawData, FetchLog, DailyStats
from services.fetch_service import fetch_data_from_idata, generate_daily_stats

# ========== 配置 ==========
TARGET_INSTANCE = 'ZJWC'
ENV = '云环境'
yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
yesterday_fmt = yesterday.replace('-', '')  # 20260518
SAMPLE_PERCENT = 100  # 全量拉取

print("=" * 60)
print("全链路审计 v2 — 服务层直调版")
print("=" * 60)
print(f"目标实例: {TARGET_INSTANCE}")
print(f"日期范围: {yesterday} (格式化: {yesterday_fmt})")
print(f"抽样比例: {SAMPLE_PERCENT}%")
print()

with app.app_context():
    db.create_all()

    # ========== 第一步：调用服务层 fetch_data_from_idata ==========
    print("=" * 60)
    print("第一步：fetch_data_from_idata（所有 DEBUG 日志直接输出）")
    print("=" * 60)
    fetch_result = fetch_data_from_idata(
        env=ENV,
        instance=TARGET_INSTANCE,
        start_date=yesterday_fmt,
        end_date=yesterday_fmt,
        sample_percent=SAMPLE_PERCENT
    )
    print()
    print(f"[审计] fetch_data_from_idata 返回值:")
    for k, v in fetch_result.items():
        print(f"  {k}: {v}")

    original_total = fetch_result.get('original_total', 0)
    if original_total == 0:
        print("\n⚠️  iData 线上总数为 0，可能是：")
        print("  1. 昨天确实无审核数据（正常）")
        print("  2. Cookie 过期")
        print("  3. pt 分区日期问题（日期分区格式）")
        print("\n→ 退出审计（无数据可写）")
        sys.exit(0)

    fetched_data = fetch_result.get('fetched_data', [])
    total_fetched = fetch_result.get('total_fetched', 0)

    # ========== 第二步：去重 + 写入 RawData ==========
    print()
    print("=" * 60)
    print("第二步：去重 + 写入 RawData")
    print("=" * 60)

    batch_id = f"audit_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    print(f"[DEBUG 审计] 批次ID: {batch_id}")

    # 去重：按 (instance_code + supplier_id + product_id + created_date) 去重
    seen_keys = set()
    unique_fetched_data = []
    skipped_count = 0

    for rec in fetched_data:
        supplier_id = str(rec.get('供应商id') or rec.get('supplier_id') or '')
        product_id = str(rec.get('商品id') or rec.get('product_id') or '')
        created_date = str(rec.get('创建日期') or rec.get('created_date') or '')
        key = f"{TARGET_INSTANCE}|{supplier_id}|{product_id}|{created_date}"

        if key in seen_keys:
            skipped_count += 1
            continue
        seen_keys.add(key)

        # 查重：与 DB 中已有记录比对
        existing = RawData.query.filter_by(
            instance_code=TARGET_INSTANCE,
            supplier_id=supplier_id,
            product_id=product_id,
            created_date=created_date
        ).first()
        if existing:
            skipped_count += 1
            continue

        unique_fetched_data.append(rec)

    print(f"[DEBUG 审计] {TARGET_INSTANCE} 去重统计: 拉取{total_fetched}条, 跳重{skipped_count}条, 唯一{len(unique_fetched_data)}条")

    # 写入 RawData
    inserted_count = 0
    for rec in unique_fetched_data:
        ai_result_raw = str(rec.get('AI审核结果') or rec.get('ai_result') or '')
        reject_reason = str(rec.get('拒绝原因') or rec.get('reject_reason') or '')

        # 处理编码问题：latin1 乱码修复
        if reject_reason:
            try:
                if '\\x' in repr(reject_reason):
                    reject_reason = reject_reason.encode('utf-8').decode('latin1')
            except Exception:
                pass

        raw_record = RawData(
            supplier_id=str(rec.get('供应商id') or ''),
            label=str(rec.get('标签') or ''),
            ai_audit_id=str(rec.get('AI审核id') or ''),
            audit_id=str(rec.get('审核id') or ''),
            product_id=str(rec.get('商品id') or ''),
            ai_result=ai_result_raw,
            audit_result=str(rec.get('审核单结果') or ''),
            human_reject_item=str(rec.get('人审拒绝项') or ''),
            reject_reason=reject_reason,
            human_comment=str(rec.get('人审意见') or ''),
            ai_reject_reason=str(rec.get('AI拒绝原因') or ''),
            ai_explain=str(rec.get('AI拒绝解释') or ''),
            shop_name=str(rec.get('店铺名称') or ''),
            product_name=str(rec.get('商品名称') or ''),
            category=str(rec.get('类目') or ''),
            main_image=str(rec.get('主图') or ''),
            detail_image=str(rec.get('详情图') or ''),
            sku_image=str(rec.get('sku图') or ''),
            spu_image=str(rec.get('spu图') or ''),
            product_link=str(rec.get('商品链接') or ''),
            check_result=str(rec.get('标注结果：1=正确  0=错误') or ''),
            annotation=str(rec.get('备注') or ''),
            instance_code=TARGET_INSTANCE,
            created_date=str(rec.get('创建日期') or ''),
            annotator=str(rec.get('标注人') or ''),
            random_num=float(rec.get('随机数') or 0),
            change_category=str(rec.get('变更类别') or ''),
            gmt_created=str(rec.get('创建时间') or ''),
            fetch_batch_id=batch_id,
            source='fetch'
        )
        db.session.add(raw_record)
        inserted_count += 1

    print(f"[DEBUG 审计] {TARGET_INSTANCE} RawData 写入前: 唯一数据{len(unique_fetched_data)}条")
    print(f"[DEBUG 审计] {TARGET_INSTANCE} RawData 实际插入: {inserted_count}条")

    # ========== 第三步：写入 FetchLog ==========
    fetch_log = FetchLog(
        batch_id=batch_id,
        env=ENV,
        instances=TARGET_INSTANCE,
        sample_percent=SAMPLE_PERCENT,
        total_fetched=total_fetched,
        original_total=original_total,
        original_compliant=fetch_result.get('original_compliant', 0),
        original_non_compliant=fetch_result.get('original_non_compliant', 0),
        compliant_count=fetch_result.get('compliant_count', 0),
        non_compliant_count=fetch_result.get('non_compliant_count', 0),
        skipped_duplicates=skipped_count,
        status='completed',
        data_start_date=yesterday_fmt,
        data_end_date=yesterday_fmt
    )
    db.session.add(fetch_log)
    print(f"[DEBUG 审计] FetchLog 写入前: batch={batch_id}, original_total={original_total}")

    # ========== 第四步：commit ==========
    print(f"[DEBUG 审计] {TARGET_INSTANCE} 准备 commit: session内RawData pending + FetchLog 更新")
    try:
        db.session.commit()
        print(f"[DEBUG 审计] {TARGET_INSTANCE} commit 成功!")
    except Exception as e:
        print(f"[ERROR] commit 失败: {e}")
        db.session.rollback()
        sys.exit(1)

    # ========== 第五步：generate_daily_stats ==========
    print()
    print("=" * 60)
    print("第五步：generate_daily_stats（DailyStats DEBUG 日志）")
    print("=" * 60)
    generate_daily_stats(
        instance=TARGET_INSTANCE,
        start_date=yesterday_fmt,
        end_date=yesterday_fmt,
        batch_id=batch_id,
        original_total=original_total,
        original_compliant=fetch_result.get('original_compliant', 0),
        original_non_compliant=fetch_result.get('original_non_compliant', 0),
        error_reasons=None,
        error_reasons_by_date=None
    )

    # ========== 第六步：数据库核对 ==========
    print()
    print("=" * 60)
    print("第六步：数据库核对")
    print("=" * 60)

    # RawData 实际记录数
    raw_count = RawData.query.filter_by(
        fetch_batch_id=batch_id,
        instance_code=TARGET_INSTANCE
    ).count()
    print(f"RawData 实际记录数 (本批次): {raw_count}")

    # RawData 分布
    print("\nRawData AI审核结果分布:")
    from sqlalchemy import func
    dist = db.session.query(
        RawData.ai_result,
        func.count(RawData.id)
    ).filter(
        RawData.fetch_batch_id == batch_id,
        RawData.instance_code == TARGET_INSTANCE
    ).group_by(RawData.ai_result).all()
    raw_compliant = raw_violation = 0
    for ai_res, cnt in dist:
        print(f"  {ai_res}: {cnt}条")
        if ai_res in ('合规', '1', 'PASS'):
            raw_compliant = cnt
        elif ai_res in ('违规', '0', 'REJECT'):
            raw_violation = cnt

    # DailyStats
    ds_rows = DailyStats.query.filter_by(
        batch_id=batch_id,
        instance_code=TARGET_INSTANCE
    ).all()
    print(f"\nDailyStats 记录数: {len(ds_rows)}")
    ds_total = 0
    for ds in ds_rows:
        reasons = {}
        try:
            reasons = json.loads(ds.error_reasons or '{}')
        except:
            pass
        print(f"  日期={ds.stat_date}, total={ds.total_count}, "
              f"compliant={ds.compliant_count}, non_compliant={ds.non_compliant_count}, "
              f"违规原因标签数={len(reasons)}")
        ds_total += ds.total_count

    # 累计（含历史批次）
    total_all = RawData.query.filter(
        RawData.instance_code == TARGET_INSTANCE,
        RawData.created_date == f"{yesterday[:4]}/{yesterday[5:7]}/{yesterday[8:10]}"
    ).count()
    print(f"\n该日期 ZJWC 累计 RawData（含所有批次）: {total_all}条")

    # ========== 审计结论 ==========
    print()
    print("=" * 60)
    print("审计结论")
    print("=" * 60)
    print(f"批次ID: {batch_id}")
    print(f"日期: {yesterday}")
    print(f"抽样比例: {SAMPLE_PERCENT}%")
    print()
    print(f"--- iData 层面 ---")
    print(f"iData COUNT 返回总数: {original_total}")
    print(f"  合规: {fetch_result.get('original_compliant', 0)}")
    print(f"  违规: {fetch_result.get('original_non_compliant', 0)}")
    print()
    print(f"--- 翻页拉取 ---")
    print(f"  total_fetched: {total_fetched}")
    print()
    print(f"--- 去重写入 ---")
    print(f"本次拉取原始: {total_fetched}条")
    print(f"  跳重: {skipped_count}条")
    print(f"  唯一新增: {len(unique_fetched_data)}条")
    print(f"  RawData 实际写入: {inserted_count}条")
    print(f"  ✅ 写入一致? {'是' if inserted_count == len(unique_fetched_data) else '否'}")
    print()
    print(f"--- 累计数据对比 ---")
    print(f"该日期累计 RawData: {total_all}条")
    print(f"vs iData线上总数: {original_total}条")
    diff = original_total - total_all
    diff_pct = round(diff / original_total * 100, 1) if original_total else 0
    print(f"差异: {diff}条 ({diff_pct}%)")
    if diff == 0:
        print("✅ 累计RawData = iData线上总数，数据完整！")
    elif total_all > original_total:
        print("⚠️ 累计RawData > iData线上总数，可能跨批次重复写入！")
    else:
        print(f"⚠️ 累计RawData < iData线上总数，缺失 {diff} 条 ({diff_pct}%)")
    print()
    print(f"--- DailyStats ---")
    print(f"聚合 total: {ds_total}")
    print(f"vs iData线上总数: {original_total}条")
    if ds_total == original_total:
        print("✅ DailyStats total = iData线上总数，一致！")
    else:
        print(f"⚠️ DailyStats total ≠ iData线上总数，差值: {original_total - ds_total}")
