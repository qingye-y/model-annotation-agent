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


def fmt_bj(dt, fmt='%Y-%m-%d %H:%M:%S'):
    """UTC datetime → 北京时间（UTC+8）格式化字符串"""
    if not dt:
        return ''
    from datetime import timedelta
    return (dt + timedelta(hours=8)).strftime(fmt)


def bj_today_utc():
    """当前北京时间 00:00 对应的 UTC datetime（用于过滤 created_at）"""
    from datetime import timedelta
    utc_now = datetime.utcnow()
    bj_now = utc_now + timedelta(hours=8)
    bj_today = bj_now.date()
    return datetime.combine(bj_today, datetime.min.time()) - timedelta(hours=8)


# ---------------------------------------------------------------------------
# API-1: GET /api/dispatch/task-pool
# 获取待分配任务池，按规则聚合
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/task-pool', methods=['GET'])
@login_required
def api_task_pool():
    from sqlalchemy import or_
    # 加载实例→提示词规则映射
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()

    # 待分配记录：已互检 + 未标注 + 未分配 + 未被撤回
    # 注意：annotator/check_result/revoked_batch 在数据库中为 NULL 而非空字符串，
    # 故用 or_() 同时匹配 NULL 和 ''，避免 SQL 中 NULL=='' 为 UNKNOWN 导致查询失败
    base_filter = [
        RawData.modelb_reviewed == True,
        or_(RawData.annotator == '', RawData.annotator.is_(None)),
        or_(RawData.check_result == '', RawData.check_result.is_(None)),
        or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
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

    # 计算已分配量（给标注员但未标注完成的，排除已撤回批次）
    # annotator != '' 需要同时处理 NULL：SQL 中 NULL != '' 结果为 NULL（不是 TRUE），需用 or_()
    assigned_rows = RawData.query.filter(
        RawData.modelb_reviewed == True,
        or_(RawData.annotator != '', RawData.annotator.isnot(None)),
        or_(RawData.check_result == '', RawData.check_result.is_(None)),
        or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
    ).all()

    # 已完成的任务（分配给标注员且已标注，排除已撤回批次）
    completed_rows = RawData.query.filter(
        RawData.modelb_reviewed == True,
        or_(RawData.annotator != '', RawData.annotator.isnot(None)),
        or_(RawData.check_result != '', RawData.check_result.isnot(None)),
        or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
    ).all()

    def _add_to_group(record):
        rule_name = instance_rule_map.get(record.instance_code, '') if record.instance_code else ''
        if not rule_name:
            rule_name = '未关联规则' + ('/' + record.instance_code if record.instance_code else '')
        data_type = '不一致数据' if record.modelb_consistent == False else '一致性抽检'
        # 延迟初始化：若该规则在 rule_groups 中不存在（说明此前无未分配记录），先初始化
        if rule_name not in rule_groups:
            rule_groups[rule_name] = {
                '不一致数据': {'total': 0, 'assigned': 0, 'instances': set(), 'batch_ids': set()},
                '一致性抽检': {'total': 0, 'assigned': 0, 'instances': set(), 'batch_ids': set()},
            }
        rule_groups[rule_name][data_type]['assigned'] += 1

    for r in assigned_rows:
        _add_to_group(r)
    for r in completed_rows:
        _add_to_group(r)

    # 构建输出 — 一个规则一张卡，合并不一致+抽检
    # total = assigned(pending+completed) + remaining(unassigned)
    rules_output = []
    for rule_name, types in rule_groups.items():
        inc = types['不一致数据']
        con = types['一致性抽检']
        remaining = inc['total'] + con['total']  # remaining = unassigned count (from base_filter)
        assigned = inc['assigned'] + con['assigned']  # assigned = pending + completed
        total = assigned + remaining               # total = all in pool
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
        db.or_(RawData.annotator == '', RawData.annotator.is_(None)),
        db.or_(RawData.check_result == '', RawData.check_result.is_(None)),
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
    if assign_method == 'manual':
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
    logs = []   # 暂存 DispatchLog 对象，提交后再统一更新 batch_no
    log_to_records = []  # [(log, [rec, ...]), ...]，用于提交后写入 dispatch_batch_no
    for annotator, share in zip(annotators, shares):
        if share <= 0:
            continue
        records_to_assign = available[idx:idx + share]
        idx += share

        for rec in records_to_assign:
            rec.annotator = annotator.username
            rec.assigned_at = datetime.utcnow()  # 记录分配时间
            rec.task_status = 'assigned'  # v3.1: 同步任务状态

        actual_assigned += len(records_to_assign)

        # 写分配记录（以实际数量为准）
        log = DispatchLog(
            admin_id=current_user.id,
            rule_name=rule_name,
            annotator_id=annotator.id,
            count=len(records_to_assign),
            assign_method=assign_method,
            data_type='全部',  # 不再区分不一致/一致性
            batch_id=records_to_assign[0].fetch_batch_id if records_to_assign else None,
        )
        db.session.add(log)
        logs.append(log)
        log_to_records.append((log, records_to_assign))

    db.session.commit()  # 先提交，获取各 log.id

    # 统一生成批次号（DISP-YYYYMMDD-NNN）
    # 同一管理员同日同一次分配操作的所有标注员，共用同一个批次号
    today = date.today().strftime('%Y%m%d')
    existing_count = DispatchLog.query.filter(
        DispatchLog.admin_id == current_user.id,
        DispatchLog.batch_no.like(f'DISP-{today}-%')
    ).count()
    seq = existing_count - len(logs) + 1 if existing_count >= len(logs) else 1
    shared_batch_no = f"DISP-{today}-{str(seq).zfill(3)}"

    # 生成任务编码（ANN-YYYYMMDD-NNN）
    # 同一日同一操作的所有记录共用同一 task_code
    existing_task = RawData.query.filter(RawData.task_code.like(f'ANN-{today}-%')).count()
    task_seq = existing_task + 1
    shared_task_code = f"ANN-{today}-{str(task_seq).zfill(3)}"

    # 所有标注员共用同一批次号和任务编码
    for i, log in enumerate(logs):
        log.batch_no = shared_batch_no
        for rec in log_to_records[i][1]:
            rec.dispatch_batch_no = shared_batch_no
            rec.task_code = shared_task_code
    db.session.commit()

    # 返回实际分配数量；若与请求不符，给出提示
    if actual_assigned < assign_count:
        return jsonify({
            'success': True,
            'message': f'成功分配 {actual_assigned} 条（请求 {assign_count} 条，部分实例可用数量不足）'
        })
    return jsonify({'success': True, 'message': f'成功分配 {actual_assigned} 条任务'})


def _today_assigned(annotator_id):
    """今日已分配给该标注员的总数量（含已完成，v2.3：完成任务也占据额度）"""
    today_cutoff = bj_today_utc()  # 北京今日 00:00 UTC
    return db.session.query(db.func.count(RawData.id)).filter(
        RawData.annotator == User.query.get(annotator_id).username,
        db.or_(RawData.annotator != '', RawData.annotator.isnot(None)),
        # v2.3 改动：移除 check_result == '' 过滤，已完成的任务也占据额度
        RawData.assigned_at >= today_cutoff,  # 用 assigned_at 而非 created_at（后者是抓取时间）
    ).scalar() or 0


# ---------------------------------------------------------------------------
# API-3: GET /api/dispatch/history
# 分配历史（按 batch_no 分组返回，支持展开查看每个标注员明细）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/history', methods=['GET'])
@login_required
def api_history():
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))

    # 每条 DispatchLog 独立返回一条（不再按 batch_no 合并）
    # 这样任务池按 rule 聚合时不会因 batch_no 共享而重复计数
    query = DispatchLog.query.order_by(DispatchLog.created_at.desc())
    total = query.count()
    logs = query.offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for log in logs:
        bn = log.batch_no or ''
        log_id = log.id

        # 查询该批次对应的全部 RawData（不过滤 annotator，
        # 因为 api_revoke_batch 会清空已撤回记录的 annotator 字段，
        # 导致查不到数据使 revoked_count 始终为 0）
        all_records = RawData.query.filter(
            RawData.dispatch_batch_no == bn,
        ).all() if bn else []

        # 分类统计（revoked_batch 在撤回后才写入，撤回前为 None 或 ''）
        completed_count_calc = sum(1 for r in all_records if r.check_result != '')
        revoked_pending = sum(1 for r in all_records if r.revoked_batch == bn and r.check_result == '')
        non_revoked_pending = sum(1 for r in all_records if not r.revoked_batch and r.check_result == '')
        pending_count = revoked_pending + non_revoked_pending  # 所有待标注（含已撤回的）
        revoked_count = revoked_pending  # 撤回的待标注记录数

        # 状态判断
        if revoked_count > 0:
            status = 'partially_revoked'
            display_total = len(all_records)  # 总数 = 已完成 + 已撤回待标注 + 未撤回待标注
        elif pending_count == 0:
            status = 'done'
            display_total = completed_count_calc
        else:
            status = 'active'
            display_total = pending_count

        # ---- FIX Issue #1 & #3: per-annotator 统计 ----
        # 该 DispatchLog 对应的标注员的用户名
        log_annotator_name = log.annotator.username if log.annotator else ''
        # 仅属于该标注员的 RawData 记录（用于计算该人的完成数和分配数）
        per_annot_records = [r for r in all_records if r.annotator == log_annotator_name] if log_annotator_name else []
        per_annot_completed = sum(1 for r in per_annot_records if r.check_result != '')
        per_annot_pending = sum(1 for r in per_annot_records if r.check_result == '')
        per_annot_count = len(per_annot_records)  # 该标注员在此批次的总分配数

        items.append({
            'batch_no': bn,
            'rule_name': log.rule_name or '',
            'admin_name': log.admin.name or log.admin.username if log.admin else '',
            'created_at': fmt_bj(log.created_at),
            'assign_method': log.assign_method or '',
            'total_count': log.count or 0,
            'completed_count': completed_count_calc,  # 批次维度（所有人合计），前端用于概览
            'pending_count': pending_count,
            'revoked_count': revoked_count,
            'status': status,
            'display_total': display_total,
            'isDone': pending_count == 0 and revoked_count == 0,
            'isPartiallyRevoked': revoked_count > 0,
            'annotators': [{
                'log_id': log_id,
                'annotator_name': log_annotator_name,
                # FIX Issue #1: count 用该标注员的实际分配数，而非批次总数
                'count': per_annot_count,
                # FIX Issue #3: completed_count 仅属于该标注员，而非批次所有人合计
                'completed_count': per_annot_completed,
                'pending_count': per_annot_pending,
                'isDone': per_annot_pending == 0,
            }],
        })

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': items,
    })


# ---------------------------------------------------------------------------
# API-REVOKE-BATCH: POST /api/dispatch/revoke-batch
# 按批次号撤回分配（仅撤回 check_result 为空的记录）
# 支持单个 batch_no 或批量 batch_nos 数组（v3.0）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/revoke-batch', methods=['POST'])
@login_required
def api_revoke_batch():
    """撤回一个或多个批次（v2.3：区分已完成/未完成）
    - 未完成：标记 revoked_batch，清空 annotator，退回待分配池
    - 已完成：保留分配关系，占据今日额度
    - DispatchLog：改为 partially_revoked 状态（不删除），保留 completed_count
    - 返回 revoked_count（退回池中）+ kept_count（保留已完成）
    """
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}

    single_no = (data.get('batch_no') or '').strip()
    batch_nos = data.get('batch_nos') or []
    if isinstance(batch_nos, str):
        batch_nos = [batch_nos]
    if single_no and single_no not in batch_nos:
        batch_nos = [single_no] + batch_nos
    batch_nos = [bn.strip() for bn in batch_nos if bn and bn.strip()]
    batch_nos = list(dict.fromkeys(batch_nos))  # 去重保留顺序

    if not batch_nos:
        return jsonify({'success': False, 'error': '批次号不能为空'}), 400

    total_revoked = 0       # 退回池中的未完成任务数
    total_kept = 0           # 保留的已完成任务数
    per_batch = {}           # {batch_no: {revoked, kept}}

    for batch_no in batch_nos:
        logs = DispatchLog.query.filter(DispatchLog.batch_no == batch_no).all()
        if not logs:
            continue

        all_records = RawData.query.filter(
            RawData.dispatch_batch_no == batch_no,
        ).all()

        batch_revoked = 0
        batch_kept = 0

        for rec in all_records:
            if rec.check_result == '':
                # 未完成 → 撤回：标记 revoked_batch，清空 annotator
                rec.revoked_batch = batch_no
                rec.annotator = ''
                rec.task_status = 'unassigned'  # v3.1: 同步任务状态
                batch_revoked += 1
            else:
                # 已完成 → 保留分配关系，占据额度
                # 不动任何字段，但标注 revoked_batch 以标识该记录曾经历撤回
                rec.revoked_batch = batch_no
                batch_kept += 1

        total_revoked += batch_revoked
        total_kept += batch_kept
        per_batch[batch_no] = {'revoked': batch_revoked, 'kept': batch_kept}

        # 不更新 DispatchLog.count：保留原始分配数（如每标注员 1 条）。
        # display_total 从 RawData 实时统计，通过 api_history 的 display_total 字段传给前端。

    db.session.commit()

    if len(batch_nos) == 1:
        msg = f'已撤回批次 {batch_nos[0]}（退回 {total_revoked} 条 / 保留 {total_kept} 条已完成）'
    else:
        msg = f'已撤回 {len(batch_nos)} 个批次（退回 {total_revoked} 条 / 保留 {total_kept} 条已完成）'

    return jsonify({
        'success': True,
        'revoked_count': total_revoked,
        'kept_count': total_kept,
        'total_count': total_revoked + total_kept,
        'per_batch': per_batch,
        'batch_nos': batch_nos,
        'message': msg,
    })


# ---------------------------------------------------------------------------
# API-4: DELETE /api/dispatch/revoke
# 撤回已分配但未标注的任务
# - 无参数：撤回全部已分配未标注记录
# - 带 rule_name：撤回指定规则的全部分配
# - 带 rule_name + annotator_id：撤回指定规则+标注员的分配
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/revoke', methods=['DELETE'])
@login_required
def api_revoke():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}
    rule_name = data.get('rule_name') or ''
    annotator_id = data.get('annotator_id')
    batch_nos = data.get('batch_nos') or []

    base = [
        db.or_(RawData.annotator != '', RawData.annotator.isnot(None)),
        db.or_(RawData.check_result == '', RawData.check_result.is_(None)),
    ]

    # v3.0: 若指定了 batch_nos，只撤回选中批次的分配（支持撤回全部已分配的场景）
    if batch_nos:
        from sqlalchemy import or_
        batch_nos = [bn for bn in batch_nos if bn]
        if batch_nos:
            base.append(or_(*[RawData.dispatch_batch_no == bn for bn in batch_nos]))

    # 若指定了 rule_name，按规则过滤（与 batch_nos 同时生效）
    if rule_name:
        from services.fetch_service import get_instance_rule_mapping
        instance_rule_map = get_instance_rule_mapping()
        matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_name]
        if matched_instances:
            from sqlalchemy import or_
            base.append(or_(*[RawData.instance_code == inst for inst in matched_instances]))

    # 若指定了标注员，进一步过滤
    if annotator_id:
        user = User.query.get(int(annotator_id))
        if user:
            base.append(RawData.annotator == user.username)
        else:
            return jsonify({'success': False, 'error': '标注员不存在'}), 400

    records = RawData.query.filter(*base).all()
    count = len(records)
    for rec in records:
        rec.revoked_batch = rec.dispatch_batch_no or 'REVOKE'   # v2.2：标记已撤回
        rec.annotator = ''
        rec.dispatch_batch_no = None
        rec.task_status = 'unassigned'  # v3.1: 同步任务状态

    db.session.commit()

    if batch_nos and not rule_name and not annotator_id:
        msg = f'已撤回选中 {len(batch_nos)} 个批次的 {count} 条分配'
    elif not rule_name and not annotator_id:
        msg = f'已撤回全部 {count} 条已分配未标注任务'
    elif annotator_id:
        msg = f'已撤回 {count} 条分配'
    else:
        msg = f'已撤回规则 [{rule_name}] 的 {count} 条分配'

    return jsonify({'success': True, 'revoked_count': count, 'message': msg})


# ---------------------------------------------------------------------------
# API-REVOKE-LOG: POST /api/dispatch/revoke-log
# 按 DispatchLog ID 撤回单条分配记录（仅撤回该标注员的分配）
# v2.2：对待标注和已标注记录统一标记 revoked_batch，任务列表不再显示
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/revoke-log', methods=['POST'])
@login_required
def api_revoke_log():
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足'}), 403

    data = request.get_json() or {}
    log_id = data.get('log_id')

    if not log_id:
        return jsonify({'success': False, 'error': 'log_id 不能为空'}), 400

    log_entry = DispatchLog.query.get(int(log_id))
    if not log_entry:
        return jsonify({'success': False, 'error': '分配记录不存在'}), 404

    batch_no = log_entry.batch_no
    annotator_name = log_entry.annotator.username if log_entry.annotator else ''

    # 找出该批次该标注员的全部记录（pending + completed）
    all_records = RawData.query.filter(
        RawData.annotator == annotator_name,
        RawData.dispatch_batch_no == batch_no,
    ).all()

    revoked_count = 0    # 退回池中的未完成任务
    kept_count = 0       # 保留的已完成任务
    for rec in all_records:
        if rec.check_result == '':
            # 未完成 → 撤回：标记 revoked_batch，清空 annotator
            rec.revoked_batch = batch_no
            rec.annotator = ''
            rec.task_status = 'unassigned'  # v3.1: 同步任务状态
            revoked_count += 1
        else:
            # 已完成 → 保留分配关系，但标注 revoked_batch
            rec.revoked_batch = batch_no
            kept_count += 1

    # 更新 DispatchLog count（不删除，保留记录以供历史查看）
    log_entry.count = kept_count
    # completed_count 保持不变

    db.session.commit()

    return jsonify({
        'success': True,
        'revoked_count': revoked_count,
        'kept_count': kept_count,
        'message': f'已撤回 [{log_entry.annotator.name or annotator_name}] 的分配（退回 {revoked_count} 条 / 保留 {kept_count} 条已完成）'
    })


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
    dispatch_batch_no = request.args.get('dispatch_batch_no', '').strip()  # 分配批次号（v2.1）
    task_code_filter = request.args.get('task_code', '').strip()  # 任务编码（v2.0）
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    base = [
        RawData.modelb_reviewed == True,
        db.or_(RawData.annotator != '', RawData.annotator.isnot(None)),
    ] + annotator_filter

    # FIX Issue #2（revoked_batch 过滤修正）：
    # 部分撤回后，completed 记录保留 check_result 且 revoked_batch = batch_no，
    # pending 记录 revoked_batch = batch_no 且 annotator 被清空。
    # 正确逻辑：排除"此批次撤回的 pending 记录"，保留"此批次的 completed 记录"。
    has_batch_filter = bool(dispatch_batch_no)
    if has_batch_filter:
        if status_filter == 'done':
            # 仅已完成：包含该批次中所有已完成记录（含部分撤回后保留的）
            base.append(db.or_(RawData.check_result != '', RawData.check_result.isnot(None)))
        else:
            # pending 或全部：已完成永远可见；pending 仅显示从未被任何批次撤回的
            base.append(db.or_(
                db.or_(RawData.check_result != '', RawData.check_result.isnot(None)),
                db.and_(
                    db.or_(RawData.check_result == '', RawData.check_result.is_(None)),
                    db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),  # pending 且从未被任何批次撤回
                )
            ))
    else:
        # 无 batch_no 过滤：排除所有已撤回批次的记录（无论 pending 或 completed）
        # revoked_batch == '' → 从未被撤回的记录（可见）
        # revoked_batch != '' → 曾被撤回（隐藏）
        base.append(db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)))

    # 加载 instance→rule 映射（v2.0：rule_name 从映射表读取，而非 computed_error_reason）
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()

    if dispatch_batch_no:
        base.append(RawData.dispatch_batch_no == dispatch_batch_no)  # v2.1：按批次号精确过滤

    if task_code_filter:
        base.append(RawData.task_code == task_code_filter)  # v2.0：按任务编码精确过滤

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
        base.append(db.or_(RawData.check_result == '', RawData.check_result.is_(None)))
    elif status_filter == 'done':
        base.append(db.or_(RawData.check_result != '', RawData.check_result.isnot(None)))

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
            'rule_name': instance_rule_map.get(r.instance_code, '') or r.computed_error_reason or '',
            'data_date': str(r.created_date or '')[:10].replace('-','/'),
            'check_result': r.check_result or '',
            'task_code': r.task_code or '',  # 任务编码（历史兼容）
            'dispatch_batch_no': r.dispatch_batch_no or '',  # 分配批次号（v2.1）
            # 图片字段不过截断，支持逗号分隔的多 URL 字符串完整传递
            'main_image': r.main_image or '',
            'detail_image': r.detail_image or '',
            'sku_image': r.sku_image or '',
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
# API-5b: GET /api/annotation/my-task-groups
# 任务级聚合视图（v2.0 新增）
# 按 task_code 聚合，统计总数/待标注/正确/错误/忽略
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/annotation/my-task-groups', methods=['GET'])
@login_required
def api_my_task_groups():
    """标注员获取任务级聚合列表"""
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    # 管理员查看所有；标注员只看自己的
    if current_user.role == 'admin':
        annotator_filter = []
    else:
        annotator_filter = [RawData.annotator == current_user.username]

    instance_filter = request.args.get('instance', '').strip()
    rule_filter = request.args.get('rule_name', '').strip()
    dispatch_batch_filter = request.args.get('dispatch_batch_no', '').strip()
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 20))

    base = [
        RawData.modelb_reviewed == True,
        db.or_(RawData.annotator != '', RawData.annotator.isnot(None)),
    ] + annotator_filter

    # FIX Issue #2（revoked_batch 过滤修正）：与 api_my_tasks 保持一致
    # 已完成记录永远可见（不可撤回）；仅 pending 记录受 revoked_batch 过滤
    if dispatch_batch_filter:
        base.append(
            db.or_(
                db.or_(RawData.check_result != '', RawData.check_result.isnot(None)),  # 已完成：永远可见
                db.and_(
                    db.or_(RawData.check_result == '', RawData.check_result.is_(None)),
                    db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),  # pending 且从未被任何批次撤回
                )
            )
        )
    else:
        # 无 batch_no 过滤：排除所有已撤回批次的记录（无论 pending 或 completed）
        # revoked_batch == '' → 从未被撤回的记录（可见）
        # revoked_batch != '' → 曾被撤回（隐藏）
        base.append(db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)))

    if instance_filter:
        base.append(RawData.instance_code == instance_filter)

    if rule_filter:
        from services.fetch_service import get_instance_rule_mapping
        instance_rule_map = get_instance_rule_mapping()
        matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_filter]
        if matched_instances:
            from sqlalchemy import or_
            base.append(or_(*[RawData.instance_code == inst for inst in matched_instances]))

    # 加载 instance→rule 映射（用于显示规则名）
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()

    # 查询所有匹配的记录（用于前端分组；最多查 5000 条）
    records = RawData.query.filter(*base).order_by(RawData.id.desc()).limit(5000).all()

    # 按 task_code 分组（无 task_code 则用 created_date+instance_code 拼装虚拟 key）
    groups = {}
    for r in records:
        key = r.task_code
        if not key:
            # 无 task_code 时，用虚拟编码：ANN-VIRTUAL-{date}-{instance}
            key = f'ANN-VIRTUAL-{str(r.created_date or "")[:8]}-{r.instance_code or ""}'
        if key not in groups:
            groups[key] = {
                'task_code': r.task_code or key,
                'rule_name': instance_rule_map.get(r.instance_code, '') or r.rule_name or '',
                'data_date': str(r.created_date or '')[:10].replace('-', '/'),
                'instance_code': r.instance_code or '',
                'total_count': 0,
                'pending_count': 0,
                'annotated_count': 0,
                'correct_count': 0,
                'error_count': 0,
                'ignore_count': 0,
                # 合规/违规分桶（按 ai_result 划分）
                'compliant_total': 0,        # ai_result='合规' 的总条数
                'compliant_correct': 0,      # ai_result='合规' + check_result='correct'
                'compliant_annotated': 0,    # ai_result='合规' 的已标注数
                'compliant_ignore': 0,       # ai_result='合规' 的忽略数
                'violation_total': 0,        # ai_result='违规' 的总条数
                'violation_correct': 0,      # ai_result='违规' + check_result='correct'
                'violation_annotated': 0,   # ai_result='违规' 的已标注数
                'violation_ignore': 0,       # ai_result='违规' 的忽略数
                'dispatch_batch_no': r.dispatch_batch_no or '',
                'annotator': r.annotator or '',
            }
        g = groups[key]
        g['total_count'] += 1

        # 判断 AI 结果分类：用 ai_reject_reason 字段（latin1编码问题无法直接比较 ai_result）
        # 有驳回原因 = 违规；无驳回原因 = 合规
        ai_is_violation = bool(r.ai_reject_reason and r.ai_reject_reason.strip())

        # 合规总数 / 违规总数：统计所有记录（不区分是否已标注）
        if ai_is_violation:
            g['violation_total'] += 1
        else:
            g['compliant_total'] += 1

        if r.check_result == 'correct':
            g['correct_count'] += 1
            g['annotated_count'] += 1
            if ai_is_violation:
                g['violation_correct'] += 1
                g['violation_annotated'] += 1
            else:
                g['compliant_correct'] += 1
                g['compliant_annotated'] += 1
        elif r.check_result == 'error':
            g['error_count'] += 1
            g['annotated_count'] += 1
            if ai_is_violation:
                g['violation_annotated'] += 1
            else:
                g['compliant_annotated'] += 1
        elif r.check_result == 'ignore':
            g['ignore_count'] += 1
            g['annotated_count'] += 1
            if ai_is_violation:
                g['violation_ignore'] += 1
                g['violation_annotated'] += 1
            else:
                g['compliant_ignore'] += 1
                g['compliant_annotated'] += 1
        else:
            # pending：待标注记录（已在上方计入 compliant_total / violation_total）
            g['pending_count'] += 1

    # 从 DispatchLog 补充分配人和分配时间
    task_codes = [g['task_code'] for g in groups.values()]
    batch_nos = [g['dispatch_batch_no'] for g in groups.values() if g['dispatch_batch_no']]
    log_map = {}
    if batch_nos:
        logs = DispatchLog.query.filter(DispatchLog.batch_no.in_(batch_nos)).all()
        for log in logs:
            log_map[log.batch_no] = log

    # 计算进度百分比
    items = []
    for g in groups.values():
        progress = (g['annotated_count'] / g['total_count'] * 100) if g['total_count'] > 0 else 0
        # 分配人/时间：从第一条记录对应的 DispatchLog 获取
        admin_name = ''
        assign_time = ''
        log = log_map.get(g['dispatch_batch_no'])
        if log:
            admin_name = log.admin.name or log.admin.username if log.admin else ''
            assign_time = log.created_at.strftime('%Y-%m-%d %H:%M') if log.created_at else ''
        # 整体准确率 = 正确数 / (已标注数 - 忽略数)
        annotated_minus_ignore = g['annotated_count'] - g['ignore_count']
        overall_accuracy = round(g['correct_count'] / annotated_minus_ignore * 100, 1) if annotated_minus_ignore > 0 else 0
        # 合规准确率 = 合规正确数 / (合规已标注 - 合规忽略数)
        comp_ann_minus_ign = g['compliant_annotated'] - g['compliant_ignore']
        compliant_accuracy = round(g['compliant_correct'] / comp_ann_minus_ign * 100, 1) if comp_ann_minus_ign > 0 else 0
        # 违规准确率 = 违规正确数 / (违规已标注 - 违规忽略数)
        viol_ann_minus_ign = g['violation_annotated'] - g['violation_ignore']
        violation_accuracy = round(g['violation_correct'] / viol_ann_minus_ign * 100, 1) if viol_ann_minus_ign > 0 else 0
        items.append({
            'task_code': g['task_code'],
            'rule_name': g['rule_name'],
            'data_date': g['data_date'],
            'instance_code': g['instance_code'],
            'total_count': g['total_count'],
            'pending_count': g['pending_count'],
            'annotated_count': g['annotated_count'],
            'correct_count': g['correct_count'],
            'error_count': g['error_count'],
            'ignore_count': g['ignore_count'],
            'overall_accuracy': overall_accuracy,
            'compliant_total': g['compliant_total'],
            'compliant_accuracy': compliant_accuracy,
            'violation_total': g['violation_total'],
            'violation_accuracy': violation_accuracy,
            'progress': round(progress, 1),
            'admin_name': admin_name,
            'assign_time': assign_time,
            'dispatch_batch_no': g['dispatch_batch_no'],
        })

    # 按 task_code 降序排序
    items.sort(key=lambda x: x['task_code'], reverse=True)

    # 分页
    total = len(items)
    start = (page - 1) * per_page
    end = start + per_page
    paged = items[start:end]

    return jsonify({
        'success': True,
        'total': total,
        'pages': (total + per_page - 1) // per_page,
        'items': paged,
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
    rec.task_status = 'annotated'  # v3.1: 同步任务状态

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
        # today_assigned：仍用 SQL 过滤（从 _today_assigned 逻辑复用，created_at>=北京今日0点的UTC）
        today_assigned = _today_assigned(a.id)

        # today_completed：使用 Python 级日期比较（UTC+8 处理）
        from datetime import timedelta
        bj_today = (datetime.utcnow() + timedelta(hours=8)).date()
        bj_today_start_utc = datetime.combine(bj_today, datetime.min.time()) - timedelta(hours=8)
        bj_today_end_utc = datetime.combine(bj_today, datetime.max.time()) - timedelta(hours=8)

        # 查询今日有效标注记录（用于统计准确率）
        today_ann_records = db.session.query(Annotation).join(
            RawData, Annotation.raw_data_id == RawData.id
        ).filter(
            Annotation.annotator_id == a.id,
            Annotation.is_submitted == True,
            Annotation.created_at >= bj_today_start_utc,
            Annotation.created_at <= bj_today_end_utc,
        ).all()
        today_completed = len(today_ann_records)
        correct_count = sum(1 for r in today_ann_records if r.result == 'correct')
        today_accuracy = round(correct_count / today_completed * 100, 1) if today_completed > 0 else None

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
            'today_accuracy': today_accuracy,  # 今日标注准确率（v2.0 新增）
            'bound_rules': bound,
        })

    return jsonify({'success': True, 'items': items})


# ---------------------------------------------------------------------------
# API: GET /api/dispatch/annotator-stats
# 标注员历史标注统计：按日期+规则聚合，准确率计算
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/annotator-stats', methods=['GET'])
@login_required
def api_annotator_stats():
    from datetime import timedelta
    try:
        annotator_id = int(request.args.get('annotator_id', 0))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'error': '无效的 annotator_id'}), 400

    try:
        days = int(request.args.get('days', 30))
        if days < 0:
            days = 0
    except (ValueError, TypeError):
        days = 30

    rule_name = request.args.get('rule_name', '').strip()

    # 时间范围过滤（北京时区）
    cutoff_bj = None
    if days > 0:
        bj_now = datetime.utcnow() + timedelta(hours=8)
        cutoff_bj = bj_now.date() - timedelta(days=days - 1)  # 含起始日
        # 转换为 UTC 00:00（北京时间次日的 16:00）
        cutoff_utc = datetime.combine(cutoff_bj, datetime.min.time()) - timedelta(hours=8)

    # 标注员存在性校验
    annotator = User.query.get(annotator_id)
    if not annotator:
        return jsonify({'success': False, 'error': '标注员不存在'}), 404

    # 基础查询：只查已提交的
    q = db.session.query(
        Annotation.created_at,
        Annotation.result,
        DispatchLog.rule_name,
    ).join(
        RawData, Annotation.raw_data_id == RawData.id
    ).outerjoin(
        DispatchLog,
        db.and_(
            RawData.dispatch_batch_no == DispatchLog.batch_no,
            DispatchLog.annotator_id == Annotation.annotator_id,
        )
    ).filter(
        Annotation.annotator_id == annotator_id,
        Annotation.is_submitted == True,
    )

    # 时间范围过滤（UTC 存储，UTC+8 比较）
    if cutoff_utc:
        q = q.filter(Annotation.created_at >= cutoff_utc)

    # 规则过滤
    if rule_name:
        q = q.filter(DispatchLog.rule_name == rule_name)

    rows = q.all()

    # 按 (date_bj, rule_name) 分组聚合
    grouped = {}
    for row in rows:
        created_at, result, rn = row
        if created_at is None:
            continue
        bj_dt = created_at + timedelta(hours=8)
        date_key = bj_dt.strftime('%Y-%m-%d')
        rn = rn or '未分类'
        key = (date_key, rn)
        if key not in grouped:
            grouped[key] = {'annotated': 0, 'correct': 0}
        grouped[key]['annotated'] += 1
        if result == 'correct':
            grouped[key]['correct'] += 1

    # 构建 items
    items = []
    total_annotated = 0
    total_correct = 0
    for (date_key, rn), counts in sorted(grouped.items()):
        ann = counts['annotated']
        cor = counts['correct']
        acc = round(cor / ann * 100, 1) if ann > 0 else 0.0
        items.append({
            'date': date_key,
            'rule_name': rn,
            'annotated_count': ann,
            'correct_count': cor,
            'accuracy': acc,
        })
        total_annotated += ann
        total_correct += cor

    overall = round(total_correct / total_annotated * 100, 1) if total_annotated > 0 else 0.0

    return jsonify({
        'success': True,
        'items': items,
        'summary': {
            'total_annotated': total_annotated,
            'total_correct': total_correct,
            'overall_accuracy': overall,
        }
    })


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
# 生成标注任务（v1.4 新增，v2.1 支持漏审数据抽取）
# 接收 batch_id + rule_name + instance + sample_percent + data_mode
# - data_mode=preview          返回各分类数量（弹窗预览用，不修改数据）
# - data_mode=missed_review    漏审数据（modelb_reviewed=False）100% 纳入任务池
# - data_mode=inconsistent     不一致数据（modelb_consistent=False）100% 纳入任务池
# - data_mode=consistent       一致性数据按比例随机抽取
# - data_mode=both             漏审+不一致+一致性抽检（默认）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/generate-tasks', methods=['POST'])
@login_required
def api_generate_tasks():
    """生成标注任务（管理员手动触发）

    data_mode 行为说明：
      preview         → 仅返回各分类数量，弹窗预览用，不修改任何数据
      missed_review   → 将 modelb_reviewed=False 的记录标记为已审，纳入任务池
      inconsistent    → 将已互检的不一致记录纳入任务池
      consistent      → 从已互检的一致性记录中按比例抽样
      both            → 上述三者均纳入（漏审100%、不一致100%、一致性按比例）
    """
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足，仅管理员可操作'}), 403

    data = request.get_json() or {}
    batch_id = str(data.get('batch_id', '')).strip()
    rule_name = str(data.get('rule_name', '')).strip()
    instance_code = str(data.get('instance', '')).strip() or None
    sample_percent = float(data.get('sample_percent', 5.0))
    data_mode = str(data.get('data_mode', 'both')).lower()

    # preview 模式只需要 batch_id，不做其他校验
    is_preview = (data_mode == 'preview')

    if not batch_id:
        return jsonify({'success': False, 'error': 'batch_id 不能为空'}), 400
    if not is_preview and not rule_name:
        return jsonify({'success': False, 'error': '规则名不能为空'}), 400
    if not is_preview and (sample_percent < 0 or sample_percent > 100):
        return jsonify({'success': False, 'error': '抽检比例需在 0~100 之间'}), 400

    # 确认批次存在
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({'success': False, 'error': '批次不存在'}), 404

    if not is_preview and fetch_log.review_status != 'completed':
        return jsonify({'success': False, 'error': '该批次互检尚未完成，无法生成标注任务'}), 400

    import logging
    logger = logging.getLogger('werkzeug')
    logger.error(f'[GenerateTasks] batch_id={batch_id} data_mode={data_mode} sample_percent={sample_percent}')

    # ========== 核心计数查询 ==========
    # 不一致数据（已互检且 modelb_consistent=False）
    inconsistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == False,
    ]
    if instance_code:
        inconsistent_filter.append(RawData.instance_code == instance_code)
    inconsistent_count = RawData.query.filter(*inconsistent_filter).count()

    # 漏审数据（modelb_reviewed=False，模型B从未返回结果）
    missed_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == False,
    ]
    if instance_code:
        missed_filter.append(RawData.instance_code == instance_code)
    missed_count = RawData.query.filter(*missed_filter).count()

    # 一致性数据（已互检且 modelb_consistent=True）
    consistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == True,
    ]
    if instance_code:
        consistent_filter.append(RawData.instance_code == instance_code)

    import random
    consistent_all = RawData.query.filter(*consistent_filter).all()
    if sample_percent > 0 and sample_percent < 100:
        threshold = sample_percent / 100.0
        sampled_records = [r for r in consistent_all if (r.random_num or random.random()) < threshold]
        sampled_count = len(sampled_records)
    elif sample_percent >= 100:
        sampled_records = consistent_all
        sampled_count = len(sampled_records)
    else:
        sampled_records = []
        sampled_count = 0

    logger.error(f'[GenerateTasks] 一致总数={len(consistent_all)} 不一致={inconsistent_count} 漏审={missed_count} 抽样={sampled_count}(阈值={sample_percent}%)')

    # ========== preview 模式：仅返回计数，不修改数据 ==========
    if is_preview:
        return jsonify({
            'success': True,
            'preview': True,
            'inconsistent_count': inconsistent_count,
            'missed_count': missed_count,
            'sampled_count': sampled_count,
            'sample_percent': sample_percent,   # 传回前端用于百分比输入框同步
            'consistent_total': len(consistent_all),  # 一致性数据总量
        })

    # ========== 执行模式：根据 data_mode 处理数据 ==========
    updated_missed = 0
    if data_mode == 'missed_review':
        # 漏审数据：标记 modelb_reviewed=True，让其进入任务池
        missed_records = RawData.query.filter(*missed_filter).all()
        for rec in missed_records:
            rec.modelb_reviewed = True
            rec.modelb_result = '漏审'
            rec.modelb_reason = '漏审-模型B无返回'
            rec.modelb_consistent = None
        updated_missed = len(missed_records)
        inconsistent_count = 0
        sampled_count = 0
        sampled_records = []
        logger.error(f'[GenerateTasks] 标记 {updated_missed} 条漏审数据 modelb_reviewed=True')

    elif data_mode == 'inconsistent':
        # 仅不一致数据（已有字段，无需修改 RawData）
        sampled_count = 0
        sampled_records = []

    elif data_mode == 'consistent':
        # 仅一致性数据（sampled_records/sampled_count 已在上方计算好）
        inconsistent_count = 0
        updated_missed = 0
        logger.error(f'[GenerateTasks] 执行一致性抽样，实际纳入 {sampled_count} 条（阈值 {sample_percent}%）')

    # 'both' 或其他：漏审+不一致+sampling 均纳入任务池（RawData 已有标记，无需修改）

    # 更新 FetchLog 标记已生成
    fetch_log.task_generated = True
    fetch_log.task_generate_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fetch_log.task_sample_percent = sample_percent

    db.session.commit()

    return jsonify({
        'success': True,
        'message': f'生成成功',
        'inconsistent_count': inconsistent_count,
        'missed_count': updated_missed if data_mode == 'missed_review' else missed_count,
        'sampled_count': sampled_count,
        'sampled_percent': sample_percent,       # 本次执行的抽样比例
        'consistent_total': len(consistent_all),  # 一致性数据总量
        'total_entered': inconsistent_count + (updated_missed if data_mode == 'missed_review' else missed_count) + sampled_count,
        'task_generated': True,
        'task_generate_time': fetch_log.task_generate_time,
        'task_sample_percent': sample_percent,
        'data_mode': data_mode,
    })


# ---------------------------------------------------------------------------
# API-8.5: POST /api/dispatch/regenerate-task-pool（v3.1）
# 智能撤回未被领取的任务，并重新生成任务池条目
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/regenerate-task-pool', methods=['POST'])
@login_required
def api_regenerate_task_pool():
    """重新生成任务池（智能撤回 + 重新生成）

    逻辑：
      Step 1 - 智能撤回：查询该批次 task_status='assigned' 但 check_result 仍为空的记录
               （已分配但未完成标注），清空 annotator + 设置 revoked_batch + task_status='unassigned'
               注意：task_status='annotated' 的记录完全不动（已完成标注不可逆）
      Step 2 - 重新生成：复用 api_generate_tasks 的抽样逻辑，为退回的数据重新纳入任务池
      Step 3 - 返回结果

    与 api_generate_tasks 的区别：
      - api_generate_tasks：从 0 开始生成任务，不处理已有任务
      - 本接口：只撤回"已分配未标注"的任务，不影响"已标注"任务，再重新生成
    """
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足，仅管理员可操作'}), 403

    data = request.get_json() or {}
    batch_id = str(data.get('batch_id', '')).strip()
    rule_name = str(data.get('rule_name', '')).strip()
    instance_code = str(data.get('instance', '')).strip() or None
    sample_percent = float(data.get('sample_percent', 5.0))
    data_mode = str(data.get('data_mode', 'inconsistent')).lower()

    if not batch_id:
        return jsonify({'success': False, 'error': 'batch_id 不能为空'}), 400

    # 确认批次存在
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({'success': False, 'error': '批次不存在'}), 404

    # ========== Step 1：智能撤回 ==========
    # 查询该批次中：已分配（task_status='assigned'）但未标注（check_result 为空）的记录
    # 注意：task_status 为 NULL 的历史数据用派生逻辑计算
    assigned_filter = [
        RawData.fetch_batch_id == batch_id,
    ]
    if instance_code:
        assigned_filter.append(RawData.instance_code == instance_code)

    # 找出已分配但未完成的记录
    # 精确条件：annotator 有实际值（非空字符串 AND 非NULL）AND check_result 为空 AND 未被撤回
    # 关键修复：
    #   - SQLAlchemy 中 annotator!='' 对空字符串返回 0 行，对 NULL 返回 124 行
    #   - SQLite 中 '' IS NOT NULL = True（空字符串不是 NULL）
    #   - 因此必须用 AND(annotator != '', annotator.isnot(None)) 确保两边同时满足
    #     对空字符串：False AND True = False ✅
    #     对 NULL：True AND False = False ✅
    #     对 '林柒'：True AND True = True ✅
    revoke_filter = db.and_(
        db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),    # annotator 有实际值（非空非NULL）
        db.or_(RawData.check_result == '', RawData.check_result.is_(None)),  # check_result 为空
        db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),  # 未被撤回
    )
    to_revoke = RawData.query.filter(
        RawData.fetch_batch_id == batch_id,
        revoke_filter,
    ).all()

    revoked_count = len(to_revoke)
    import datetime as dt
    batch_no_prefix = f'REGEN-{dt.date.today().strftime("%Y%m%d")}-{batch_id}'

    for rec in to_revoke:
        rec.revoked_batch = batch_no_prefix
        rec.annotator = ''
        rec.task_status = 'unassigned'  # v3.1

    import logging
    logger = logging.getLogger('werkzeug')
    logger.error(f'[RegenerateTaskPool] batch_id={batch_id} revoked_count={revoked_count}')

    # ========== Step 2：重新生成（复用 api_generate_tasks 的核心逻辑）==========
    # 不一致数据
    inconsistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == False,
    ]
    if instance_code:
        inconsistent_filter.append(RawData.instance_code == instance_code)
    inconsistent_count = RawData.query.filter(*inconsistent_filter).count()

    # 漏审数据
    missed_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == False,
    ]
    if instance_code:
        missed_filter.append(RawData.instance_code == instance_code)
    missed_count = RawData.query.filter(*missed_filter).count()

    # 一致性数据抽样
    consistent_filter = [
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == True,
    ]
    if instance_code:
        consistent_filter.append(RawData.instance_code == instance_code)

    import random
    consistent_all = RawData.query.filter(*consistent_filter).all()
    if sample_percent > 0 and sample_percent < 100:
        threshold = sample_percent / 100.0
        sampled_records = [r for r in consistent_all if (r.random_num or random.random()) < threshold]
        sampled_count = len(sampled_records)
    elif sample_percent >= 100:
        sampled_records = consistent_all
        sampled_count = len(sampled_records)
    else:
        sampled_records = []
        sampled_count = 0

    # 处理漏审数据（标记 modelb_reviewed=True）
    updated_missed = 0
    if data_mode == 'missed_review':
        missed_records = RawData.query.filter(*missed_filter).all()
        for rec in missed_records:
            rec.modelb_reviewed = True
            rec.modelb_result = '漏审'
            rec.modelb_reason = '漏审-模型B无返回'
            rec.modelb_consistent = None
        updated_missed = len(missed_records)
        inconsistent_count = 0
        sampled_count = 0
        sampled_records = []
        logger.error(f'[RegenerateTaskPool] 标记 {updated_missed} 条漏审数据')
    elif data_mode == 'inconsistent':
        sampled_count = 0
        sampled_records = []
    elif data_mode == 'consistent':
        inconsistent_count = 0
        updated_missed = 0

    # 确保 FetchLog.task_generated 保持 True
    fetch_log.task_generated = True
    if not fetch_log.task_generate_time:
        fetch_log.task_generate_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    db.session.commit()

    # 统计任务池当前总数量（未被撤回的 assigned/unassigned 记录）
    pool_filter = [
        RawData.fetch_batch_id == batch_id,
        db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
    ]
    if instance_code:
        pool_filter.append(RawData.instance_code == instance_code)
    total_in_pool = RawData.query.filter(*pool_filter).count()

    logger.error(f'[RegenerateTaskPool] 重新生成完成: revoked={revoked_count}, inconsistent={inconsistent_count}, missed={updated_missed}, sampled={sampled_count}, total_pool={total_in_pool}')

    return jsonify({
        'success': True,
        'revoked_count': revoked_count,
        'regenerated_inconsistent': inconsistent_count,
        'regenerated_missed': updated_missed if data_mode == 'missed_review' else missed_count,
        'regenerated_sampled': sampled_count,
        'total_in_pool': total_in_pool,
        'message': f'重新生成成功。撤回 {revoked_count} 条，重新纳入 {inconsistent_count + (updated_missed if data_mode == "missed_review" else missed_count) + sampled_count} 条。',
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
