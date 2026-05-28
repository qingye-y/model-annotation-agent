# -*- coding: utf-8 -*-
"""数据获取蓝图 - 从 services 导入业务逻辑"""
from flask import Blueprint, request, jsonify
from flask_login import login_required
import threading
import time
from datetime import datetime
try:
    import pandas as pd
except ImportError:
    pd = None
from models import db, RawData, FetchLog, DailyStats, QcRecord, DispatchLog

# 服务层导入
from services.fetch_service import (
    get_idata_cookie, build_sql, execute_sql_query, replace_params,
    extract_violation_keywords, extract_error_reason,
    generate_daily_stats, update_daily_stats_inconsistency,
    get_instance_rule_mapping, fetch_data_from_idata, fetch_data_with_template,
    get_pipeline_sql_by_category, replace_pipeline_params
)
from services.utils import to_beijing_time

data_fetch_bp = Blueprint('data_fetch', __name__)


# ========== 路由函数 ==========

@data_fetch_bp.route('/api/data-fetch', methods=['POST'])
def api_data_fetch():
    """数据拉取接口 - 支持多实例遍历 + 实时进度更新"""
    data = request.get_json() or {}
    env = data.get('env', '')
    instances_input = data.get('instances', [])
    sample_percent = data.get('sample_percent', 100)
    start_date = data.get('start_date', '')
    end_date = data.get('end_date', '')

    # 支持 template_id
    template_id = data.get('template_id')
    params = data.get('params', {})

    # 规范化实例列表（必须最先确定，以供后续 env 判断使用）
    if isinstance(instances_input, list) and instances_input:
        final_instances = instances_input
    elif isinstance(instances_input, str) and instances_input:
        final_instances = [i.strip() for i in instances_input.split(',') if i.strip()]
    else:
        # 从模板获取实例列表
        from models import SqlTemplate
        if template_id:
            config = SqlTemplate.query.get(template_id)
            if config and config.instances:
                final_instances = [i.strip() for i in config.instances.split(',') if i.strip()]
            else:
                final_instances = []
        else:
            return jsonify({"success": False, "message": "请选择至少一个实例"}), 400

    if not final_instances:
        return jsonify({"success": False, "message": "实例列表为空"}), 400

    # 自动判断环境
    if not env:
        lcy_instances = ['YNLCY', 'GXLCY']
        env = '云环境'
        for inst in final_instances:
            if inst in lcy_instances:
                env = '乐采云环境'
                break

    # 验证 env 是否有效（不在白名单中的值自动降级）
    from config import ENV_CONFIG
    if env not in ENV_CONFIG:
        # 降级：按实例判断
        lcy_instances = ['YNLCY', 'GXLCY']
        env = '乐采云环境' if any(i in lcy_instances for i in final_instances) else '云环境'

    # 合并日期参数（格式化：去除连字符转为 YYYYMMDD）
    if start_date:
        params['start_date'] = start_date.replace('-', '').replace('/', '')
    if end_date:
        params['end_date'] = end_date.replace('-', '').replace('/', '')
    if start_date and len(start_date) >= 4:
        params['year'] = start_date[:4]

    # 生成批次号
    batch_id = "BAT-" + datetime.now().strftime('%Y%m%d%H%M%S')

    # 插入 running 状态的日志（完整字段）
    log = FetchLog(
        batch_id=batch_id,
        env=env,
        instances=','.join(final_instances),
        sample_percent=sample_percent,
        total_fetched=0,
        original_total=0,
        original_compliant=0,
        original_non_compliant=0,
        compliant_count=0,
        non_compliant_count=0,
        status='running',
        source='fetch',
        data_start_date=start_date,
        data_end_date=end_date
    )
    db.session.add(log)
    db.session.commit()

    all_fetched = []
    total_compliant = 0
    total_non_compliant = 0
    total_original_compliant = 0
    total_original_violation = 0
    total_skipped = 0
    all_original_totals = {}
    all_error_reasons = {}  # {instance: {reason: count}}
    all_instance_original_compliant = {}
    all_instance_original_non_compliant = {}

    for instance in final_instances:
        try:
            # 清理该实例该日期范围内的旧 RawData（允许重复拉取覆盖）
            existing_product_ids = set()
            if start_date and end_date:
                start_date_slash = f"{start_date[:4]}/{start_date[4:6]}/{start_date[6:8]}" if len(start_date) == 8 else start_date
                end_date_slash = f"{end_date[:4]}/{end_date[4:6]}/{end_date[6:8]}" if len(end_date) == 8 else end_date

                # 删除该实例该日期范围的旧 RawData（来源=本次拉取环境）
                old_count = RawData.query.filter(
                    RawData.created_date >= start_date_slash,
                    RawData.created_date <= end_date_slash,
                    RawData.instance_code == instance,
                    RawData.source == 'fetch'
                ).delete(synchronize_session='fetch')
                if old_count > 0:
                    print(f"[DEBUG 清理] {instance} 已删除 {old_count} 条旧 RawData（日期: {start_date_slash}~{end_date_slash}）")
            
            print(f"[DEBUG 去重] {instance} 旧数据已清理，准备写入新数据")

            # 判断是否使用自定义模板
            from models import SqlTemplate
            sql_template = None
            if template_id:
                config = SqlTemplate.query.get(template_id)
                if config:
                    sql_template = config.sql_text
                    # 对每个实例单独替换参数
                    instance_params = params.copy()
                    instance_params['instance'] = instance
                    instance_sql = replace_params(sql_template, instance_params)
                    # 传入已抽取的审核ID列表，实现增量抽样
                    result = fetch_data_with_template(env, instance, instance_sql, sample_percent, start_date, end_date)
                else:
                    result = fetch_data_from_idata(env, instance, start_date, end_date, sample_percent)
            else:
                result = fetch_data_from_idata(env, instance, start_date, end_date, sample_percent)

            # 直接使用拉取结果（旧数据已在拉取前清理）
            unique_fetched_data = result.get('fetched_data', [])
            skipped_count = result.get('excluded_count', 0)
            print(f"[DEBUG 审计] {instance} 拉取{len(unique_fetched_data)}条, 写入{len(unique_fetched_data)}条")

            # 基于去重后数据统计
            unique_compliant = 0
            unique_violation = 0
            for d in unique_fetched_data:
                val = d.get('AI审核结果', d.get('ai_result', ''))
                if val in ['合规', '1', 'PASS']:
                    unique_compliant += 1
                elif val in ['违规', '0', 'REJECT']:
                    unique_violation += 1

            # 存储去重后的数据
            for row in unique_fetched_data:
                raw = RawData(
                    supplier_id=str(row.get('供应商id', '')),
                    label=str(row.get('标签', '')),
                    ai_audit_id=str(row.get('AI审核id', '')),
                    audit_id=str(row.get('审核id', '')),
                    product_id=str(row.get('商品id', '')),
                    ai_result=str(row.get('AI审核结果', '')),
                    audit_result=str(row.get('审核单结果', '')),
                    human_reject_item=str(row.get('人审拒绝项', '')),
                    reject_reason=str(row.get('拒绝原因', '')),
                    human_comment=str(row.get('人审意见', '')),
                    ai_reject_reason=str(row.get('AI拒绝原因', '')),
                    ai_explain=str(row.get('AI拒绝解释', '')),
                    shop_name=str(row.get('店铺名称', '')),
                    product_name=str(row.get('商品名称', '')),
                    category=str(row.get('类目', '')),
                    main_image=str(row.get('主图', '')),
                    detail_image=str(row.get('详情图', '')),
                    sku_image=str(row.get('sku图', '')),
                    spu_image=str(row.get('spu图', '')),
                    product_link=str(row.get('商品链接', '')),
                    check_result=str(row.get('标注结果：1=正确 0=错误', '')),
                    annotation=str(row.get('备注', '')),
                    instance_code=str(row.get('实例编码', instance)),
                    created_date=str(row.get('创建日期', '')),
                    annotator=str(row.get('标注人', '')),
                    random_num=float(row.get('随机数', 0)) if row.get('随机数') else 0,
                    change_category=str(row.get('变更类别', '')),
                    gmt_created=str(row.get('创建时间', '')),
                    fetch_batch_id=batch_id,
                    source='fetch'
                )
                db.session.add(raw)

            # 累加统计
            all_fetched.extend(unique_fetched_data)
            all_original_totals[instance] = result.get('original_total', 0)
            print(f"[DEBUG 审计] {instance} RawData 写入前待提交: {len(unique_fetched_data)}条")
            # 从 iData 线上全量统计 error_reasons（替代 RawData 本地查询）
            from services.fetch_service import fetch_error_reasons_online
            sd = params.get('start_date', start_date.replace('-', '').replace('/', ''))
            ed = params.get('end_date', end_date.replace('-', '').replace('/', ''))
            inst_error_reasons = fetch_error_reasons_online(env, instance, sd, ed, max_records=10000)
            # inst_error_reasons = {'by_date': {date: {reason: count}}, 'global': {reason: count}}
            all_error_reasons[instance] = inst_error_reasons['by_date']
            print(f"[DEBUG] {instance} error_reasons 线上统计: "
                  f"days={len(inst_error_reasons['by_date'])}, global_tags={len(inst_error_reasons['global'])})")
            all_instance_original_compliant[instance] = result.get('original_compliant', 0)
            all_instance_original_non_compliant[instance] = result.get('original_non_compliant', 0)

            if 'original_compliant' in result:
                total_original_compliant += result['original_compliant']
            if 'original_non_compliant' in result:
                total_original_violation += result['original_non_compliant']

            # 每完成一个实例，更新一次 FetchLog 进度
            log.total_fetched = len(all_fetched)
            log.original_total = sum(all_original_totals.values())
            log.compliant_count = total_compliant + unique_compliant
            log.non_compliant_count = total_non_compliant + unique_violation
            log.original_compliant = total_original_compliant
            log.original_non_compliant = total_original_violation
            log.skipped_duplicates = total_skipped
            print(f"[DEBUG 审计] {instance} 提交前: session内RawData pending + FetchLog 更新，db.session.commit()")
            db.session.commit()
            print(f"[DEBUG 审计] {instance} 提交完成!")

            total_compliant += unique_compliant
            total_non_compliant += unique_violation

        except Exception as e:
            log.status = 'failed'
            db.session.commit()
            return jsonify({
                "success": False,
                "message": f"实例 {instance} 拉取失败: {str(e)}"
            }), 500

    # 计算原始合规/违规总数
    original_compliant_total = total_original_compliant
    original_non_compliant_total = total_original_violation

    # 兼容：如果未统计到，使用抽样数据推算
    if (original_compliant_total == 0 and original_non_compliant_total == 0) and sample_percent and sample_percent > 0 and sample_percent < 100:
        ratio = 100.0 / sample_percent
        original_compliant_total = int(total_compliant * ratio)
        original_non_compliant_total = int(total_non_compliant * ratio)

    # 更新日志为完成
    log.total_fetched = len(all_fetched)
    log.compliant_count = total_compliant
    log.non_compliant_count = total_non_compliant
    log.original_compliant = original_compliant_total
    log.original_non_compliant = original_non_compliant_total
    log.skipped_duplicates = total_skipped
    log.status = 'completed'
    log.data_start_date = start_date
    log.data_end_date = end_date
    log.original_total = sum(all_original_totals.values())
    db.session.commit()

    # 清理僵尸 FetchLog：RawData 已被本次拉取清空但 FetchLog 仍存在的旧批次
    from sqlalchemy import not_
    orphan_batches = db.session.query(FetchLog.batch_id).filter(
        FetchLog.batch_id != batch_id,
        ~FetchLog.batch_id.in_(
            db.session.query(RawData.fetch_batch_id).filter(RawData.fetch_batch_id.isnot(None)).distinct()
        )
    ).all()
    for ob in orphan_batches:
        orphan_id = ob[0]
        orphan_log = FetchLog.query.filter_by(batch_id=orphan_id).first()
        if orphan_log and orphan_log.source == 'fetch':
            print(f"[DEBUG 清理] 已删除僵尸批次 {orphan_id}（RawData 已被清空）")
            db.session.delete(orphan_log)
    if orphan_batches:
        db.session.commit()

    print(f"[DEBUG 审计] FetchLog 最终写入: batch={batch_id}, original_total={log.original_total}, "
          f"total_fetched={log.total_fetched}, skipped={total_skipped}, "
          f"compliant={total_compliant}, non_compliant={total_non_compliant}")

    # 生成 DailyStats 统计快照（每个实例，使用 iData 按日精确计数）
    _start = params.get('start_date', start_date.replace('-', '').replace('/', ''))
    _end = params.get('end_date', end_date.replace('-', '').replace('/', ''))
    for instance in final_instances:
        inst_original_total = all_original_totals.get(instance, 0)
        inst_error_reasons = all_error_reasons.get(instance, {})
        
        # 额外查询 iData 按日 GROUP BY 的精确计数（避免比例分配导致的误差）
        # ========== S6: 每日分组 COUNT（从管道读取）==========
        daily_counts = None
        try:
            detail_sql = build_sql(instance, _start, _end)
            daily_tpl = get_pipeline_sql_by_category(env, 'daily')
            if daily_tpl:
                daily_count_sql = replace_pipeline_params(daily_tpl['sql_text'], detail_sql=detail_sql)
            else:
                # 兜底：使用原有硬编码逻辑
                daily_count_sql = f"""
                    SELECT `创建日期`, `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
                    FROM ({detail_sql}) t
                    GROUP BY `创建日期`, `AI审核结果`
                    ORDER BY `创建日期`
                """
            daily_result = execute_sql_query(daily_count_sql, instance, env)
            if isinstance(daily_result, list) and daily_result:
                daily_counts = {}
                for row in daily_result:
                    if not isinstance(row, dict):
                        continue
                    raw_date = str(row.get('创建日期', '') or '').strip()
                    status = str(row.get('AI审核结果', '') or '').strip()
                    cnt = int(row.get('cnt', 0) or 0)
                    date_str = raw_date.replace('/', '').replace('-', '')
                    if len(date_str) != 8:
                        continue
                    daily_counts.setdefault(date_str, {'total': 0, 'compliant': 0, 'non_compliant': 0})
                    daily_counts[date_str]['total'] += cnt
                    if status in ('合规', '1', 'PASS'):
                        daily_counts[date_str]['compliant'] = cnt
                    elif status in ('违规', '0', 'REJECT'):
                        daily_counts[date_str]['non_compliant'] = cnt
                print(f"[DEBUG 审计] {instance} 按日 COUNT: {len(daily_counts)} 天")
        except Exception as e:
            print(f"[WARN] {instance} 按日 COUNT 查询失败，回退到比例分配: {e}")
            daily_counts = None
        
        print(f"[DEBUG 审计] generate_daily_stats 调用: batch={batch_id}, instance={instance}, "
              f"original_total={inst_original_total}, error_reasons_days={len(inst_error_reasons)}, "
              f"daily_counts={'YES' if daily_counts else 'NO'}")
        generate_daily_stats(
            instance,
            _start,
            _end,
            batch_id,
            original_total=inst_original_total,
            original_compliant=all_instance_original_compliant.get(instance, 0),
            original_non_compliant=all_instance_original_non_compliant.get(instance, 0),
            error_reasons_by_date=inst_error_reasons,
            daily_counts_by_date=daily_counts
        )

    # 获取模板名称
    template_name = ''
    if template_id:
        from models import SqlTemplate
        config = SqlTemplate.query.get(template_id)
        if config:
            template_name = config.name

    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "total_fetched": len(all_fetched),
        "compliant_count": total_compliant,
        "non_compliant_count": total_non_compliant,
        "original_total": sum(all_original_totals.values()),
        "sample_percent": sample_percent,
        "instances": final_instances,
        "original_totals": all_original_totals,
        "skipped_duplicates": total_skipped,
        "template_id": template_id,
        "template_name": template_name
    })


# ========== 测试接口：直接执行 SQL ==========
@data_fetch_bp.route('/api/test-sql', methods=['POST'])
def api_test_sql():
    """测试执行 SQL，返回原始结果（用于调试）"""
    data = request.get_json()
    sql = data.get('sql', '')
    instance = data.get('instance', '')
    env = data.get('env', '云环境')

    if not sql or not instance:
        return jsonify({"success": False, "message": "缺少 sql 或 instance 参数"}), 400

    try:
        result = execute_sql_query(sql, instance, env)
        return jsonify({
            "success": True,
            "result": result,
            "result_type": type(result).__name__,
            "sql": sql
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": str(e),
            "sql": sql
        }), 500


# ========== 兼容旧接口 ==========
@data_fetch_bp.route('/fetch', methods=['POST'])
def legacy_fetch():
    """兼容旧版接口"""
    return api_data_fetch()


@data_fetch_bp.route('/fetch-batch', methods=['POST'])
def legacy_fetch_batch():
    """兼容旧版批量接口"""
    return api_data_fetch()


# ========== 任务批次运行状态接口 ==========
@data_fetch_bp.route('/api/task-batches/running', methods=['GET'])
def api_task_batches_running():
    """获取当前正在执行的任务"""
    source = request.args.get('source', '')
    query = FetchLog.query.filter_by(status='running')
    
    if source:
        query = query.filter(FetchLog.source == source)
    
    running_task = query.order_by(FetchLog.fetch_time.desc()).first()
    
    if running_task:
        return jsonify({
            "running": True,
            "batch_id": running_task.batch_id,
            "total_fetched": running_task.total_fetched,
            "original_total": running_task.original_total,
            "compliant_count": running_task.compliant_count,
            "non_compliant_count": running_task.non_compliant_count,
            "status": running_task.status,
            "env": running_task.env,
            "instances": running_task.instances,
            "sample_percent": running_task.sample_percent,
            "source": running_task.source or 'fetch',
            "fetch_time": to_beijing_time(running_task.fetch_time)
        })
    else:
        return jsonify({"running": False})


# ========== 任务历史接口 ==========
@data_fetch_bp.route('/api/task-batches', methods=['GET'])
def api_task_batches():
    """获取拉取批次列表"""
    env = request.args.get('env', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    source = request.args.get('source', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    instances = request.args.get('instances', '')
    rule = request.args.get('rule', '')
    review_status_filter = request.args.get('review_status', '')  # v1.2: 跳转优化
    task_generated_filter = request.args.get('task_generated', '')  # v1.4: 任务分配状态筛选

    # 如果指定了规则，从映射中获取实例列表
    if rule and not instances:
        mapping = get_instance_rule_mapping()
        if mapping:
            rule_instances = [k for k, v in mapping.items() if v == rule]
            if rule_instances:
                instances = ','.join(rule_instances)

    # 构建查询
    query = FetchLog.query

    if env:
        query = query.filter(FetchLog.env == env)

    effective_start = date_from or start_date
    if effective_start:
        try:
            if len(effective_start) == 8:
                from_date = datetime.strptime(effective_start, '%Y%m%d')
            else:
                from_date = datetime.strptime(effective_start, '%Y-%m-%d')
            query = query.filter(FetchLog.fetch_time >= from_date)
        except:
            pass

    effective_end = date_to or end_date
    if effective_end:
        try:
            if len(effective_end) == 8:
                to_date = datetime.strptime(effective_end, '%Y%m%d')
            else:
                to_date = datetime.strptime(effective_end, '%Y-%m-%d')
            to_date = to_date.replace(hour=23, minute=59, second=59)
            query = query.filter(FetchLog.fetch_time <= to_date)
        except:
            pass

    if source:
        query = query.filter(FetchLog.source == source)

    if instances:
        instance_list = [i.strip() for i in instances.split(',') if i.strip()]
        if instance_list:
            from sqlalchemy import or_
            filters = []
            for inst in instance_list:
                filters.append(FetchLog.instances.like('%' + inst + '%'))
            query = query.filter(or_(*filters))

    # v1.2: 互检状态筛选（跳转优化）
    if review_status_filter == 'completed':
        query = query.filter(FetchLog.review_status == 'completed')
    elif review_status_filter == 'pending':
        query = query.filter(FetchLog.review_status != 'completed')

    # v1.4: 任务分配状态筛选（已分配 = 互检完成 + 任务已生成）
    if task_generated_filter == 'true':
        query = query.filter(FetchLog.review_status == 'completed', FetchLog.task_generated == True)
    elif task_generated_filter == 'false':
        query = query.filter(FetchLog.task_generated != True)

    batches = query.order_by(FetchLog.fetch_time.desc()).all()
    
    result = []
    for b in batches:
        total_items = b.total_fetched or 0
        reviewed_count = 0
        if total_items > 0:
            reviewed_count = RawData.query.filter_by(
                fetch_batch_id=b.batch_id,
                modelb_reviewed=True
            ).count()
        
        db_review_status = b.review_status or 'pending'
        review_status_map = {
            'pending': '未互检',
            'running': '互检中',
            'completed': '已互检',
            'failed': '互检失败',
            'aborted': '互检中止'
        }
        review_status = review_status_map.get(db_review_status, '未互检')
        
        result.append({
            "batch_id": b.batch_id,
            "env": b.env or '',
            "instances": b.instances or '',
            "sample_percent": b.sample_percent,
            "total_fetched": b.total_fetched,
            "original_total": b.original_total or 0,
            "original_compliant": b.original_compliant or 0,
            "original_non_compliant": b.original_non_compliant or 0,
            "compliant_count": b.compliant_count,
            "non_compliant_count": b.non_compliant_count,
            "fetch_time": to_beijing_time(b.fetch_time),
            "status": b.status,
            "review_status": review_status,
            "source": b.source or 'fetch',
            "data_start_date": b.data_start_date or '',
            "data_end_date": b.data_end_date or '',
            "reviewed_count": reviewed_count,
            "total_items": total_items,
            # v1.4 任务调度模块
            "task_generated": bool(b.task_generated),
            "task_generate_time": b.task_generate_time or '',
            "task_sample_percent": b.task_sample_percent or 5.0,
        })
    
    return jsonify({"batches": result})


# ========== 批次概览接口 ==========
@data_fetch_bp.route('/api/task-batches/<batch_id>/summary', methods=['GET'])
def api_task_batch_summary(batch_id):
    """获取批次基本信息（用于详情页顶部概览）"""
    log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not log:
        return jsonify({"success": False, "message": f"批次 {batch_id} 不存在"}), 404

    total_items = RawData.query.filter_by(fetch_batch_id=batch_id).count()
    reviewed_count = RawData.query.filter_by(
        fetch_batch_id=batch_id, modelb_reviewed=True
    ).count()

    review_status_map = {
        'pending': '未互检',
        'running': '互检中',
        'completed': '已互检',
        'failed': '互检失败',
        'aborted': '互检中止'
    }

    return jsonify({
        "success": True,
        "batch_id": log.batch_id,
        "env": log.env or '',
        "instances": log.instances or '',
        "source": log.source or 'fetch',
        "status": log.status,
        "review_status": review_status_map.get(log.review_status or 'pending', '未互检'),
        "fetch_time": to_beijing_time(log.fetch_time) if log.fetch_time else '',
        "data_start_date": log.data_start_date or '',
        "data_end_date": log.data_end_date or '',
        "original_total": log.original_total or 0,
        "original_compliant": log.original_compliant or 0,
        "original_non_compliant": log.original_non_compliant or 0,
        "total_items": total_items,
        "reviewed_count": reviewed_count,
        # v1.4 任务调度模块
        "task_generated": bool(log.task_generated),
        "task_generate_time": log.task_generate_time or '',
        "task_sample_percent": log.task_sample_percent or 5.0,
    })


# ========== 批次明细接口 ==========
@data_fetch_bp.route('/api/task-batches/<batch_id>/items', methods=['GET'])
def api_task_batch_items(batch_id):
    """获取指定批次的明细数据，支持筛选和分页"""
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    ai_result_filter = request.args.get('ai_result', '')
    instance_filter = request.args.get('instance', '')
    keyword = request.args.get('keyword', '')
    # v3.1: 任务分配状态筛选（支持：assigned/unassigned/annotated）
    task_status_filter = request.args.get('task_status', '')

    # 基础查询（无筛选，用于统计全量）
    base_query = RawData.query.filter_by(fetch_batch_id=batch_id)
    all_items = base_query.all()

    # 统计：基于全量数据
    instance_stats = {}
    total_compliant = 0
    total_non_compliant = 0
    modelb_stats = {
        'A合规B合规': 0, 'A合规B违规': 0,
        'A违规B合规': 0, 'A违规B违规': 0, '未互检': 0
    }
    for item in all_items:
        code = item.instance_code or '未知'
        if code not in instance_stats:
            instance_stats[code] = {'total': 0, 'compliant': 0, 'non_compliant': 0}
        instance_stats[code]['total'] += 1
        is_compliant = item.ai_result and ('合规' in str(item.ai_result) or 'å' in str(item.ai_result))
        if is_compliant:
            instance_stats[code]['compliant'] += 1
            total_compliant += 1
        else:
            instance_stats[code]['non_compliant'] += 1
            total_non_compliant += 1
        if item.modelb_reviewed:
            ai_res = str(item.ai_result or '')
            modelb_res = str(item.modelb_result or '')
            if '合规' in ai_res and '合规' in modelb_res:
                modelb_stats['A合规B合规'] += 1
            elif '合规' in ai_res and '违规' in modelb_res:
                modelb_stats['A合规B违规'] += 1
            elif '违规' in ai_res and '合规' in modelb_res:
                modelb_stats['A违规B合规'] += 1
            elif '违规' in ai_res and '违规' in modelb_res:
                modelb_stats['A违规B违规'] += 1
            else:
                modelb_stats['未互检'] += 1
        else:
            modelb_stats['未互检'] += 1

    instances_list = [{'instance_code': code, 'total': instance_stats[code]['total'],
                       'compliant': instance_stats[code]['compliant'],
                       'non_compliant': instance_stats[code]['non_compliant']}
                      for code in sorted(instance_stats.keys())]
    summary = {
        'total': {'total': len(all_items), 'compliant': total_compliant, 'non_compliant': total_non_compliant},
        'modelb': modelb_stats, 'instances': instances_list
    }

    # 筛选后的查询（用于分页数据）
    filtered_query = RawData.query.filter_by(fetch_batch_id=batch_id)
    if ai_result_filter:
        if ai_result_filter == '合规':
            filtered_query = filtered_query.filter(RawData.ai_result.in_(['合规', '1', 'PASS']))
        elif ai_result_filter == '违规':
            filtered_query = filtered_query.filter(RawData.ai_result.in_(['违规', '0', 'REJECT']))
    if instance_filter:
        filtered_query = filtered_query.filter(RawData.instance_code == instance_filter)
    if keyword:
        filtered_query = filtered_query.filter(
            RawData.product_name.ilike(f'%{keyword}%') |
            RawData.product_id.ilike(f'%{keyword}%') |
            RawData.ai_reject_reason.ilike(f'%{keyword}%')
        )

    # AB差异筛选（v2.0: 精简为 consistent / inconsistent）
    diff_status = request.args.get('diff_status', '')
    if diff_status == 'consistent':
        filtered_query = filtered_query.filter(RawData.modelb_consistent == True)
    elif diff_status == 'inconsistent':
        filtered_query = filtered_query.filter(RawData.modelb_consistent == False)

    # v2.1: 互检状态筛选（独立筛选项，与模型B结果分离）
    review_status_filter = request.args.get('review_status', '')
    if review_status_filter == 'reviewed':
        filtered_query = filtered_query.filter(RawData.modelb_reviewed == True)
    elif review_status_filter == 'not_reviewed':
        filtered_query = filtered_query.filter(RawData.modelb_reviewed == False)

    # v2.1: 模型B审核结果筛选（回退为 3 选项：全部/合规/违规，移除"未互检""漏审"）
    modelb_result = request.args.get('modelb_result', '')
    if modelb_result == '合规':
        filtered_query = filtered_query.filter(RawData.modelb_result.in_(['合规', '1', 'PASS']))
    elif modelb_result == '违规':
        filtered_query = filtered_query.filter(RawData.modelb_result.in_(['违规', '0', 'REJECT']))
    elif modelb_result == '无法审核':  # v1.1新增
        filtered_query = filtered_query.filter(RawData.modelb_result == '无法审核')

    # v3.1: 任务分配状态筛选（派生于现有字段，向后兼容 task_status 字段为 NULL 的历史数据）
    # 已标注：check_result 非空
    # 已分配未标注：annotator 非空但 check_result 为空
    # 未分配：annotator 为空
    if task_status_filter == 'annotated':
        filtered_query = filtered_query.filter(
            db.and_(RawData.check_result != '', RawData.check_result.isnot(None))
        )
    elif task_status_filter == 'assigned':
        filtered_query = filtered_query.filter(
            db.and_(
                db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),
                db.or_(RawData.check_result == '', RawData.check_result.is_(None))
            )
        )
    elif task_status_filter == 'unassigned':
        # 修复：annotator IS NULL 时 annotator=='' 返回 NULL（非 False），故用 OR 兜底
        # 同时要求 check_result 也为空（未完成标注）
        filtered_query = filtered_query.filter(
            db.and_(
                db.or_(RawData.annotator == '', RawData.annotator.is_(None)),
                db.or_(RawData.check_result == '', RawData.check_result.is_(None))
            )
        )

    total = filtered_query.count()
    items_query = filtered_query.order_by(RawData.id.desc()).offset((page - 1) * per_page).limit(per_page)
    items = items_query.all()

    result = []
    for item in items:
        def truncate(val, max_len=80):
            if not val:
                return ''
            s = str(val)
            return s[:max_len] + '...' if len(s) > max_len else s

        result.append({
            "id": item.id,
            "product_id": item.product_id,
            "product_name": item.product_name,
            "category": item.category,
            "ai_result": item.ai_result,
            "ai_audit_id": item.ai_audit_id,
            "audit_result": item.audit_result,
            "audit_id": item.audit_id,
            "instance_code": item.instance_code,
            "shop_name": item.shop_name,
            "supplier_id": item.supplier_id,
            "label": item.label,
            "human_reject_item": item.human_reject_item,
            "reject_reason": item.reject_reason,
            "ai_reject_reason": item.ai_reject_reason,
            "ai_explain": item.ai_explain,
            "human_comment": item.human_comment,
            "main_image": truncate(item.main_image),
            "detail_image": truncate(item.detail_image),
            "sku_image": truncate(item.sku_image),
            "spu_image": truncate(item.spu_image),
            "product_link": truncate(item.product_link),
            "check_result": item.check_result,
            "annotation": item.annotation,
            "created_date": item.created_date,
            "change_category": item.change_category,
            "random_num": item.random_num,
            "modelb_result": item.modelb_result,
            "modelb_reason": item.modelb_reason,
            "modelb_consistent": item.modelb_consistent,
            "modelb_reviewed": item.modelb_reviewed,
            # v3.1: 任务分配状态字段（派生于现有字段，向后兼容）
            "annotator_display": item.annotator or '',
            "dispatch_batch_no": item.dispatch_batch_no or '',
            "task_status": item.task_status if item.task_status else (
                'annotated' if (item.check_result and item.check_result.strip()) else
                ('assigned' if (item.annotator and item.annotator.strip()) else 'unassigned')
            ),
        })

    # 计算 max_reviewed_id（v2.0: 用于前端四分类中的"漏审"边界判断）
    max_reviewed_id = db.session.query(db.func.max(RawData.id)).filter(
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True
    ).scalar() or None

    return jsonify({
        "items": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "summary": summary,
        "max_reviewed_id": max_reviewed_id
    })


# ========== 中止批次接口 ==========
@data_fetch_bp.route('/api/task-batches/<batch_id>/abort', methods=['PUT'])
def api_abort_batch(batch_id):
    """中止指定批次（将running状态改为failed）"""
    log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not log:
        return jsonify({"success": False, "message": "批次不存在"}), 404

    if log.status != 'running':
        return jsonify({"success": False, "message": "任务不在进行中，无法中止"}), 400

    try:
        log.status = 'failed'
        log.fetch_time = datetime.utcnow()
        db.session.commit()
        return jsonify({"success": True, "message": "任务已中止"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"中止失败: {str(e)}"}), 500


# ========== 删除批次接口 ==========
@data_fetch_bp.route('/api/task-batches/<batch_id>', methods=['DELETE'])
def api_delete_batch(batch_id):
    """删除指定批次及其关联数据"""
    log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not log:
        return jsonify({"success": False, "message": "批次不存在"}), 404

    if log.status == 'running':
        return jsonify({"success": False, "message": "正在执行的任务无法删除"}), 400

    try:
        # 0. 收集该批次的 RawData IDs（用于清理关联表）
        raw_ids_subq = db.session.query(RawData.id).filter(RawData.fetch_batch_id == batch_id).subquery()
        raw_ids_list = [r[0] for r in db.session.query(RawData.id).filter(RawData.fetch_batch_id == batch_id).all()]

        # 1. 删除 QcRecord（依赖 annotation_id → 先删）
        from models import Annotation
        if raw_ids_list:
            ann_ids = db.session.query(Annotation.id).filter(Annotation.raw_data_id.in_(raw_ids_list)).all()
            ann_ids_list = [a[0] for a in ann_ids]
            qc_count = QcRecord.query.filter(QcRecord.annotation_id.in_(ann_ids_list)).delete(synchronize_session='fetch') if ann_ids_list else 0
        else:
            qc_count = 0

        # 2. 删除 Annotation
        ann_count = Annotation.query.filter(Annotation.raw_data_id.in_(raw_ids_list)).delete(synchronize_session='fetch') if raw_ids_list else 0

        # 3. 删除 DispatchLog（依赖 batch_id）
        dl_count = DispatchLog.query.filter_by(batch_id=batch_id).delete(synchronize_session='fetch')

        # 4. 删除 DailyStats
        ds_count = DailyStats.query.filter_by(batch_id=batch_id).delete(synchronize_session='fetch')

        # 5. 删除 RawData
        rd_count = RawData.query.filter_by(fetch_batch_id=batch_id).delete(synchronize_session='fetch')

        # 6. 删除 FetchLog
        db.session.delete(log)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "批次已删除",
            "deleted": {"raw_data": rd_count, "daily_stats": ds_count,
                        "annotations": ann_count, "qc_records": qc_count,
                        "dispatch_logs": dl_count, "fetch_log": 1}
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"}), 500


@data_fetch_bp.route('/api/task-batches/clear', methods=['DELETE'])
def api_clear_all_batches():
    """清空所有历史批次及关联数据"""
    running_task = FetchLog.query.filter_by(status='running').first()
    if running_task:
        return jsonify({"success": False, "message": "有任务正在执行中，无法清空"}), 400

    try:
        from models import Annotation
        ann_count = db.session.query(Annotation).delete(synchronize_session='fetch')
        ds_count = db.session.query(DailyStats).delete(synchronize_session='fetch')
        rd_count = db.session.query(RawData).delete(synchronize_session='fetch')
        fl_count = db.session.query(FetchLog).delete(synchronize_session='fetch')
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "所有历史记录已清空",
            "deleted": {"raw_data": rd_count, "daily_stats": ds_count,
                        "annotations": ann_count, "fetch_log": fl_count}
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"清空失败: {str(e)}"}), 500


# ========== 文件上传接口 ==========
@data_fetch_bp.route('/api/upload/for-review', methods=['POST'])
def api_upload_for_review():
    """文件上传机审接口"""
    if pd is None:
        return jsonify({"success": False, "message": "请安装 pandas 库: pip install pandas openpyxl"}), 500
    
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "请选择文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名为空"}), 400
    
    allowed_exts = ['.xlsx', '.xls']
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_exts:
        return jsonify({"success": False, "message": f"仅支持 Excel 文件: {', '.join(allowed_exts)}"}), 400
    
    instance_rule_mapping = get_instance_rule_mapping()
    
    try:
        temp_path = os.path.join('/tmp', file.filename)
        file.save(temp_path)
        df = pd.read_excel(temp_path)
        os.remove(temp_path)
    except Exception as e:
        return jsonify({"success": False, "message": f"读取文件失败: {str(e)}"}), 500
    
    if df.empty:
        return jsonify({"success": False, "message": "文件为空"}), 400
    
    column_mapping = {
        '商品id': 'product_id', '商品ID': 'product_id',
        '商品名称': 'product_name',
        '类目': 'category',
        'AI审核结果': 'ai_result',
        '审核单结果': 'audit_result',
        '审核id': 'audit_id',
        'AI审核id': 'ai_audit_id',
        '店铺名称': 'shop_name',
        '供应商id': 'supplier_id',
        '标签': 'label',
        '人审拒绝项': 'human_reject_item',
        '拒绝原因': 'reject_reason',
        'AI拒绝原因': 'ai_reject_reason',
        'AI拒绝解释': 'ai_explain',
        '人审意见': 'human_comment',
        '主图': 'main_image',
        '详情图': 'detail_image',
        'sku图': 'sku_image',
        'spu图': 'spu_image',
        '商品链接': 'product_link',
        '实例编码': 'instance_code',
        '创建日期': 'created_date',
        '变更类别': 'change_category'
    }
    
    df.columns = [column_mapping.get(col, col) for col in df.columns]
    batch_id = "UPLOAD-" + datetime.now().strftime('%Y%m%d%H%M%S')
    total_rows = len(df)
    compliant_count = 0
    violation_count = 0
    
    for _, row in df.iterrows():
        product_id = str(row.get('product_id', ''))
        if not product_id or product_id == 'nan':
            continue
        
        ai_result = str(row.get('ai_result', ''))
        if ai_result in ['合规', '1', 'PASS']:
            compliant_count += 1
        elif ai_result in ['违规', '0', 'REJECT']:
            violation_count += 1
        
        raw = RawData(
            supplier_id=str(row.get('supplier_id', '')),
            label=str(row.get('label', '')),
            ai_audit_id=str(row.get('ai_audit_id', '')),
            audit_id=str(row.get('audit_id', '')),
            product_id=product_id,
            ai_result=ai_result,
            audit_result=str(row.get('audit_result', '')),
            human_reject_item=str(row.get('human_reject_item', '')),
            reject_reason=str(row.get('reject_reason', '')),
            human_comment=str(row.get('human_comment', '')),
            ai_reject_reason=str(row.get('ai_reject_reason', '')),
            ai_explain=str(row.get('ai_explain', '')),
            shop_name=str(row.get('shop_name', '')),
            product_name=str(row.get('product_name', '')),
            category=str(row.get('category', '')),
            main_image=str(row.get('main_image', '')),
            detail_image=str(row.get('detail_image', '')),
            sku_image=str(row.get('sku_image', '')),
            spu_image=str(row.get('spu_image', '')),
            product_link=str(row.get('product_link', '')),
            instance_code=str(row.get('instance_code', '')),
            created_date=str(row.get('created_date', '')),
            change_category=str(row.get('change_category', '')),
            fetch_batch_id=batch_id,
            source='upload'
        )
        db.session.add(raw)
    
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"保存数据失败: {str(e)}"}), 500
    
    upload_date = datetime.now().strftime('%Y%m%d')
    log = FetchLog(
        batch_id=batch_id,
        env='云环境',
        instances='',
        sample_percent=100,
        total_fetched=total_rows,
        original_total=total_rows,
        compliant_count=compliant_count,
        non_compliant_count=violation_count,
        status='completed',
        source='upload',
        data_start_date=upload_date,
        data_end_date=upload_date
    )
    db.session.add(log)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "total": total_rows,
        "compliant_count": compliant_count,
        "non_compliant_count": violation_count
    })


# ========== 导入模板下载接口 ==========
@data_fetch_bp.route('/api/upload/template', methods=['GET'])
def api_download_template():
    """下载导入模板 Excel 文件"""
    if pd is None:
        return jsonify({"success": False, "message": "请安装 pandas 库: pip install pandas openpyxl"}), 500
    
    try:
        columns = [
            '商品id', '商品名称', '类目', 'AI审核结果', 'AI拒绝原因', 
            'AI拒绝解释', '审核单结果', '人审拒绝项', '拒绝原因', 
            '人审意见', '店铺名称', '主图', '详情图', 'sku图', 
            'spu图', '商品链接', '实例编码', '创建日期'
        ]
        
        df = pd.DataFrame(columns=columns)
        
        from io import BytesIO
        output = BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='导入模板')
        
        output.seek(0)
        
        from flask import send_file
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='template.xlsx'
        )
    
    except Exception as e:
        return jsonify({"success": False, "message": f"生成模板失败: {str(e)}"}), 500


# ========== 批次数据下载接口 ==========

# CSV字段与RawData模型的映射关系（完整28个字段）
RAW_DATA_FIELD_MAPPING = {
    'supplier_id': '供应商id',
    'label': '标签',
    'ai_audit_id': 'AI审核id',
    'audit_id': '审核id',
    'product_id': '商品id',
    'ai_result': 'AI审核结果',
    'audit_result': '审核单结果',
    'human_reject_item': '人审拒绝项',
    'reject_reason': '拒绝原因',
    'human_comment': '人审意见',
    'ai_reject_reason': 'AI拒绝原因',
    'ai_explain': 'AI拒绝解释',
    'shop_name': '店铺名称',
    'product_name': '商品名称',
    'category': '类目',
    'main_image': '主图',
    'detail_image': '详情图',
    'sku_image': 'sku图',
    'spu_image': 'spu图',
    'product_link': '商品链接',
    'check_result': '标注结果',
    'annotation': '备注',
    'instance_code': '实例编码',
    'created_date': '创建日期',
    'annotator': '标注人',
    'random_num': '随机数',
    'change_category': '变更类别',
    'gmt_created': '创建时间'
}

# CSV中文表头（与iData查询结果一致）
CSV_HEADERS_CN = [
    '供应商id', '标签', 'AI审核id', '审核id', '商品id', 'AI审核结果',
    '审核单结果', '人审拒绝项', '拒绝原因', '人审意见', 'AI拒绝原因',
    'AI拒绝解释', '店铺名称', '商品名称', '类目', '主图', '详情图',
    'sku图', 'spu图', '商品链接', '标注结果', '备注', '实例编码',
    '创建日期', '标注人', '随机数', '变更类别', '创建时间'
]

# iData返回的字段名（与CSV表头对应）
IDATA_FIELD_NAMES = [
    '供应商id', '标签', 'AI审核id', '审核id', '商品id', 'AI审核结果',
    '审核单结果', '人审拒绝项', '拒绝原因', '人审意见', 'AI拒绝原因',
    'AI拒绝解释', '店铺名称', '商品名称', '类目', '主图', '详情图',
    'sku图', 'spu图', '商品链接', '标注结果：1=正确  0=错误', '备注',
    '实例编码', '创建日期', '标注人', '随机数', '变更类别', '创建时间'
]

# iData字段名到RawData字段名的映射
IDATA_TO_RAWDATA_MAPPING = {
    '标注结果：1=正确  0=错误': 'check_result',
}


def _row_to_csv_row(row_dict, field_names):
    """将一行数据转换为CSV格式字符串，处理特殊字符"""
    values = []
    for field in field_names:
        val = row_dict.get(field, '')
        # 转义CSV特殊字符
        if val is None:
            val = ''
        val_str = str(val)
        # 如果包含逗号、引号或换行，需要加引号
        if ',' in val_str or '"' in val_str or '\n' in val_str or '\r' in val_str:
            val_str = '"' + val_str.replace('"', '""') + '"'
        values.append(val_str)
    return ','.join(values)


def _generate_csv_stream(data_list, field_names, headers):
    """生成CSV流，带BOM（兼容Excel中文）"""
    from io import StringIO
    output = StringIO()
    # UTF-8 BOM for Excel compatibility
    output.write('\ufeff')
    # 写入表头
    output.write(','.join(headers) + '\n')
    # 写入数据行
    for row in data_list:
        output.write(_row_to_csv_row(row, field_names) + '\n')
    return output.getvalue()


@data_fetch_bp.route('/api/data-fetch/download-raw/<batch_id>', methods=['GET'])
def api_download_raw(batch_id):
    """
    下载原始数据 - 从iData拉取抽样前的原始全量数据
    包含所有28个字段，不经过本地数据库过滤
    """
    try:
        # 1. 获取批次信息
        log = FetchLog.query.filter_by(batch_id=batch_id).first()
        if not log:
            return jsonify({"success": False, "message": f"批次 {batch_id} 不存在"}), 404

        # 获取该批次的实例列表
        instances = [i.strip() for i in log.instances.split(',') if i.strip()]
        env = log.env
        start_date = log.data_start_date
        end_date = log.data_end_date

        if not start_date or not end_date:
            return jsonify({"success": False, "message": "批次缺少日期范围信息"}), 400

        # 2. 从iData获取原始全量数据（100%抽样）
        all_raw_data = []
        from services.fetch_service import fetch_data_from_idata

        for instance in instances:
            print(f"[下载原始数据] 从iData获取 {instance} 原始数据...")
            result = fetch_data_from_idata(env, instance, start_date, end_date, sample_percent=100)
            fetched_data = result.get('fetched_data', [])
            all_raw_data.extend(fetched_data)
            print(f"[下载原始数据] {instance} 获取 {len(fetched_data)} 条")

        if not all_raw_data:
            return jsonify({"success": False, "message": "未从iData获取到任何数据"}), 404

        # 3. 生成CSV
        csv_content = _generate_csv_stream(all_raw_data, IDATA_FIELD_NAMES, CSV_HEADERS_CN)

        # 4. 返回文件
        from flask import Response
        filename = f"{batch_id}_原始数据.csv"
        return Response(
            csv_content,
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename*=UTF-8\'\'{filename}',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"}), 500


@data_fetch_bp.route('/api/data-fetch/download-reviewed/<batch_id>', methods=['GET'])
def api_download_reviewed(batch_id):
    """
    下载互检后数据 - 从本地数据库读取经过模型B互检的数据
    包含模型A结果、模型B结果、互检差异标记等字段
    """
    try:
        # 1. 验证批次存在
        log = FetchLog.query.filter_by(batch_id=batch_id).first()
        if not log:
            return jsonify({"success": False, "message": f"批次 {batch_id} 不存在"}), 404

        # 检查是否已完成互检
        if log.review_status != 'completed':
            return jsonify({"success": False, "message": f"批次 {batch_id} 尚未完成互检"}), 400

        # 2. 从本地数据库读取该批次的数据
        raw_records = RawData.query.filter_by(fetch_batch_id=batch_id).all()

        if not raw_records:
            return jsonify({"success": False, "message": "该批次没有数据记录"}), 404

        # 3. 构建导出数据
        export_data = []
        for rec in raw_records:
            # 计算差异类型
            model_a = rec.ai_result or ''
            model_b = rec.modelb_result or ''
            if model_a and model_b:
                if model_a in ['合规', '1', 'PASS'] and model_b in ['违规', '0', 'REJECT']:
                    diff_type = 'A合规B违规'
                elif model_a in ['违规', '0', 'REJECT'] and model_b in ['合规', '1', 'PASS']:
                    diff_type = 'A违规B合规'
                elif model_a in ['合规', '1', 'PASS'] and model_b in ['合规', '1', 'PASS']:
                    diff_type = 'A合规B合规'
                elif model_a in ['违规', '0', 'REJECT'] and model_b in ['违规', '0', 'REJECT']:
                    diff_type = 'A违规B违规'
                else:
                    diff_type = '其他'
            else:
                diff_type = 'B未审核'

            row = {
                '商品id': rec.product_id or '',
                '商品名称': rec.product_name or '',
                '类目': rec.category or '',
                '实例编码': rec.instance_code or '',
                '创建日期': rec.created_date or '',
                '模型A审核结果': rec.ai_result or '',
                '模型A拒绝原因': rec.ai_reject_reason or '',
                '模型B审核结果': rec.modelb_result or '',
                '模型B拒绝原因': rec.modelb_reason or '',
                '模型B详细说明': rec.modelb_detail or '',
                '互检差异类型': diff_type,
                'AI审核结果': rec.ai_result or '',
                '审核单结果': rec.audit_result or '',
                '人审拒绝项': rec.human_reject_item or '',
                '拒绝原因': rec.reject_reason or '',
                '人审意见': rec.human_comment or '',
                'AI拒绝原因': rec.ai_reject_reason or '',
                'AI拒绝解释': rec.ai_explain or '',
                '店铺名称': rec.shop_name or '',
                '主图': rec.main_image or '',
                '详情图': rec.detail_image or '',
                'sku图': rec.sku_image or '',
                'spu图': rec.spu_image or '',
                '商品链接': rec.product_link or '',
                '标注结果': rec.check_result or '',
                '备注': rec.annotation or '',
                '标注人': rec.annotator or '',
                '变更类别': rec.change_category or ''
            }
            export_data.append(row)

        # 互检数据专用表头
        review_headers = [
            '商品id', '商品名称', '类目', '实例编码', '创建日期',
            '模型A审核结果', '模型A拒绝原因', '模型B审核结果', '模型B拒绝原因',
            '模型B详细说明', '互检差异类型',
            'AI审核结果', '审核单结果', '人审拒绝项', '拒绝原因', '人审意见',
            'AI拒绝原因', 'AI拒绝解释', '店铺名称', '主图', '详情图', 'sku图',
            'spu图', '商品链接', '标注结果', '备注', '标注人', '变更类别'
        ]

        review_field_names = list(review_headers)

        # 4. 生成CSV
        csv_content = _generate_csv_stream(export_data, review_field_names, review_headers)

        # 5. 返回文件
        from flask import Response
        filename = f"{batch_id}_互检数据.csv"
        return Response(
            csv_content,
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename*=UTF-8\'\'{filename}',
                'Content-Type': 'text/csv; charset=utf-8'
            }
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"}), 500


@data_fetch_bp.route('/api/data-fetch/download/<batch_id>', methods=['GET'])
def api_download(batch_id):
    """
    下载批次数据 - 支持三种导出类型，流式写入避免内存溢出
    
    type参数：
    - raw: 原始全量数据（所有记录，28字段）
    - violation: 仅违规数据（ai_result='违规'，28字段）
    - review: 互检差异数据（模型A≠B，包含模型B结果，28+字段）
    """
    try:
        # 1. 验证批次存在
        log = FetchLog.query.filter_by(batch_id=batch_id).first()
        if not log:
            return jsonify({"success": False, "message": f"批次 {batch_id} 不存在"}), 404

        # 2. 获取导出类型参数
        export_type = request.args.get('type', 'raw')
        
        # 3. 根据类型构建查询
        query = RawData.query.filter_by(fetch_batch_id=batch_id)
        
        if export_type == 'violation':
            # 仅违规数据
            query = query.filter(RawData.ai_result.in_(['违规', '0', 'REJECT']))
            filename = f"{batch_id}_violation.csv"
        elif export_type == 'review':
            # 互检差异数据：模型A与B结果不一致
            query = query.filter(
                RawData.modelb_result.isnot(None),
                RawData.modelb_result != ''
            )
            filename = f"{batch_id}_review.csv"
        else:
            # 原始全量数据
            filename = f"{batch_id}_raw.csv"

        # 4. 流式生成CSV（分批查询，避免内存溢出）
        def generate_csv_stream():
            """流式生成CSV，分批查询数据库"""
            from io import StringIO
            
            # 写入UTF-8 BOM
            yield '\ufeff'
            
            # 根据类型确定字段和表头
            if export_type == 'review':
                headers = [
                    '商品id', '商品名称', '类目', '实例编码', '创建日期',
                    'AI审核结果', 'AI拒绝原因', 
                    '模型B审核结果', '模型B拒绝原因', '模型B详细说明', '互检差异类型',
                    '供应商id', '标签', 'AI审核id', '审核id',
                    '审核单结果', '人审拒绝项', '拒绝原因', '人审意见',
                    'AI拒绝解释', '店铺名称', '主图', '详情图', 'sku图', 'spu图',
                    '商品链接', '标注结果', '备注', '标注人', '变更类别', '创建时间'
                ]
            else:
                headers = CSV_HEADERS_CN
            
            # 写入表头
            yield ','.join(headers) + '\n'
            
            # 分批查询（每批500条）
            batch_size = 500
            offset = 0
            
            while True:
                batch_query = query.order_by(RawData.id).offset(offset).limit(batch_size)
                records = batch_query.all()
                
                if not records:
                    break
                
                for rec in records:
                    row_data = _build_export_row(rec, export_type)
                    yield _row_to_csv_row(row_data, headers) + '\n'
                
                offset += batch_size
                
                # 如果这批数据少于batch_size，说明已经查完
                if len(records) < batch_size:
                    break

        # 5. 返回流式响应（用 stream_with_context 保持请求上下文，确保生成器可访问 app context）
        from flask import Response, stream_with_context
        return Response(
            stream_with_context(generate_csv_stream()),
            mimetype='text/csv; charset=utf-8',
            headers={
                'Content-Disposition': f'attachment; filename="{filename}"',
                'Content-Type': 'text/csv; charset=utf-8',
                'X-Export-Type': export_type,
                'Cache-Control': 'no-cache'
            }
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"success": False, "message": f"下载失败: {str(e)}"}), 500


def _build_export_row(rec, export_type='raw'):
    """构建导出行数据"""
    if export_type == 'review':
        # 互检差异数据：包含模型B结果和差异类型
        model_a = rec.ai_result or ''
        model_b = rec.modelb_result or ''
        
        # 计算差异类型
        if model_a and model_b:
            if model_a in ['合规', '1', 'PASS'] and model_b in ['违规', '0', 'REJECT']:
                diff_type = 'A合规B违规'
            elif model_a in ['违规', '0', 'REJECT'] and model_b in ['合规', '1', 'PASS']:
                diff_type = 'A违规B合规'
            elif model_a in ['合规', '1', 'PASS'] and model_b in ['合规', '1', 'PASS']:
                diff_type = 'A合规B合规'
            elif model_a in ['违规', '0', 'REJECT'] and model_b in ['违规', '0', 'REJECT']:
                diff_type = 'A违规B违规'
            else:
                diff_type = '其他'
        else:
            diff_type = 'B未审核'
        
        return {
            '商品id': rec.product_id or '',
            '商品名称': rec.product_name or '',
            '类目': rec.category or '',
            '实例编码': rec.instance_code or '',
            '创建日期': rec.created_date or '',
            'AI审核结果': rec.ai_result or '',
            'AI拒绝原因': rec.ai_reject_reason or '',
            '模型B审核结果': rec.modelb_result or '',
            '模型B拒绝原因': rec.modelb_reason or '',
            '模型B详细说明': rec.modelb_detail or '',
            '互检差异类型': diff_type,
            '供应商id': rec.supplier_id or '',
            '标签': rec.label or '',
            'AI审核id': rec.ai_audit_id or '',
            '审核id': rec.audit_id or '',
            '审核单结果': rec.audit_result or '',
            '人审拒绝项': rec.human_reject_item or '',
            '拒绝原因': rec.reject_reason or '',
            '人审意见': rec.human_comment or '',
            'AI拒绝解释': rec.ai_explain or '',
            '店铺名称': rec.shop_name or '',
            '主图': rec.main_image or '',
            '详情图': rec.detail_image or '',
            'sku图': rec.sku_image or '',
            'spu图': rec.spu_image or '',
            '商品链接': rec.product_link or '',
            '标注结果': rec.check_result or '',
            '备注': rec.annotation or '',
            '标注人': rec.annotator or '',
            '变更类别': rec.change_category or '',
            '创建时间': rec.gmt_created or ''
        }
    else:
        # 原始全量/违规数据：28个字段
        return {
            '供应商id': rec.supplier_id or '',
            '标签': rec.label or '',
            'AI审核id': rec.ai_audit_id or '',
            '审核id': rec.audit_id or '',
            '商品id': rec.product_id or '',
            'AI审核结果': rec.ai_result or '',
            '审核单结果': rec.audit_result or '',
            '人审拒绝项': rec.human_reject_item or '',
            '拒绝原因': rec.reject_reason or '',
            '人审意见': rec.human_comment or '',
            'AI拒绝原因': rec.ai_reject_reason or '',
            'AI拒绝解释': rec.ai_explain or '',
            '店铺名称': rec.shop_name or '',
            '商品名称': rec.product_name or '',
            '类目': rec.category or '',
            '主图': rec.main_image or '',
            '详情图': rec.detail_image or '',
            'sku图': rec.sku_image or '',
            'spu图': rec.spu_image or '',
            '商品链接': rec.product_link or '',
            '标注结果：1=正确  0=错误': rec.check_result or '',
            '备注': rec.annotation or '',
            '实例编码': rec.instance_code or '',
            '创建日期': rec.created_date or '',
            '标注人': rec.annotator or '',
            '随机数': str(rec.random_num) if rec.random_num else '',
            '变更类别': rec.change_category or '',
            '创建时间': rec.gmt_created or ''
        }


def _row_to_csv_row(row_dict, field_names):
    """将一行数据转换为CSV格式字符串"""
    values = []
    for field in field_names:
        val = row_dict.get(field, '')
        if val is None:
            val = ''
        val_str = str(val)
        # CSV转义：逗号、引号、换行符需要处理
        if ',' in val_str or '"' in val_str or '\n' in val_str or '\r' in val_str:
            val_str = '"' + val_str.replace('"', '""') + '"'
        values.append(val_str)
    return ','.join(values)
