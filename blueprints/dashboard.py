# -*- coding: utf-8 -*-
from flask import Blueprint, jsonify, request
from models import db, RawData, FetchLog, User, Annotation, SqlTemplate, SqlConfig, DailyStats
from datetime import datetime, timedelta
from sqlalchemy import func, text, case, or_
import requests
import json

# 服务层导入
from services.stats_service import (
    get_idata_cookie, get_env_config, get_default_sql_template,
    get_env_by_instance, query_idata, get_daily_stats_sql,
    extract_violation_type, get_reason_distribution_sql,
    annotated_count
)

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/api')


@dashboard_bp.route('/stats', methods=['GET'])
def api_stats():
    """数据统计接口"""
    total = RawData.query.count()

    if total > 0:
        compliant = RawData.query.filter_by(ai_result='合规').count()
        non_compliant = total - compliant
        return jsonify({
            "total_count": total,
            "compliant_count": compliant,
            "non_compliant_count": non_compliant,
            "compliance_rate": round(compliant / total * 100, 2) if total > 0 else 0,
            "non_compliance_rate": round(non_compliant / total * 100, 2) if total > 0 else 0,
            "accuracy_rate": 95.82,
            "inconsistency_rate": 8.67,
            "pending_tasks": total - annotated_count()
        })

    return jsonify({
        "total_count": 0,
        "compliant_count": 0,
        "non_compliant_count": 0,
        "compliance_rate": 0,
        "non_compliance_rate": 0,
        "accuracy_rate": 0,
        "inconsistency_rate": 0,
        "pending_tasks": 0
    })


@dashboard_bp.route('/dashboard/stats', methods=['GET'])
def api_dashboard_stats():
    """看板统计接口 - 从 DailyStats 快照表读取线上原始全量统计"""
    from config import ENV_CONFIG

    # 获取查询参数
    instances_param = request.args.get('instances', '')
    start_date = request.args.get('start_date', '')  # YYYYMMDD
    end_date = request.args.get('end_date', '')      # YYYYMMDD

    # 默认日期范围：最近7天
    if not start_date or not end_date:
        today = datetime.now()
        end_date = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=6)).strftime('%Y%m%d')

    print(f"[Dashboard] 查询参数: start_date={start_date}, end_date={end_date}, instances={instances_param}")

    # ====== 先检查 DailyStats 表是否有数据 ======
    stats_count = DailyStats.query.count()
    if stats_count == 0:
        print("[Dashboard] DailyStats 表为空，返回空统计")
        return jsonify({
            "total_count": 0,
            "compliant_count": 0,
            "non_compliant_count": 0,
            "violation_rate": 0,
            "accuracy_rate": None,
            "disagree_rate": None,
            "by_date": [],
            "by_instance": {},
            "empty": True,
            "message": "暂无数据，请先获取线上数据"
        })

    # 解析实例列表
    if instances_param:
        instance_list = [inst.strip() for inst in instances_param.split(',') if inst.strip()]
    else:
        instance_list = []
        for env_config in ENV_CONFIG.values():
            instance_list.extend(env_config.get('instances', []))
        instance_list = list(set(instance_list))

    print(f"[Dashboard] 实例列表: {instance_list}")

    # ====== 从 DailyStats 表读取统计快照 ======
    query = DailyStats.query.filter(
        DailyStats.stat_date >= start_date,
        DailyStats.stat_date <= end_date
    )
    if instance_list:
        query = query.filter(DailyStats.instance_code.in_(instance_list))

    daily_records = query.all()
    record_count = len(daily_records)

    print(f"[Dashboard] DailyStats 记录数: {record_count}")

    # ====== DEBUG: 打印 by_date_map keys ======
    _tmp_map = {}
    for record in daily_records:
        ds = record.stat_date
        if ds not in _tmp_map:
            _tmp_map[ds] = 0
        _tmp_map[ds] += 1
    print(f"[DEBUG] by_date_map keys: {sorted(_tmp_map.keys())}")

    # 初始化统计数据
    total_count = 0
    compliant_count = 0
    non_compliant_count = 0
    by_date_map = {}      # {date: {total, compliant, non_compliant}}
    by_instance_map = {}  # {instance: {total, compliant, non_compliant}}
    violation_reason_map = {}  # {tag: count} 用于合并违规原因

    # 聚合 DailyStats 数据
    inconsistent_count = 0
    for record in daily_records:
        rec_total = record.total_count or 0
        rec_compliant = record.compliant_count or 0
        rec_non_compliant = record.non_compliant_count or 0
        rec_inconsistent = record.inconsistent_count or 0

        total_count += rec_total
        compliant_count += rec_compliant
        non_compliant_count += rec_non_compliant
        inconsistent_count += rec_inconsistent

        # 按日期聚合
        date_str = record.stat_date
        if date_str not in by_date_map:
            by_date_map[date_str] = {'total': 0, 'compliant': 0, 'non_compliant': 0, 'inconsistent': 0}
        by_date_map[date_str]['total'] += rec_total
        by_date_map[date_str]['compliant'] += rec_compliant
        by_date_map[date_str]['non_compliant'] += rec_non_compliant
        by_date_map[date_str]['inconsistent'] += rec_inconsistent

        # 按实例聚合
        inst = record.instance_code
        if inst not in by_instance_map:
            by_instance_map[inst] = {'total': 0, 'compliant': 0, 'non_compliant': 0, 'inconsistent': 0}
        by_instance_map[inst]['total'] += rec_total
        by_instance_map[inst]['compliant'] += rec_compliant
        by_instance_map[inst]['non_compliant'] += rec_non_compliant
        by_instance_map[inst]['inconsistent'] += rec_inconsistent

        # 合并违规原因统计
        if record.error_reasons:
            try:
                reasons = json.loads(record.error_reasons)
                if isinstance(reasons, dict):
                    for tag, count in reasons.items():
                        violation_reason_map[tag] = violation_reason_map.get(tag, 0) + count
            except (json.JSONDecodeError, TypeError):
                pass

    # 计算总体违规率和不一致率
    violation_rate = round(non_compliant_count / total_count * 100, 2) if total_count > 0 else 0

    # 机审不一致率：改用 FetchLog 口径（已完成互检批次，A/B模型判定不一致比例）
    # 筛选业务日期与查询范围有重叠的批次
    fetch_query = FetchLog.query.filter(FetchLog.review_status == 'completed')
    if start_date and end_date:
        fetch_query = fetch_query.filter(
            FetchLog.data_start_date <= end_date,
            FetchLog.data_end_date >= start_date
        )
    if instance_list:
        inst_filters = [FetchLog.instances.like('%' + inst + '%') for inst in instance_list]
        fetch_query = fetch_query.filter(or_(*inst_filters))
    fetch_batches = fetch_query.all()
    fl_total = 0
    fl_inconsistent = 0
    for fb in fetch_batches:
        fl_total += fb.total_fetched or 0
        fl_inconsistent += fb.inconsistent_count or 0
    inconsistent_rate = round(fl_inconsistent / fl_total * 100, 2) if fl_total > 0 else 0

    # ====== 构建日期范围内的完整 by_date 数组（补零）======
    date_range = []
    try:
        start_dt = datetime.strptime(start_date, '%Y%m%d')
        end_dt = datetime.strptime(end_date, '%Y%m%d')
        current = start_dt
        while current <= end_dt:
            date_range.append(current.strftime('%Y%m%d'))
            current += timedelta(days=1)
        print(f"[DEBUG] date_range 构建完成: {date_range}")
    except Exception as e:
        print(f"[ERROR] 日期解析失败: {e}")
        date_range = []

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

    # 计算违规原因分组（Top 10 + 其他模式）
    # 规则：按频次降序，取前10个类型独立展示，其余归入"其他"
    top_violation_reasons = []
    all_reasons_detail = []   # 全量明细，用于"其他"展开
    if violation_reason_map:
        total_violations = sum(violation_reason_map.values())
        sorted_reasons = sorted(violation_reason_map.items(), key=lambda x: x[1], reverse=True)
        # 全量明细（按次数降序）
        for tag, count in sorted_reasons:
            percentage = round(count / total_violations * 100, 1) if total_violations > 0 else 0
            all_reasons_detail.append({'name': tag, 'value': count, 'percentage': percentage})
        # Top 10 + 其他：取前10个，其余归入"其他"
        top10 = []
        other_total = 0
        for i, (tag, count) in enumerate(sorted_reasons):
            percentage = round(count / total_violations * 100, 1) if total_violations > 0 else 0
            if i < 10:
                top10.append({'name': tag, 'value': count, 'percentage': percentage})
            else:
                other_total += count
        # 如果有"其他"，加入分组末尾
        other_pct = 0
        if other_total > 0:
            other_pct = round(other_total / total_violations * 100, 1) if total_violations > 0 else 0
            top10.append({'name': '其他', 'value': other_total, 'percentage': other_pct})
        top_violation_reasons = top10
        print(f"[看板统计] 违规原因Top10分组: Top10类型={len([x for x in top_violation_reasons if x['name']!='其他'])}, "
              f"其他={other_total}次({other_pct}%), 全量={len(all_reasons_detail)}种")

    # ====== 大模型审核准确率统计（基于 RawData 互检结果）======
    # 口径：modelb_reviewed=True 且 modelb_consistent != None（已互检有结论）
    # ignore 记录不参与分母，不影响准确率
    try:
        acc_query = RawData.query.filter(
            RawData.modelb_reviewed == True,
            RawData.modelb_consistent != None
        )
        # 日期筛选：gmt_created 格式为 YYYY-MM-DD HH:MM:SS，需将 YYYYMMDD 转为 YYYY-MM-DD 格式
        if start_date and end_date:
            # YYYYMMDD -> YYYY-MM-DD
            start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
            end_fmt   = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
            acc_query = acc_query.filter(
                RawData.gmt_created >= start_fmt,
                RawData.gmt_created <= end_fmt + ' 23:59:59'
            )
        if instance_list:
            acc_query = acc_query.filter(RawData.instance_code.in_(instance_list))

        acc_records = acc_query.all()
        acc_correct = 0
        acc_total = 0  # 有效分母（排除 ignore）
        acc_ignored = 0
        acc_skipped = 0

        for rec in acc_records:
            if rec.check_result == 'ignore':
                acc_ignored += 1
                continue
            acc_total += 1
            is_correct = _is_modela_correct(rec)
            if is_correct is True:
                acc_correct += 1

        accuracy_rate = round(acc_correct / acc_total * 100, 1) if acc_total > 0 else None
        accuracy_empty = acc_total == 0
        print(f"[看板统计] 互检准确率：correct={acc_correct}, total={acc_total}, "
              f"ignored={acc_ignored}, accuracy={accuracy_rate}%")
    except Exception as e:
        print(f"[ERROR] 准确率计算失败: {e}")
        accuracy_rate = None
        acc_correct = 0
        acc_total = 0
        accuracy_empty = True

    # 打印诊断日志
    print(f"[看板统计] 筛选条件：日期={start_date}~{end_date}, 实例={instance_list}")
    print(f"[看板统计] DailyStats 汇总：审核总数={total_count}, 合规={compliant_count}, 违规={non_compliant_count}, 记录数={record_count}")
    print(f"[看板统计] FetchLog 口径：审核总数={fl_total}, 不一致={fl_inconsistent}, 不一致率={inconsistent_rate}%")
    print(f"[看板统计] 违规原因标签数={len(violation_reason_map)}, top10={len(top_violation_reasons)}")

    # ====== 标注员今日效能统计（annotator_stats）======
    # 查询当天所有标注员的有效标注数据，按人聚合，计算标注量和准确率
    annotator_stats = []
    try:
        # 北京时区今日日期
        bj_today = (datetime.utcnow() + timedelta(hours=8)).date()
        today_start_utc = datetime.combine(bj_today, datetime.min.time()) - timedelta(hours=8)
        today_end_utc = datetime.combine(bj_today, datetime.max.time()) - timedelta(hours=8)

        # 查询所有活跃标注员
        active_annotators = User.query.filter(
            User.role == 'annotator',
            User.is_active == True
        ).all()

        for annotator in active_annotators:
            # 查询该标注员今日的有效标注记录（已提交）
            ann_records = db.session.query(Annotation).join(
                RawData, Annotation.raw_data_id == RawData.id
            ).filter(
                Annotation.annotator_id == annotator.id,
                Annotation.is_submitted == True,
                Annotation.created_at >= today_start_utc,
                Annotation.created_at <= today_end_utc,
            ).all()

            today_annotated = len(ann_records)
            correct_count = sum(1 for r in ann_records if r.result == 'correct')
            today_accuracy = round(correct_count / today_annotated * 100, 1) if today_annotated > 0 else None

            annotator_stats.append({
                'id': annotator.id,
                'name': annotator.name or annotator.username,
                'today_annotated': today_annotated,
                'today_accuracy': today_accuracy,
            })
        print(f"[看板统计] 标注员效能数据：{len(annotator_stats)} 人")
    except Exception as e:
        print(f"[ERROR] 获取标注员效能数据失败: {e}")
        annotator_stats = []

    # ====== DEBUG: 打印 by_date 构建结果 ======
    print(f"[DEBUG] by_date 构建前: date_range={date_range}, by_date_map keys={sorted(by_date_map.keys())}")
    print(f"[DEBUG] by_date 最终: {[{x['date']: x['total']} for x in by_date]}")

    # 构建返回数据
    return jsonify({
        'total_count': total_count,
        'compliant_count': compliant_count,
        'non_compliant_count': non_compliant_count,
        'violation_rate': violation_rate,
        'inconsistent_count': inconsistent_count,
        'inconsistent_rate': inconsistent_rate,
        'accuracy_rate': accuracy_rate,
        'accuracy_correct_count': acc_correct,
        'accuracy_total_reviewed': acc_total,
        'accuracy_empty': accuracy_empty,
        'disagree_rate': None,
        'by_instance': by_instance_map,
        'by_date': by_date,
        'top_violation_reasons': top_violation_reasons,
        'all_reasons_detail': all_reasons_detail,  # 全量明细，供"其他"展开
        'annotator_stats': annotator_stats,  # 标注员今日效能数据
        'record_count': record_count,
        'start_date': start_date,
        'end_date': end_date,
        'debug_batches': [],
        'empty': total_count == 0,
        'message': None if total_count > 0 else '该日期范围内无数据'
    })


@dashboard_bp.route('/overview', methods=['GET'])
def api_overview():
    """概览统计"""
    total = RawData.query.count()
    compliant = RawData.query.filter_by(ai_result='合规').count()
    non_compliant = total - compliant

    annotated = annotated_count()

    today = datetime.utcnow().date()
    today_new = RawData.query.filter(func.date(RawData.created_at) == today).count()

    return jsonify({
        "ai_total": total,
        "ai_compliant": compliant,
        "ai_non_compliant": non_compliant,
        "compliance_rate": round(compliant / total * 100, 1) if total > 0 else 0,
        "annotated": annotated,
        "pending": total - annotated,
        "today_new": today_new
    })


@dashboard_bp.route('/by-instance', methods=['GET'])
def api_by_instance():
    """按实例统计"""
    stats = db.session.query(
        RawData.instance_code,
        func.count(RawData.id).label('total'),
        func.sum(case((RawData.ai_result == '合规', 1), else_=0)).label('compliant')
    ).group_by(RawData.instance_code).all()

    result = []
    for inst, total, compliant in stats:
        result.append({
            "instance": inst,
            "total": total,
            "compliant": compliant or 0,
            "non_compliant": total - (compliant or 0)
        })

    return jsonify(result)


@dashboard_bp.route('/by-user', methods=['GET'])
def api_by_user():
    """按标注员统计"""
    users = User.query.filter_by(is_active=True).all()

    result = []
    for user in users:
        annotated = Annotation.query.filter_by(
            annotator_id=user.id,
            is_submitted=True
        ).count()

        progress = round(annotated / user.daily_quota * 100, 1) if user.daily_quota > 0 else 0

        result.append({
            "username": user.username,
            "name": user.name,
            "daily_quota": user.daily_quota,
            "annotated": annotated,
            "progress": progress
        })

    return jsonify(result)


@dashboard_bp.route('/trend', methods=['GET'])
def api_trend():
    """每日趋势"""
    days = request.args.get('days', 7, type=int)
    end_date = datetime.utcnow().date()
    start_date = end_date - timedelta(days=days)

    stats = db.session.query(
        func.date(RawData.created_at).label('date'),
        func.count(RawData.id).label('count')
    ).filter(
        func.date(RawData.created_at) >= start_date
    ).group_by(
        func.date(RawData.created_at)
    ).order_by('date').all()

    result = []
    for date, count in stats:
        result.append({
            "date": date.isoformat() if date else None,
            "count": count
        })

    return jsonify(result)


@dashboard_bp.route('/logs', methods=['GET'])
def api_logs():
    """拉取日志"""
    logs = FetchLog.query.order_by(FetchLog.fetch_time.desc()).limit(20).all()

    result = []
    for log in logs:
        result.append({
            "batch_id": log.batch_id,
            "env": log.env,
            "instances": log.instances,
            "total_fetched": log.total_fetched,
            "compliant_count": log.compliant_count,
            "non_compliant_count": log.non_compliant_count,
            "status": log.status,
            "fetch_time": log.fetch_time.isoformat() if log.fetch_time else None
        })

    return jsonify(result)


import re
import json as json_mod

# 导入 services 中的违规原因提取函数
from services.fetch_service import extract_violation_keywords, extract_error_reason


@dashboard_bp.route('/dashboard/reason-distribution', methods=['GET'])
def api_reason_distribution():
    """违规原因分布接口 - 从 iData 线上获取数据"""
    from config import ENV_CONFIG
    
    # 获取查询参数
    instances_param = request.args.get('instances', '')
    start_date = request.args.get('start_date', '')  # YYYYMMDD
    end_date = request.args.get('end_date', '')      # YYYYMMDD
    
    # 默认日期范围：最近7天
    if not start_date or not end_date:
        today = datetime.now()
        end_date = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=6)).strftime('%Y%m%d')
    
    year = start_date[:4]
    
    # 解析实例列表
    if instances_param:
        instance_list = [inst.strip() for inst in instances_param.split(',') if inst.strip()]
    else:
        instance_list = []
        for env_config in ENV_CONFIG.values():
            instance_list.extend(env_config.get('instances', []))
        instance_list = list(set(instance_list))
    
    print(f"[ReasonDistribution] 查询参数: start_date={start_date}, end_date={end_date}, instances={instance_list}")
    
    # 统计各违规类型
    reason_stats = {}  # {违规类型: 数量}
    total_violations = 0
    
    for instance in instance_list:
        template = get_default_sql_template(instance)
        if not template:
            continue
        
        # 生成SQL
        reason_sql = get_reason_distribution_sql(template, instance, start_date, end_date, year)
        
        # 执行查询
        results = query_idata(reason_sql, instance, None)
        
        if not results:
            continue
        
        # 解析结果，提取违规类型
        for row in results:
            if not isinstance(row, dict):
                continue
            
            reject_reason = row.get('reject_reason', '')
            if not reject_reason:
                continue
            
            # 提取违规类型
            violation_type = extract_violation_type(reject_reason)
            
            if violation_type not in reason_stats:
                reason_stats[violation_type] = 0
            reason_stats[violation_type] += 1
            total_violations += 1
    
    # 转换为数组格式，按数量降序排列
    reason_list = []
    for reason, count in sorted(reason_stats.items(), key=lambda x: x[1], reverse=True):
        reason_list.append({
            "name": reason,
            "value": count
        })
    
    print(f"[ReasonDistribution] 返回数据: total={total_violations}, types={len(reason_list)}")
    
    return jsonify({
        "total": total_violations,
        "reasons": reason_list,
        "start_date": start_date,
        "end_date": end_date
    })


@dashboard_bp.route('/dashboard/inconsistency-rate', methods=['GET'])
def api_inconsistency_rate():
    """机审不一致率接口 - 支持日期和实例筛选"""
    from config import ENV_CONFIG
    from sqlalchemy import or_
    from datetime import datetime, timedelta
    
    # 获取查询参数
    start_date = request.args.get('start_date', '')  # YYYYMMDD 或 YYYY-MM-DD
    end_date = request.args.get('end_date', '')
    instances_param = request.args.get('instances', '')  # 逗号分隔的实例列表
    
    # 解析实例列表
    instance_list = []
    if instances_param:
        instance_list = [inst.strip() for inst in instances_param.split(',') if inst.strip()]
    
    # 解析日期（转为与数据库一致的格式 YYYYMMDD）
    start_date_str = ''
    end_date_str = ''
    
    if start_date:
        # 统一转为 YYYYMMDD 格式
        start_date_str = start_date.replace('-', '').replace('/', '')
    if end_date:
        end_date_str = end_date.replace('-', '').replace('/', '')
    
    # 构建查询条件（使用 data_start_date/data_end_date 进行业务日期筛选）
    query = FetchLog.query.filter(FetchLog.review_status == 'completed')
    
    # 筛选条件：批次的业务日期范围与查询日期范围有重叠
    if start_date_str and end_date_str:
        # data_start_date <= end_date AND data_end_date >= start_date（区间有交集）
        query = query.filter(
            FetchLog.data_start_date <= end_date_str,
            FetchLog.data_end_date >= start_date_str
        )
    elif start_date_str:
        # 只有开始日期：筛选 data_end_date >= start_date
        query = query.filter(FetchLog.data_end_date >= start_date_str)
    elif end_date_str:
        # 只有结束日期：筛选 data_start_date <= end_date
        query = query.filter(FetchLog.data_start_date <= end_date_str)
    
    if instance_list:
        # 使用 instances 字段的 LIKE 匹配
        instance_filters = []
        for inst in instance_list:
            instance_filters.append(FetchLog.instances.like('%' + inst + '%'))
        query = query.filter(or_(*instance_filters))
    
    # 按 fetch_time 降序获取所有满足条件的批次
    batches = query.order_by(FetchLog.fetch_time.desc()).all()
    
    # 如果指定日期范围无数据，直接返回 null（不自动扩展）
    if not batches:
        print("[InconsistencyRate] 筛选范围内暂无互检完成的批次")
        return jsonify({
            "rate": None,
            "message": "当前筛选范围内暂无互检数据"
        })
    
    # 聚合计数
    total = 0
    inconsistent = 0
    batch_ids = []
    for b in batches:
        total += b.total_fetched or 0
        inconsistent += b.inconsistent_count or 0
        batch_ids.append(b.batch_id)
    
    if total == 0:
        return jsonify({
            "rate": None,
            "message": "所选范围内无有效数据"
        })
    
    rate = round(inconsistent / total * 100, 2)
    
    print(f"[InconsistencyRate] 筛选: {start_date}~{end_date}, 实例: {instance_list}")
    print(f"[InconsistencyRate] 批次: {len(batch_ids)}, 总数: {total}, ��一致: {inconsistent}, 比率: {rate}%")
    
    return jsonify({
        "rate": rate,
        "total": total,
        "inconsistent": inconsistent,
        "batch_count": len(batch_ids),
        "batches": batch_ids[:10]  # 最多返回10个批次ID
    })


# ====== 大模型审核准确率指标 ======

def _is_modela_correct(record):
    """
    判定模型A是否正确。

    规则（按 PRD v1.0 §2.4）：
    - modelb_consistent=True（一致）：默认正确，标注为 error 时修正为错误
    - modelb_consistent=False（不一致）：默认错误，标注为 correct 时修正为正确
    """
    if record.modelb_consistent is True:
        # 一致：默认正确，标注为 error 时修正为错误
        # ignore 视为正确（不影响准确率）
        return record.check_result != 'error'
    elif record.modelb_consistent is False:
        # 不一致：默认错误，标注为 correct 时修正为正确
        return record.check_result == 'correct'
    else:
        # null 漏审：不参与计算
        return None


@dashboard_bp.route('/dashboard/accuracy-stats', methods=['GET'])
def api_accuracy_stats():
    """准确率整体统计 - 返回当前筛选条件下的整体准确率"""
    # 获取查询参数
    instances_param = request.args.get('instances', '')
    start_date = request.args.get('start_date', '')  # YYYYMMDD
    end_date = request.args.get('end_date', '')      # YYYYMMDD

    # 默认日期范围：最近7天
    if not start_date or not end_date:
        today = datetime.now()
        end_date = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=6)).strftime('%Y%m%d')

    # 解析实例列表
    instance_list = []
    if instances_param:
        instance_list = [inst.strip() for inst in instances_param.split(',') if inst.strip()]

    print(f"[AccuracyStats] 查询参数: start_date={start_date}, end_date={end_date}, instances={instance_list}")

    # 仅纳入已完成互检且有结果的记录（modelb_consistent != None）
    query = RawData.query.filter(
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent != None
    )

    # 日期筛选（基于 gmt_created 字段）
    if start_date and end_date:
        # YYYYMMDD -> YYYY-MM-DD（gmt_created 存储格式）
        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt   = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        query = query.filter(
            RawData.gmt_created >= start_fmt,
            RawData.gmt_created <= end_fmt + ' 23:59:59'
        )

    if instance_list:
        query = query.filter(RawData.instance_code.in_(instance_list))

    records = query.all()
    print(f"[AccuracyStats] 互检有效记录数: {len(records)}")

    correct_count = 0
    total_reviewed = 0   # 有效分母（排除 ignore）
    ignored_count = 0    # 被标注为 ignore 的记录数（不参与准确率计算）
    skipped_count = 0     # 漏审记录数（modelb_consistent=null，已在 query 中排除）

    for rec in records:
        # ignore 记录不计入分母
        if rec.check_result == 'ignore':
            ignored_count += 1
            continue

        total_reviewed += 1
        is_correct = _is_modela_correct(rec)
        if is_correct is True:
            correct_count += 1
        # is_correct is False/null 不计入分子

    accuracy_rate = round(correct_count / total_reviewed * 100, 1) if total_reviewed > 0 else None
    empty = total_reviewed == 0

    print(f"[AccuracyStats] 结果: correct={correct_count}, total={total_reviewed}, "
          f"ignored={ignored_count}, accuracy={accuracy_rate}%")

    return jsonify({
        'accuracy_rate': accuracy_rate,
        'correct_count': correct_count,
        'total_reviewed': total_reviewed,
        'ignored_count': ignored_count,
        'skipped_count': skipped_count,
        'start_date': start_date,
        'end_date': end_date,
        'empty': empty
    })


@dashboard_bp.route('/dashboard/accuracy-trend', methods=['GET'])
def api_accuracy_trend():
    """准确率趋势 - 返回按天分组的准确率趋势数据"""
    # 获取查询参数
    instances_param = request.args.get('instances', '')
    start_date = request.args.get('start_date', '')  # YYYYMMDD
    end_date = request.args.get('end_date', '')      # YYYYMMDD

    # 默认日期范围：最近7天
    if not start_date or not end_date:
        today = datetime.now()
        end_date = today.strftime('%Y%m%d')
        start_date = (today - timedelta(days=6)).strftime('%Y%m%d')

    # 解析实例列表
    instance_list = []
    if instances_param:
        instance_list = [inst.strip() for inst in instances_param.split(',') if inst.strip()]

    print(f"[AccuracyTrend] 查询参数: start_date={start_date}, end_date={end_date}, instances={instance_list}")

    # 仅纳入已完成互检且有结果的记录
    query = RawData.query.filter(
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent != None,
        RawData.gmt_created != None
    )

    if start_date and end_date:
        # YYYYMMDD -> YYYY-MM-DD（gmt_created 存储格式）
        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt   = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        query = query.filter(
            RawData.gmt_created >= start_fmt,
            RawData.gmt_created <= end_fmt + ' 23:59:59'
        )

    if instance_list:
        query = query.filter(RawData.instance_code.in_(instance_list))

    records = query.all()

    # 按天分组统计
    date_map = {}  # {date_str: {'correct': 0, 'total': 0}}

    for rec in records:
        # 跳过 ignore
        if rec.check_result == 'ignore':
            continue

        # 截取日期（gmt_created 前10位：YYYY-MM-DD）
        date_str = (rec.gmt_created or '')[:10]
        if not date_str:
            continue

        if date_str not in date_map:
            date_map[date_str] = {'correct': 0, 'total': 0}

        date_map[date_str]['total'] += 1
        is_correct = _is_modela_correct(rec)
        if is_correct is True:
            date_map[date_str]['correct'] += 1

    # 构建趋势数据（按日期升序）
    trend = []
    for date_str in sorted(date_map.keys()):
        data = date_map[date_str]
        accuracy_rate = round(data['correct'] / data['total'] * 100, 1) if data['total'] > 0 else None
        trend.append({
            'date': date_str,
            'accuracy_rate': accuracy_rate,
            'correct_count': data['correct'],
            'total_reviewed': data['total']
        })

    empty = len(trend) == 0

    print(f"[AccuracyTrend] 结果: {len(trend)} 天数据, empty={empty}")

    return jsonify({
        'trend': trend,
        'start_date': start_date,
        'end_date': end_date,
        'empty': empty
    })


