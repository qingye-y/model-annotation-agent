# -*- coding: utf-8 -*-
"""
任务调度模块蓝图（v1.4）

API 列表：
  POST /api/dispatch/generate-tasks  生成标注任务（v1.4 新增）
  GET  /api/dispatch/task-pool       获取待分配任务池（按规则聚合）
  POST /api/dispatch/assign          执行分配
  GET  /api/dispatch/history         分配历史
  DELETE /api/dispatch/revoke        清空分配
  GET  /api/annotation/my-tasks      标注员获取自己的任务列表
  PUT  /api/annotation/submit        标注员提交标注结果
  GET  /api/dispatch/annotator-load  标注员负载概览
  GET  /api/qc/notifications         管理员修正通知
  GET  /api/config/labels            获取标注错误原因标签列表
  GET  /api/badcase/list             Badcase列表（标注员只读）
"""

import random
from datetime import datetime, date
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from models import db, User, RawData, Annotation, FetchLog, DispatchLog, SqlConfig, QcRecord

dispatch_bp = Blueprint('dispatch', __name__)


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def get_config(key, default=None):
    """读取 SqlConfig"""
    row = SqlConfig.query.filter_by(key=key).first()
    return row.value if row else default


def rule_display_name(reason_tag):
    """将 computed_error_reason 映射为展示名"""
    if not reason_tag:
        return '其他违规'
    # 简单映射，可后续扩展
    return reason_tag.strip()


def today_str():
    return date.today().isoformat()


# ---------------------------------------------------------------------------
# API-1: GET /api/dispatch/task-pool
# 获取待分配任务池，按规则聚合
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/task-pool', methods=['GET'])
@login_required
def api_task_pool():
    # 加载实例→提示词规则映射
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()

    # 待分配记录：已互检 + 未标注 + 未分配
    base_filter = [
        RawData.modelb_reviewed == True,
        RawData.annotator == '',
        RawData.check_result == '',
    ]

    # 获取所有待分配记录
    all_records = RawData.query.filter(*base_filter).all()
    if not all_records:
        return jsonify({'success': True, 'rules': []})

    # 按提示词规则 + 数据类别分组
    # 结构: { rule_name: { '不一致数据': {total, assigned, instances, batch_ids},
    #                       '一致性抽检': {total, assigned, instances, batch_ids} } }
    rule_groups = {}
    for r in all_records:
        # 通过 instance_code 找提示词规则
        rule_name = instance_rule_map.get(r.instance_code, '') if r.instance_code else ''
        if not rule_name:
            rule_name = '未关联规则' + ('/' + r.instance_code if r.instance_code else '')

        data_type = '不一致数据' if r.modelb_consistent == False else '一致性抽检'

        if rule_name not in rule_groups:
            rule_groups[rule_name] = {
                '不一致数据': {'total': 0, 'assigned': 0, 'instances': set(), 'batch_ids': set()},
                '一致性抽检': {'total': 0, 'assigned': 0, 'instances': set(), 'batch_ids': set()},
            }

        g = rule_groups[rule_name][data_type]
        g['total'] += 1
        g['instances'].add(r.instance_code or '')
        g['batch_ids'].add(r.fetch_batch_id or '')

    # 计算已分配量（给标注员但未标注完成的）
    assigned_rows = RawData.query.filter(
        RawData.modelb_reviewed == True,
        RawData.annotator != '',
        RawData.check_result == '',
    ).all()

    for r in assigned_rows:
        rule_name = instance_rule_map.get(r.instance_code, '') if r.instance_code else ''
        if not rule_name:
            rule_name = '未关联规则' + ('/' + r.instance_code if r.instance_code else '')
        data_type = '不一致数据' if r.modelb_consistent == False else '一致性抽检'
        if rule_name in rule_groups and data_type in rule_groups[rule_name]:
            rule_groups[rule_name][data_type]['assigned'] += 1

    # 构建输出 — 一个规则一张卡，合并不一致+抽检
    rules_output = []
    for rule_name, types in rule_groups.items():
        inc = types['不一致数据']
        con = types['一致性抽检']
        total = inc['total'] + con['total']
        assigned = inc['assigned'] + con['assigned']
        remaining = total - assigned
        instances = sorted(set(list(inc['instances']) + list(con['instances'])))
        batch_ids = list(inc['batch_ids'] | con['batch_ids'])
        date_range = _get_date_range(batch_ids)
        if total == 0:
            continue
        rules_output.append({
            'rule_name': rule_name,
            'total': total,
            'assigned': assigned,
            'remaining': remaining,
            'instances': [x for x in instances if x],
            'date_range': date_range,
        })

    rules_output.sort(key=lambda x: x['rule_name'])

    return jsonify({
        'success': True,
        'rules': rules_output,
    })


def _get_date_range(batch_ids):
    """从 FetchLog 批次获取日期范围，格式 YYYY/MM/DD"""
    if not batch_ids:
        return ''
    logs = FetchLog.query.filter(FetchLog.batch_id.in_(batch_ids)).all()
    starts = [log.data_start_date for log in logs if log.data_start_date]
    ends = [log.data_end_date for log in logs if log.data_end_date]
    if not starts or not ends:
        return ''
    start = min(starts)
    end = max(ends)
    if start == end:
        return f"{_fmt_date(start)}"
    return f"{_fmt_date(start)} ~ {_fmt_date(end)}"


def _fmt_date(s):
    """YYYYMMDD → YYYY/MM/DD"""
    if not s or len(s) != 8:
        return s or ''
    return f"{s[:4]}/{s[4:6]}/{s[6:8]}"


# ---------------------------------------------------------------------------
# API-2: POST /api/dispatch/assign
# 执行分配
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/assign', methods=['POST'])
@login_required
def api_assign():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}
    rule_name = data.get('rule_name', '').strip()
    annotator_ids = data.get('annotator_ids', [])
    assign_method = data.get('assign_method', '平均分配')
    assign_count = int(data.get('assign_count', 0))

    if not rule_name:
        return jsonify({'success': False, 'error': '规则名不能为空'}), 400
    if not annotator_ids or assign_count <= 0:
        return jsonify({'success': False, 'error': '参数错误'}), 400

    data_type = data.get('data_type', 'audit')

    # 加载实例→规则映射
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()
    # 找到该规则名对应的所有实例
    matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_name]

    # 确认标注员存在且启用（按 username 匹配，前端传的是 username 字符串）
    annotators = User.query.filter(
        User.username.in_(annotator_ids),
        User.role == 'annotator',
        User.is_active == True
    ).all()
    if len(annotators) != len(annotator_ids):
        return jsonify({'success': False, 'error': '部分标注员不存在或已停用'}), 400

    # 取该规则下所有未分配记录
    base_filter = [
        RawData.modelb_reviewed == True,
        RawData.annotator == '',
        RawData.check_result == '',
    ]
    if matched_instances:
        from sqlalchemy import or_
        raw_filter = base_filter + [or_(*[RawData.instance_code == inst for inst in matched_instances])]
    else:
        raw_filter = base_filter

    available = RawData.query.filter(*raw_filter).order_by(RawData.id).all()
    if len(available) < assign_count:
        return jsonify({
            'success': False,
            'error': f'可分配数量不足，当前剩余 {len(available)} 条，请求分配 {assign_count} 条'
        }), 400

    # 计算每人分配数量
    n = len(annotators)
    if assign_method == '平均分配':
        shares = [assign_count // n] * n
        for i in range(assign_count % n):
            shares[i] += 1
    else:  # 按额度比例
        quotas = [(a.id, max(0, (a.daily_quota or 200) - _today_assigned(a.id))) for a in annotators]
        total_quota = sum(q[1] for q in quotas)
        if total_quota == 0:
            return jsonify({'success': False, 'error': '所有标注员今日额度已用尽'}), 400
        shares = []
        for aid, quota in quotas:
            cnt = min(round(assign_count * quota / total_quota), quota)
            shares.append(cnt)
        # 补足差额给第一人
        actual = sum(shares)
        shares[0] += assign_count - actual

    # 执行分配
    idx = 0
    actual_assigned = 0  # 实际分配数量（可能小于 assign_count）
    for annotator, share in zip(annotators, shares):
        if share <= 0:
            continue
        records_to_assign = available[idx:idx + share]
        idx += share

        for rec in records_to_assign:
            rec.annotator = annotator.username

        actual_assigned += len(records_to_assign)

        # 写分配记录（以实际数量为准）
        log = DispatchLog(
            admin_id=current_user.id,
            rule_name=rule_name,
            annotator_id=annotator.id,
            count=len(records_to_assign),
            assign_method=assign_method,
            data_type=data_type,
            batch_id=records_to_assign[0].fetch_batch_id if records_to_assign else None,
        )
        db.session.add(log)

    db.session.commit()

    # 返回实际分配数量；若与请求不符，给出提示
    if actual_assigned < assign_count:
        return jsonify({
            'success': True,
            'message': f'成功分配 {actual_assigned} 条（请求 {assign_count} 条，部分实例可用数量不足）'
        })
    return jsonify({'success': True, 'message': f'成功分配 {actual_assigned} 条任务'})


def _today_assigned(annotator_id):
    """今日已分配给该标注员的数量"""
    today = datetime.combine(date.today(), datetime.min.time())
    return db.session.query(db.func.count(RawData.id)).filter(
        RawData.annotator == User.query.get(annotator_id).username,
        RawData.annotator != '',
        RawData.check_result == '',
        RawData.created_at >= today,
    ).scalar() or 0


# ---------------------------------------------------------------------------
# API-3: GET /api/dispatch/history
# 分配历史
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/history', methods=['GET'])
@login_required
def api_history():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    query = DispatchLog.query.order_by(DispatchLog.created_at.desc())
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()

    items = [{
        'id': log.id,
        'created_at': log.created_at.strftime('%Y-%m-%d %H:%M:%S') if log.created_at else '',
        'admin_name': log.admin.name or log.admin.username if log.admin else '',
        'rule_name': log.rule_name,
        'annotator_name': log.annotator.name or log.annotator.username if log.annotator else '',
        'count': log.count,
        'assign_method': log.assign_method or '',
        'data_type': log.data_type or '',
    } for log in logs]

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': items,
    })


# ---------------------------------------------------------------------------
# API-4: DELETE /api/dispatch/revoke
# 清空分配（撤回已分配但未标注的任务）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/revoke', methods=['DELETE'])
@login_required
def api_revoke():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}
    rule_name = data.get('rule_name')
    annotator_id = data.get('annotator_id')

    if not rule_name:
        return jsonify({'success': False, 'error': '规则名不能为空'}), 400

    # 加载实例→规则映射
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()
    matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_name]

    base = [
        RawData.annotator != '',
        RawData.check_result == '',
    ]
    if matched_instances:
        from sqlalchemy import or_
        base.append(or_(*[RawData.instance_code == inst for inst in matched_instances]))

    if annotator_id:
        user = User.query.get(int(annotator_id))
        if user:
            base.append(RawData.annotator == user.username)

    records = RawData.query.filter(*base).all()
    count = len(records)
    for rec in records:
        rec.annotator = ''

    db.session.commit()
    return jsonify({'success': True, 'revoked_count': count, 'message': f'已撤回 {count} 条分配'})


# ---------------------------------------------------------------------------
# API-5: GET /api/annotation/my-tasks
# 标注员获取自己的任务列表（不含 source_type）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/annotation/my-tasks', methods=['GET'])
@login_required
def api_my_tasks():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    # 管理员查看所有时不过滤 annotator
    if current_user.role == 'admin':
        annotator_filter = []
    else:
        annotator_filter = [RawData.annotator == current_user.username]

    rule_filter = request.args.get('rule_name', '')
    status_filter = request.args.get('status', '')  # pending / done
    instance_filter = request.args.get('instance', '')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    base = [
        RawData.modelb_reviewed == True,
        RawData.annotator != '',
    ] + annotator_filter

    if rule_filter:
        # 通过规则名找对应实例
        from services.fetch_service import get_instance_rule_mapping
        instance_rule_map = get_instance_rule_mapping()
        matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_filter]
        if matched_instances:
            from sqlalchemy import or_
            base.append(or_(*[RawData.instance_code == inst for inst in matched_instances]))

    if instance_filter:
        base.append(RawData.instance_code == instance_filter)

    if status_filter == 'pending':
        base.append(RawData.check_result == '')
    elif status_filter == 'done':
        base.append(RawData.check_result != '')

    query = RawData.query.filter(*base).order_by(RawData.id.desc())
    total = query.count()
    records = query.offset((page - 1) * per_page).limit(per_page).all()

    def truncate(val, max_len=100):
        if not val:
            return ''
        s = str(val)
        return s[:max_len] + '...' if len(s) > max_len else s

    items = []
    for r in records:
        items.append({
            'id': r.id,
            'product_id': r.product_id,
            'product_name': r.product_name,
            'category': r.category,
            'instance_code': r.instance_code,
            'ai_result': r.ai_result or '合规',
            'ai_reject_reason': truncate(r.ai_reject_reason),
            'modelb_result': r.modelb_result or '',
            'modelb_reason': truncate(r.modelb_reason),
            'rule_name': r.computed_error_reason or ('合规抽检' if r.modelb_consistent else '其他违规'),
            'data_date': str(r.created_date or '')[:10].replace('-','/'),
            'check_result': r.check_result or '',
            'main_image': truncate(r.main_image),
            'detail_image': truncate(r.detail_image),
            'sku_image': truncate(r.sku_image),
            'annotation': r.annotation or '',
            'annotator': r.annotator or '',
            # ⚠️ 不返回 source_type，防止标注员预判
        })

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': items,
    })


# ---------------------------------------------------------------------------
# API-6: PUT /api/annotation/submit
# 标注员提交标注结果
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/annotation/submit', methods=['PUT'])
@login_required
def api_submit():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}
    raw_data_id = int(data.get('raw_data_id', 0))
    result = data.get('result', '').strip()  # correct / error / ignore
    error_tag = data.get('error_tag', '').strip()
    note = data.get('note', '').strip()

    if result not in ('correct', 'error', 'ignore'):
        return jsonify({'success': False, 'error': '标注结果必须是 correct/error/ignore'}), 400
    if result == 'error' and not error_tag:
        return jsonify({'success': False, 'error': '选择"错误"时必须选择错误标签'}), 400

    rec = RawData.query.get(raw_data_id)
    if not rec:
        return jsonify({'success': False, 'error': '记录不存在'}), 404

    # 权限校验：自己只能标自己的
    if current_user.role != 'admin' and rec.annotator != current_user.username:
        return jsonify({'success': False, 'error': '无权操作此任务'}), 403
    if rec.check_result:
        return jsonify({'success': False, 'error': '此任务已标注，无法重复提交'}), 400

    # 写入/更新 Annotation
    ann = Annotation.query.filter_by(raw_data_id=raw_data_id).first()
    if not ann:
        ann = Annotation(
            raw_data_id=raw_data_id,
            annotator_id=current_user.id,
        )
        db.session.add(ann)

    ann.result = result
    ann.error_tag = error_tag if result == 'error' else None
    ann.note = note
    ann.is_submitted = True

    # 同步 RawData
    rec.check_result = result
    rec.annotation = error_tag if result == 'error' else (result == 'correct' and '正确' or '忽略')
    rec.annotator = current_user.username

    db.session.commit()
    return jsonify({'success': True, 'message': '标注已提交', 'annotation_id': ann.id})


# ---------------------------------------------------------------------------
# API-7: GET /api/dispatch/annotator-load
# 标注员负载概览
# - 管理员：返回所有标注员的负载
# - 标注员：仅返回自己的负载
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/annotator-load', methods=['GET'])
@login_required
def api_annotator_load():
    today_start = datetime.combine(date.today(), datetime.min.time())

    # 标注员只能查自己；管理员查所有人
    if current_user.role == 'annotator':
        annotators = [current_user]
    elif current_user.role == 'admin':
        annotators = User.query.filter(
            User.role == 'annotator',
            User.is_active == True
        ).all()
    else:
        return jsonify({'success': False, 'error': '权限不足'}), 403

    items = []
    for a in annotators:
        today_assigned = RawData.query.filter(
            RawData.annotator == a.username,
            RawData.created_at >= today_start,
        ).count()

        today_completed = RawData.query.filter(
            RawData.annotator == a.username,
            RawData.check_result != '',
            RawData.created_at >= today_start,
        ).count()

        quota = a.daily_quota or 200
        remaining = max(0, quota - today_assigned)

        # 解析绑定规则
        bound = []
        if a.bound_rules:
            try:
                bound = __import__('json').loads(a.bound_rules)
            except Exception:
                bound = []
        if isinstance(bound, str):
            bound = [bound]

        items.append({
            'id': a.id,
            'name': a.name or a.username,
            'daily_quota': quota,
            'today_assigned': today_assigned,
            'today_completed': today_completed,
            'remaining': remaining,
            'bound_rules': bound,
        })

    return jsonify({'success': True, 'items': items})


# ---------------------------------------------------------------------------
# API-8: GET /api/qc/notifications
# 管理员修正通知：标注员查看被质检修正的通知列表
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/qc/notifications', methods=['GET'])
@login_required
def api_qc_notifications():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    # 标注员只看自己的；管理员看所有
    if current_user.role == 'annotator':
        base = [QcRecord.annotator_id == current_user.id]
    else:
        base = []

    query = QcRecord.query.filter(*base).order_by(QcRecord.created_at.desc())
    total = query.count()
    records = query.offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for rec in records:
        raw = RawData.query.get(rec.raw_data_id)
        annotator_user = User.query.get(rec.annotator_id)
        qc_user = User.query.get(rec.qc_user_id) if rec.qc_user_id else None
        items.append({
            'id': rec.id,
            'raw_data_id': rec.raw_data_id,
            'product_name': raw.product_name if raw else '',
            'product_id': raw.product_id if raw else '',
            'original_result': rec.original_result or '',
            'corrected_result': rec.corrected_result or '',
            'solution': rec.solution or '',
            'annotator_name': annotator_user.name or annotator_user.username if annotator_user else '',
            'qc_user_name': qc_user.name or qc_user.username if qc_user else '',
            'is_notified': rec.is_notified,
            'created_at': rec.created_at.isoformat() if rec.created_at else '',
            # 前端标记用
            'source_type': 'correction',
        })

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': items,
    })


# ---------------------------------------------------------------------------
# API-10: GET /api/config/labels
# 获取标注错误原因标签列表（供标注员前端下拉框使用）
# ---------------------------------------------------------------------------

# 默认标签列表（与 config.js DEFAULT_LABELS 保持一致）
ANNOTATION_DEFAULT_LABELS = [
    '特殊资质缺失', '水印', '马赛克', '盗图', '类目错放', '图文不一致',
    '销售属性错误', 'SKU图不一致', '关键属性不一致', '站外引流', '无关信息',
    '多主体', '商品清单', '品类词堆砌', '禁售商品', '标题无关词',
    'AI生成', '书籍版权页', '其他'
]


@dispatch_bp.route('/api/config/labels', methods=['GET'])
@login_required
def api_config_labels():
    """获取标注错误原因标签列表"""
    config = SqlConfig.query.filter_by(key='ANNOTATION_LABELS').first()
    if config and config.value:
        try:
            import json
            labels = json.loads(config.value)
            if isinstance(labels, list) and labels:
                return jsonify({'success': True, 'labels': labels})
        except Exception:
            pass
    # Fallback to defaults
    return jsonify({'success': True, 'labels': ANNOTATION_DEFAULT_LABELS})


# ---------------------------------------------------------------------------
# API-9: POST /api/dispatch/generate-tasks
# 生成标注任务（v1.4 新增）
# 接收 batch_id + rule_name + instance + sample_percent
# - 不一致数据（modelb_consistent=False）100% 进入任务池
# - 一致性数据（modelb_consistent=True）按比例随机抽取
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/generate-tasks', methods=['POST'])
@login_required
def api_generate_tasks():
    """生成标注任务（管理员手动触发）"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足，仅管理员可操作'}), 403

    data = request.get_json() or {}
    batch_id = str(data.get('batch_id', '')).strip()
    rule_name = str(data.get('rule_name', '')).strip()
    instance_code = str(data.get('instance', '')).strip() or None
    sample_percent = float(data.get('sample_percent', 5.0))

    if not batch_id:
        return jsonify({'success': False, 'error': 'batch_id 不能为空'}), 400
    if not rule_name:
        return jsonify({'success': False, 'error': '规则名不能为空'}), 400
    if sample_percent < 0 or sample_percent > 100:
        return jsonify({'success': False, 'error': '抽检比例需在 0~100 之间'}), 400

    # 确认批次存在
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({'success': False, 'error': '批次不存在'}), 404

    if fetch_log.review_status != 'completed':
        return jsonify({'success': False, 'error': '该批次互检尚未完成，无法生成标注任务'}), 400

    # 诊断日志（v2.0.1）
    import logging
    logger = logging.getLogger('werkzeug')
    logger.error(f'[GenerateTasks] batch_id={batch_id} review_status={fetch_log.review_status} sample_percent={sample_percent} instance={instance_code}')

    # 该批次 RawData 总记录数
    total_raw = RawData.query.filter(RawData.fetch_batch_id == batch_id).count()
    # 互检标记情况
    reviewed_raw = RawData.query.filter(RawData.fetch_batch_id == batch_id, RawData.modelb_reviewed == True).count()
    # 一致性标记情况
    consistent_raw = RawData.query.filter(RawData.fetch_batch_id == batch_id, RawData.modelb_consistent == True).count()
    inconsistent_raw = RawData.query.filter(RawData.fetch_batch_id == batch_id, RawData.modelb_consistent == False).count()
    neither_raw = RawData.query.filter(RawData.fetch_batch_id == batch_id, RawData.modelb_consistent == None).count()
    logger.error(f'[GenerateTasks] RawData总={total_raw} 已互检={reviewed_raw} 一致={consistent_raw} 不一致={inconsistent_raw} 未标记={neither_raw}')

    # 查询该批次的不一致数据（modelb_reviewed=True, modelb_consistent=False）
    inconsistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == False,
    ]
    if instance_code:
        inconsistent_filter.append(RawData.instance_code == instance_code)
    inconsistent_q = RawData.query.filter(*inconsistent_filter)
    inconsistent_count = inconsistent_q.count()

    # 查询该批次的一致性数据（modelb_reviewed=True, modelb_consistent=True）
    consistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == True,
    ]
    if instance_code:
        consistent_filter.append(RawData.instance_code == instance_code)

    if sample_percent > 0 and sample_percent < 100:
        # 按比例随机抽样：随机数 < sample_percent/100
        import random
        threshold = sample_percent / 100.0
        consistent_all = RawData.query.filter(*consistent_filter).all()
        sampled_records = [r for r in consistent_all if (r.random_num or random.random()) < threshold]
        sampled_count = len(sampled_records)
    elif sample_percent >= 100:
        sampled_count = RawData.query.filter(*consistent_filter).count()
    else:
        sampled_count = 0

    # data_mode 控制包含哪些数据（v2.0）
    data_mode = str(data.get('data_mode', 'both')).lower()
    if data_mode == 'inconsistent':
        # 仅不一致数据
        inconsistent_count = inconsistent_q.count()
        sampled_count = 0
    elif data_mode == 'consistent':
        # 仅一致性数据
        sampled_count = sampled_count  # 已有逻辑
        inconsistent_count = 0
    # 'both' 或其他：两者都包含（默认行为）

    logger.error(f'[GenerateTasks] 查询结果 inconsistent_count={inconsistent_count} sampled_count={sampled_count}')

    # 更新 FetchLog 标记已生成
    fetch_log.task_generated = True
    fetch_log.task_generate_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fetch_log.task_sample_percent = sample_percent

    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'生成成功',
        'inconsistent_count': inconsistent_count,
        'sampled_count': sampled_count,
        'total_entered': inconsistent_count + sampled_count,
        'task_generated': True,
        'task_generate_time': fetch_log.task_generate_time,
        'task_sample_percent': sample_percent,
        'data_mode': data_mode,
    })


# ---------------------------------------------------------------------------
# API-9: GET /api/badcase/list
# Badcase 列表（标注员只读版）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/badcase/list', methods=['GET'])
@login_required
def api_badcase_list():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))
    error_tag_filter = request.args.get('error_tag', '')
    date_filter = request.args.get('date', '')

    # Badcase: Annotation.result='error' 且经过人工确认的记录
    # 目前用 check_result='错误' 来标记（由质检中心管理员确认）
    base = [
        Annotation.is_submitted == True,
        Annotation.result == 'error',
    ]

    if error_tag_filter:
        base.append(Annotation.error_tag == error_tag_filter)

    query = Annotation.query.filter(*base).order_by(Annotation.updated_at.desc())
    total = query.count()
    records = query.offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for rec in records:
        raw = RawData.query.get(rec.raw_data_id)
        annotator_user = User.query.get(rec.annotator_id)
        items.append({
            'id': rec.id,
            'raw_data_id': rec.raw_data_id,
            'product_name': raw.product_name if raw else '',
            'product_id': raw.product_id if raw else '',
            'instance_code': raw.instance_code if raw else '',
            'error_tag': rec.error_tag or '',
            'original_result': rec.result or '',
            'note': rec.note or '',
            'annotator_name': annotator_user.name or annotator_user.username if annotator_user else '',
            'created_at': rec.created_at.isoformat() if rec.created_at else '',
            'updated_at': rec.updated_at.isoformat() if rec.updated_at else '',
        })

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': items,
    })
