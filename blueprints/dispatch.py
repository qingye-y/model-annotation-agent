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
from datetime import date, datetime, timedelta
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
    from sqlalchemy import or_, and_
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
    # 注意：空字符串 '' 在 SQL 中 != '' 为 FALSE，需同时用 and_() 配合 IS NOT NULL，
    # 确保 annotator='' 的记录不被匹配（尤其在 Python 3.14 + SQLite 下）
    assigned_rows = RawData.query.filter(
        RawData.modelb_reviewed == True,
        and_(RawData.annotator != '', RawData.annotator.isnot(None)),
        or_(RawData.check_result == '', RawData.check_result.is_(None)),
        or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
    ).all()

    # 已完成的任务（分配给标注员且已标注，排除已撤回批次）
    completed_rows = RawData.query.filter(
        RawData.modelb_reviewed == True,
        and_(RawData.annotator != '', RawData.annotator.isnot(None)),
        and_(RawData.check_result != '', RawData.check_result.isnot(None)),
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
    selected_dates = data.get('dates', [])  # v1.0：日期筛选，格式 ["MM/DD", "MM/DD"]

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

    # 确认标注员/管理员存在且启用（按 username 匹配，前端传的是 username 字符串）
    # v2.0+：允许 admin 分配给自己，即 admin 也可作为标注任务执行者
    annotators = User.query.filter(
        User.username.in_(annotator_ids),
        User.role.in_(['annotator', 'admin']),
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

    # v1.0：按日期筛选（前端传 MM/DD，需转原始格式匹配 created_date）
    if selected_dates:
        date_patterns = []
        for d in selected_dates:
            parts = d.split('/')
            if len(parts) == 2:
                mm, dd = parts
                date_patterns.append(db.or_(
                    RawData.created_date.like(f'%/{mm}/{dd}'),
                    RawData.created_date.like(f'%{mm}{dd}')
                ))
        if date_patterns:
            raw_filter = raw_filter + [db.or_(*date_patterns)]

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
    if actual_assigned == 0:
        # 分配了 0 条：可能是请求数量为 0，或所有标注员额度已满
        return jsonify({'success': False, 'message': '无可分配数据（请求数量为 0 或所有标注员今日额度已用尽）'}), 400
    if actual_assigned < assign_count:
        return jsonify({
            'success': True,
            'message': f'成功分配 {actual_assigned} 条（请求 {assign_count} 条，部分实例可用数量不足）'
        })
    return jsonify({'success': True, 'message': f'成功分配 {actual_assigned} 条任务'})


def _today_assigned(annotator_id):
    """今日已分配给该标注员的总数量（含已完成）
    2026-05-31 修复：改用 DispatchLog.count 求和，替代 COUNT(RawData.id)，
    避免 assigned_at 字段时效性问题导致分配 18 条只统计到 1 条的问题。"""
    today_cutoff = bj_today_utc()  # 北京今日 00:00 UTC
    result = db.session.query(db.func.sum(DispatchLog.count)).filter(
        DispatchLog.annotator_id == annotator_id,
        DispatchLog.created_at >= today_cutoff,
    ).scalar()
    return result or 0


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
        # FIX: check_result 为 NULL 表示未标注（pending），'' 也算 pending；只有非空非 NULL 才算 completed
        def is_pending(r):
            return r.check_result is None or r.check_result == ''
        def is_completed(r):
            return r.check_result is not None and r.check_result != ''
        completed_count_calc = sum(1 for r in all_records if is_completed(r))
        revoked_pending = sum(1 for r in all_records if r.revoked_batch == bn and is_pending(r))
        non_revoked_pending = sum(1 for r in all_records if not r.revoked_batch and is_pending(r))
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
            display_total = len(all_records)  # 始终显示原始分配总数（如300），不变

        # ---- FIX Issue #1 & #3: per-annotator 统计 ----
        # 该 DispatchLog 对应的标注员的用户名
        log_annotator_name = log.annotator.username if log.annotator else ''
        # 仅属于该标注员的 RawData 记录（用于计算该人的完成数和分配数）
        per_annot_records = [r for r in all_records if r.annotator == log_annotator_name] if log_annotator_name else []
        per_annot_completed = sum(1 for r in per_annot_records if is_completed(r))  # FIX: 正确处理 NULL
        per_annot_pending = sum(1 for r in per_annot_records if is_pending(r))
        per_annot_count = len(per_annot_records)  # 该标注员在此批次的总分配数

        # 【修复 DISP-20260527-001 跳转失败】
        # 如果当前用户是标注员且该批次对其没有可见记录，跳过不显示
        # （如 REGEN 撤回后该用户记录全部清空的情况）
        current_user_obj = current_user
        if current_user_obj.role == 'annotator' and per_annot_count == 0:
            continue

        items.append({
            'batch_no': bn,
            'rule_name': log.rule_name or '',
            'admin_name': log.admin.name or log.admin.username if log.admin else '',
            'created_at': fmt_bj(log.created_at),
            'assign_method': log.assign_method or '',
            'total_count': per_annot_count,        # 本标注员分配数
            'completed_count': per_annot_completed, # 本标注员完成数
            'pending_count': per_annot_pending,     # 本标注员待标注数
            'revoked_count': 0,
            'status': 'done' if per_annot_pending == 0 else 'active',
            'display_total': per_annot_count,
            'isDone': per_annot_pending == 0,
            'isPartiallyRevoked': False,
            'annotators': [{
                'log_id': log_id,
                'annotator_name': log_annotator_name,
                'count': per_annot_count,
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
        # FIX: check_result 可能是 None（从未标注）或空字符串，不能用 == '' 判断
        # pending = check_result 为 None 或空字符串（从未标注）；已标注为 'correct'/'error'/'ignore'
        if rec.check_result in (None, ''):
            # 未完成（从未标注）→ 撤回：标记 revoked_batch，清空 annotator，退回待分配池
            rec.revoked_batch = batch_no
            rec.annotator = ''
            rec.dispatch_batch_no = None
            rec.task_status = 'unassigned'
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
        db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),  # FIX: 用 and_ 避免 annotator='' 泄露
    ] + annotator_filter

    # FIX Issue #2（revoked_batch 过滤修正）：
    # 部分撤回后，completed 记录保留 check_result 且 revoked_batch = batch_no，
    # pending 记录 revoked_batch = batch_no 且 annotator 被清空。
    # 正确逻辑：排除"此批次撤回的 pending 记录"，保留"此批次的 completed 记录"。
    has_batch_filter = bool(dispatch_batch_no)
    if has_batch_filter:
        if status_filter == 'done':
            # 仅已完成：包含该批次中所有已完成记录（含部分撤回后保留的）
            base.append(db.and_(RawData.check_result != '', RawData.check_result.isnot(None)))
        else:
            # pending 或全部：已完成永远可见；pending 仅显示从未被任何批次撤回的
            # 修复：仅对 pending 记录（check_result IS NULL）施加 revoked_batch IS NULL 过滤
            # 错误逻辑：check_result IS NOT NULL 对 pending(NULL) 返回 TRUE，导致 revoked 记录误入
            base.append(
                db.or_(
                    db.and_(RawData.check_result != '', RawData.check_result.isnot(None)),  # 已完成（correct/error/ignore）
                    db.and_(RawData.check_result.is_(None), RawData.revoked_batch.is_(None)),  # pending 且从未被任何批次撤回
                )
            )
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
    elif task_code_filter and task_code_filter.startswith('DISP-'):
        # task_code 的值实际是 dispatch_batch_no（由 task-group 虚拟键产生）
        base.append(RawData.dispatch_batch_no == task_code_filter)

    if task_code_filter and not (dispatch_batch_no and task_code_filter == dispatch_batch_no):
        # task_code 和 dispatch_batch_no 相同时只用一个过滤条件
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

    # check_result（correct/error/ignore）：精细标注状态过滤，与 status 互斥
    check_result = request.args.get('check_result', '').strip()
    if check_result:
        # 有 check_result 时跳过原有的 pending/done 逻辑
        if check_result == 'correct':
            base.append(RawData.check_result == '正确')
        elif check_result == 'error':
            base.append(RawData.check_result == '错误')
        elif check_result == 'ignore':
            base.append(RawData.check_result == '忽略')
    else:
        # 原有 pending/done 逻辑（兼容旧调用方）
        if status_filter == 'pending':
            base.append(db.or_(RawData.check_result == '', RawData.check_result.is_(None)))
        elif status_filter == 'done':
            base.append(db.or_(RawData.check_result != '', RawData.check_result.isnot(None)))

    # ai_result：AI审核结果（合规/违规）
    ai_result = request.args.get('ai_result', '').strip()
    if ai_result == '合规':
        base.append(RawData.ai_result.in_(['合规', '1', 'PASS']))
    elif ai_result == '违规':
        base.append(RawData.ai_result.in_(['违规', '0', 'REJECT']))

    # annotator：标注人精确匹配
    annotator = request.args.get('annotator', '').strip()
    if annotator:
        base.append(RawData.annotator == annotator)

    # keyword：商品名称或ID模糊搜索
    keyword = request.args.get('keyword', '').strip()
    if keyword:
        from sqlalchemy import or_
        base.append(or_(
            RawData.product_name.contains(keyword),
            RawData.product_id.contains(keyword)
        ))

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
            'product_link': r.product_link or '',
            'shop_name': r.shop_name or '',
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
        db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),  # FIX: 用 and_ 避免 annotator='' 泄露
    ] + annotator_filter

    # FIX Issue #2（revoked_batch 过滤修正）：与 api_my_tasks 保持一致
    # 已完成记录永远可见（不可撤回）；仅 pending 记录受 revoked_batch 过滤
    if dispatch_batch_filter:
        # 修复：OR 的第二分支应仅针对 pending 记录（check_result IS NULL 且 revoked_batch IS NULL）
        # 错误逻辑：check_result IS NOT NULL 对 pending(NULL) 返回 TRUE，导致 revoked 记录误入 pending
        # 正确逻辑：pending = check_result IS NULL AND revoked_batch IS NULL（从未被任何批次撤回）
        base.append(
            db.or_(
                db.and_(RawData.check_result != '', RawData.check_result.isnot(None)),  # 已完成（correct/error/ignore）：永远可见
                db.and_(RawData.check_result.is_(None), RawData.revoked_batch.is_(None)),  # pending 且从未被任何批次撤回
            )
        )
    else:
        # 无 batch_no 过滤：排除所有已撤回批次的记录（无论 pending 或 completed）
        # revoked_batch == '' → 从未被撤回的记录（可见）
        # revoked_batch != '' → 曾被撤回（隐藏）
        base.append(db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)))

    if instance_filter:
        base.append(RawData.instance_code == instance_filter)

    # 修复：从任务调度模块跳转时，后端按 dispatch_batch_no 精确过滤（问题3）
    if dispatch_batch_filter:
        base.append(RawData.dispatch_batch_no == dispatch_batch_filter)

    if rule_filter:
        from services.fetch_service import get_instance_rule_mapping
        instance_rule_map = get_instance_rule_mapping()
        matched_instances = [inst for inst, rule in instance_rule_map.items() if rule == rule_filter]
        if matched_instances:
            from sqlalchemy import or_
            base.append(or_(*[RawData.instance_code == inst for inst in matched_instances]))

    # check_result：精细标注状态过滤（correct/error/ignore → 中文值）
    check_result = request.args.get('check_result', '').strip()
    if check_result == 'correct':
        base.append(RawData.check_result == '正确')
    elif check_result == 'error':
        base.append(RawData.check_result == '错误')
    elif check_result == 'ignore':
        base.append(RawData.check_result == '忽略')

    # 加载 instance→rule 映射（用于显示规则名）
    from services.fetch_service import get_instance_rule_mapping
    instance_rule_map = get_instance_rule_mapping()

    # 查询所有匹配的记录（用于前端分组；最多查 5000 条）
    records = RawData.query.filter(*base).order_by(RawData.id.desc()).limit(5000).all()

    # 按 task_code 分组（无 task_code 则按 dispatch_batch_no 分组，不再生成虚拟 ANN-VIRTUAL）
    groups = {}
    for r in records:
        key = r.task_code
        if not key:
            key = r.dispatch_batch_no or f'DISP-UNASSIGNED-{r.id}'
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
                'my_pending_count': 0,        # 当前用户在该批次的 pending 条数（进入标注时你会看到多少条）
            }
        g = groups[key]
        g['total_count'] += 1

        # 统计当前用户在该批次的 pending 条数（用于批次卡片"你的份额"提示）
        # FIX: pending = check_result 为空（None 或空字符串），与 item 列表的"待标注"定义保持一致
        # 不再依赖 revoked_batch 判断（revoked 后该记录会被 item 列表过滤掉，但 pending_count 可能已混入）
        if not r.check_result:
            if current_user.role == 'admin' or r.annotator == current_user.username:
                g['my_pending_count'] = g.get('my_pending_count', 0) + 1

        # 判断 AI 结果分类：改用 ai_result 字段，与 api_annotation_stats（task-stats）保持一致
        # 兼容多种表示：合规/1/PASS/pass → 合规；违规/0/REJECT/fail → 违规
        COMPLIANT_VALUES  = {'合规', '1', 'PASS', 'pass'}
        VIOLATION_VALUES = {'违规', '0', 'REJECT', 'fail'}
        ai_result_val = str(r.ai_result or '').strip()
        ai_is_violation = ai_result_val in VIOLATION_VALUES

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
            'my_pending_count': g.get('my_pending_count', 0),  # 当前用户在该批次的 pending 条数（进入标注时你会看到）
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
    # v2.0+: 保留原始分配人（叶雨的任务被 admin 标注后，annotator 仍为"叶雨"）
    # 仅当原 annotator 为空时，才写入当前标注人（支持无主任务首次被标注的情况）
    # Annotation 表的 annotator_id 已正确记录实际标注人（Line 1099）
    if not rec.annotator:
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
def api_dispatch_annotator_load():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    # 管理员返回所有标注员+管理员（admin 也可作为标注任务执行者）；标注员只返回自己
    if current_user.role == 'admin':
        users = User.query.filter(User.role == 'annotator', User.is_active == True).all()
    else:
        users = [current_user]

    result = []
    for u in users:
        # 修复：用 bj_today_utc() 与 assigned_at/created_at（UTC）保持一致
        # date.today() = UTC date，但北京时间 00:00~08:00 时 UTC 日期比北京少一天
        bj_today_dt = bj_today_utc()
        bj_today_date = (bj_today_dt + timedelta(hours=8)).date()
        # 今日已分配：DispatchLog.created_at >= bj今日00:00 UTC
        today_assigned = DispatchLog.query.filter(
            DispatchLog.annotator_id == u.id,
            DispatchLog.created_at >= bj_today_dt
        ).count()
        # 今日已完成：Annotation.updated_at >= bj今日00:00 UTC
        today_completed = Annotation.query.filter_by(annotator_id=u.id, is_submitted=True).filter(
            Annotation.updated_at >= bj_today_dt
        ).count()

        result.append({
            'id': u.id,
            'username': u.username,
            'display_name': u.name or u.username,
            'today_assigned': today_assigned,
            'today_completed': today_completed,
            'quota': u.daily_quota or 100,
            'used': today_assigned,   # used ≈ 今日已分配
        })

    return jsonify({'success': True, 'items': result, 'data': result})


# ---------------------------------------------------------------------------
# API-7: GET /api/annotation/task-stats
# 返回标注批次级别的统计数字（全量，不走分页）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/annotation/task-stats', methods=['GET'])
@login_required
def api_annotation_stats():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    dispatch_batch_no = request.args.get('dispatch_batch_no', '').strip()
    instance_code = request.args.get('instance_code', '').strip()
    annotator = request.args.get('annotator', '').strip()

    # 基础过滤：同 api_my_tasks 的权限和数据可见性规则
    if current_user.role == 'admin':
        annotator_filter = []
    else:
        annotator_filter = [RawData.annotator == current_user.username]

    base = [
        RawData.modelb_reviewed == True,
        db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),
    ] + annotator_filter

    # 排除已撤回的批次
    base.append(db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)))

    if dispatch_batch_no:
        base.append(RawData.dispatch_batch_no == dispatch_batch_no)
    if instance_code:
        base.append(RawData.instance_code == instance_code)
    if annotator:
        base.append(RawData.annotator == annotator)

    records = RawData.query.filter(*base).all()

    # 统计计数
    total = len(records)
    pending = sum(1 for r in records if not r.check_result)
    annotated = total - pending
    correct = sum(1 for r in records if r.check_result == 'correct')
    error = sum(1 for r in records if r.check_result == 'error')
    ignore = sum(1 for r in records if r.check_result == 'ignore')

    # 整体准确率 = 正确数 / (正确数 + 错误数)，忽略不计入
    annotated_effective = correct + error
    accuracy = round(correct / annotated_effective, 4) if annotated_effective > 0 else None

    # 按 AI 结果分组（v1.0：改用 ai_result 字段，与前端口径统一；兼容多种表示）
    COMPLIANT_VALUES = {'合规', '1', 'PASS', 'pass'}
    VIOLATION_VALUES = {'违规', '0', 'REJECT', 'fail'}
    compliant_records = [r for r in records if str(r.ai_result or '').strip() in COMPLIANT_VALUES]
    violation_records = [r for r in records if str(r.ai_result or '').strip() in VIOLATION_VALUES]

    comp_correct = sum(1 for r in compliant_records if r.check_result == 'correct')
    comp_error   = sum(1 for r in compliant_records if r.check_result == 'error')
    comp_effective = comp_correct + comp_error
    compliant_accuracy = round(comp_correct / comp_effective, 4) if comp_effective > 0 else None

    viol_correct = sum(1 for r in violation_records if r.check_result == 'correct')
    viol_error   = sum(1 for r in violation_records if r.check_result == 'error')
    viol_effective = viol_correct + viol_error
    non_compliant_accuracy = round(viol_correct / viol_effective, 4) if viol_effective > 0 else None

    # 今日指标（标注员视角：个人数据；管理员视角：全局数据）
    today_assigned = 0
    today_remaining = 0
    today_completed = 0
    today_accuracy = None
    today_progress = None
    today_annotator_count = 0  # 管理员用：今日参与标注人数
    if current_user.role == 'admin':
        # 全局今日标注统计
        from datetime import timedelta
        bj_today = (datetime.utcnow() + timedelta(hours=8)).date()
        bj_today_start_utc = datetime.combine(bj_today, datetime.min.time()) - timedelta(hours=8)
        bj_today_end_utc = datetime.combine(bj_today, datetime.max.time()) - timedelta(hours=8)

        # 今日分配总数（所有批次）
        today_assigned = db.session.query(db.func.sum(DispatchLog.count)).filter(
            DispatchLog.created_at >= bj_today_start_utc,
            DispatchLog.created_at <= bj_today_end_utc,
        ).scalar() or 0

        # 今日提交标注总数 + 参与人数
        today_ann_q = db.session.query(Annotation).filter(
            Annotation.is_submitted == True,
            Annotation.created_at >= bj_today_start_utc,
            Annotation.created_at <= bj_today_end_utc,
        )
        today_annotated = today_ann_q.count()
        today_annotator_count = db.session.query(
            db.func.count(db.distinct(Annotation.annotator_id))
        ).filter(
            Annotation.is_submitted == True,
            Annotation.created_at >= bj_today_start_utc,
            Annotation.created_at <= bj_today_end_utc,
        ).scalar() or 0

    elif current_user.role == 'annotator':
        from datetime import timedelta
        bj_today = (datetime.utcnow() + timedelta(hours=8)).date()
        bj_today_start_utc = datetime.combine(bj_today, datetime.min.time()) - timedelta(hours=8)
        bj_today_end_utc = datetime.combine(bj_today, datetime.max.time()) - timedelta(hours=8)

        today_assigned = _today_assigned(current_user.id)
        quota = current_user.daily_quota or 200
        today_remaining = max(0, quota - today_assigned)

        # 今日已完成（含正确/错误/忽略）
        today_ann = db.session.query(Annotation).join(
            RawData, Annotation.raw_data_id == RawData.id
        ).filter(
            Annotation.annotator_id == current_user.id,
            Annotation.is_submitted == True,
            Annotation.created_at >= bj_today_start_utc,
            Annotation.created_at <= bj_today_end_utc,
        ).all()
        today_completed = len(today_ann)
        today_correct = sum(1 for r in today_ann if r.result == 'correct')
        today_error = sum(1 for r in today_ann if r.result == 'error')
        today_accuracy = round(today_correct / (today_correct + today_error) * 100, 1) \
            if (today_correct + today_error) > 0 else None
        today_progress = round(today_completed / quota * 100, 1) if quota > 0 else None

    # progress_percent：批次完成度百分比
    progress_percent = round(annotated / total * 100, 1) if total > 0 else None

    # today_annotated：管理员用全局今日标注数（标注员时沿用个人 today_completed）
    if current_user.role == 'admin':
        today_annotated = today_annotated  # 已在 admin 分支赋值
    else:
        today_annotated = today_completed  # 标注员：个人今日完成数

    return jsonify({
        'success': True,
        # v3.0 重命名 + 新增字段
        'total_tasks': total,
        'pending_tasks': pending,
        'completed_tasks': annotated,
        'progress_percent': progress_percent,
        'correct_count': correct,
        'error_count': error,
        'ignore_count': ignore,
        'overall_accuracy': round(accuracy * 100, 1) if accuracy is not None else None,  # 小数→百分比
        'compliant_accuracy': round(compliant_accuracy * 100, 1) if compliant_accuracy is not None else None,
        'non_compliant_accuracy': round(non_compliant_accuracy * 100, 1) if non_compliant_accuracy is not None else None,
        # 今日指标（v3.0）
        'today_annotated': today_annotated,      # 今日标注总数
        'today_assigned': today_assigned,        # 今日分配总数
        'today_annotator_count': today_annotator_count,  # 今日参与人数（管理员）
        'today_remaining': today_remaining,
        'today_completed': today_completed,
        'today_accuracy': today_accuracy,
        'today_progress': today_progress,
    })


# API-8: GET /api/annotation/daily-progress
# 返回近 N 天每日标注完成数趋势（标注员用）
# ---------------------------------------------------------------------------
@dispatch_bp.route('/api/annotation/daily-progress', methods=['GET'])
@login_required
def api_daily_progress():
    if current_user.role not in ('annotator', 'admin'):
        return jsonify({'success': False, 'error': '权限不足'}), 403

    days = int(request.args.get('days', 7))
    days = min(days, 90)

    # 使用北京时间
    from datetime import timedelta
    bj_now = datetime.utcnow() + timedelta(hours=8)
    bj_today = bj_now.date()
    start_date = bj_today - timedelta(days=days - 1)

    start_utc = datetime.combine(start_date, datetime.min.time()) - timedelta(hours=8)
    end_utc = datetime.combine(bj_today, datetime.max.time()) - timedelta(hours=8)

    # 查询每日完成数
    daily_counts = db.session.query(
        db.func.date(Annotation.created_at + timedelta(hours=8)).label('date'),
        db.func.count(Annotation.id).label('count')
    ).filter(
        Annotation.annotator_id == current_user.id,
        Annotation.is_submitted == True,
        Annotation.created_at >= start_utc,
        Annotation.created_at <= end_utc,
    ).group_by(
        db.func.date(Annotation.created_at + timedelta(hours=8))
    ).order_by(
        db.func.date(Annotation.created_at + timedelta(hours=8))
    ).all()

    trend_map = {str(r.date): r.count for r in daily_counts}
    trend = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        trend.append({'date': d_str, 'count': trend_map.get(d_str, 0)})

    return jsonify({'success': True, 'trend': trend})


def api_annotator_load():
    today_start = datetime.combine(date.today(), datetime.min.time())

    # 标注员只能查自己；管理员查所有人（标注员+管理员，admin 也可作为标注任务执行者）
    if current_user.role == 'annotator':
        annotators = [current_user]
    elif current_user.role == 'admin':
        annotators = User.query.filter(
            User.role.in_(['annotator', 'admin']),
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
# 生成标注任务（v1.4 新增，v1.1 checkbox 多选模式重构）
# 接收 batch_id + rule_name + instance + sample_percent + categories（数组）
# v1.1 五分类定义：
#   diff          = modelb_reviewed=True  AND modelb_consistent=False
#   missed        = modelb_reviewed=False AND id < max_reviewed_id（并发被跳过）
#   unreviewable  = modelb_reviewed=True  AND modelb_result='无法审核'
#   consistency   = modelb_reviewed=True  AND modelb_consistent=True AND modelb_result!='无法审核'
#   un-reviewed   = modelb_reviewed=False AND id >= max_reviewed_id（仍在排队，不入任何分类）
#
# categories 数组（默认 ['diff','missed','unreviewable','consistency']）：
#   传入 categories 时：精确控制纳入哪些类别
#   不传 categories（兼容旧版）：fallback 到 data_mode 逻辑
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/generate-tasks', methods=['POST'])
@login_required
def api_generate_tasks():
    """生成标注任务（管理员手动触发）

    v1.1 五分类数据定义（checkbox 多选模式）：
      diff          = modelb_reviewed=True  AND modelb_consistent=False
      missed        = modelb_reviewed=False AND id < max_reviewed_id（并发被跳过）
      unreviewable  = modelb_reviewed=True  AND modelb_result='无法审核'
      consistency   = modelb_reviewed=True  AND modelb_consistent=True AND modelb_result!='无法审核'
      un-reviewed   = modelb_reviewed=False AND id >= max_reviewed_id（仍排队，不入任何分类）

    categories 参数（数组，默认 ['diff','missed','unreviewable','consistency']）：
      传入 categories 时：精确控制纳入哪些类别
      不传 categories（兼容旧版）：fallback 到 data_mode 逻辑
    """
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足，仅管理员可操作'}), 403

    data = request.get_json() or {}
    batch_id = str(data.get('batch_id', '')).strip()
    rule_name = str(data.get('rule_name', '')).strip()
    instance_code = str(data.get('instance', '')).strip() or None
    sample_percent = float(data.get('sample_percent', 5.0))
    categories = data.get('categories')   # v1.1: 数组，默认后端兜底
    data_mode_legacy = str(data.get('data_mode', 'all')).lower()

    # v1.1 修复：有 categories 数组 → preview（用户点开弹窗看计数）
    # 旧版兼容：data_mode=preview 时也走 preview
    is_preview = (categories is not None) or (data_mode_legacy == 'preview')

    if not batch_id:
        return jsonify({'success': False, 'error': 'batch_id 不能为空'}), 400
    if not is_preview and not rule_name:
        return jsonify({'success': False, 'error': '规则名不能为空'}), 400
    if not is_preview and (sample_percent < 0 or sample_percent > 100):
        return jsonify({'success': False, 'error': '抽检比例需在 0~100 之间'}), 400

    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({'success': False, 'error': '批次不存在'}), 404

    if not is_preview and fetch_log.review_status not in ('completed', 'aborted'):
        return jsonify({'success': False, 'error': '该批次互检尚未开始，无法生成标注任务'}), 400

    import logging
    import random
    logger = logging.getLogger('werkzeug')
    logger.error(f'[GenerateTasks] batch_id={batch_id} categories={categories} sample_percent={sample_percent}')

    # ========== 核心：计算 max_reviewed_id 作为漏审边界 ==========
    max_reviewed_id = db.session.query(db.func.max(RawData.id)).filter(
        RawData.fetch_batch_id == batch_id,
        RawData.modelb_reviewed == True,
    ).scalar()

    base_filter = [RawData.fetch_batch_id == batch_id]
    if instance_code:
        base_filter.append(RawData.instance_code == instance_code)

    # 差异数据（已互检且 A/B 不一致，排除无法审核）
    diff_filter = base_filter + [
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == False,
        RawData.modelb_result != '无法审核',   # v1.1：无法审核不计入差异数据
    ]
    diff_count = RawData.query.filter(*diff_filter).count()

    # 漏审数据：modelb_reviewed=False 且 id < max_reviewed_id
    missed_filter = base_filter + [RawData.modelb_reviewed == False]
    if max_reviewed_id is not None:
        missed_filter.append(RawData.id < max_reviewed_id)
    missed_count = RawData.query.filter(*missed_filter).count()

    # 无法审核数据（已互检，modelb_result='无法审核'）
    unreviewable_filter = base_filter + [
        RawData.modelb_reviewed == True,
        RawData.modelb_result == '无法审核',
    ]
    unreviewable_count = RawData.query.filter(*unreviewable_filter).count()

    # 一致性数据（已互检且 A/B 一致，排除无法审核）
    consistency_filter = base_filter + [
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == True,
    ]
    consistency_all = RawData.query.filter(*consistency_filter).all()
    consistency_total = len(consistency_all)

    # 按比例抽样
    if sample_percent > 0 and sample_percent < 100:
        threshold = sample_percent / 100.0
        sampled_records = [r for r in consistency_all if (r.random_num or random.random()) < threshold]
        consistency_sampled = len(sampled_records)
    elif sample_percent >= 100:
        sampled_records = consistency_all
        consistency_sampled = len(sampled_records)
    else:
        sampled_records = []
        consistency_sampled = 0

    logger.error(f'[GenerateTasks v1.1] max_reviewed_id={max_reviewed_id} '
                  f'差异={diff_count} 漏审={missed_count} 无法审核={unreviewable_count} '
                  f'一致性抽样={consistency_sampled}/{consistency_total}(阈值={sample_percent}%)')

    # ========== preview 模式：仅返回计数，不修改数据 ==========
    if is_preview:
        return jsonify({
            'success': True,
            'preview': True,
            'diff_count': diff_count,
            'missed_count': missed_count,
            'unreviewable_count': unreviewable_count,      # v1.1 新增
            'consistency_total': consistency_total,
            'consistency_sampled': consistency_sampled,
            'sample_percent': sample_percent,
            'max_reviewed_id': max_reviewed_id,
        })

    # ========== v1.1 执行模式：根据 categories 数组处理数据 ==========
    # 默认包含全部类别（diff/missed/unreviewable/consistency）
    cats = categories if isinstance(categories, list) else ['diff', 'missed', 'unreviewable', 'consistency']

    updated_missed = 0
    updated_unreviewable = 0

    # 1. 漏审数据：标记 modelb_reviewed=True
    if 'missed' in cats:
        missed_records = RawData.query.filter(*missed_filter).all()
        for rec in missed_records:
            rec.modelb_reviewed = True
            rec.modelb_result = '漏审'
            rec.modelb_reason = '漏审-并发跳过'
            rec.modelb_consistent = None
        updated_missed = len(missed_records)
        logger.error(f'[GenerateTasks v1.1] 标记 {updated_missed} 条漏审数据 modelb_reviewed=True')

    # 2. 无法审核数据（无需标记，已在池中）
    updated_unreviewable = unreviewable_count if 'unreviewable' in cats else 0

    # 3. 差异数据（无需修改字段）
    active_diff_count = diff_count if 'diff' in cats else 0

    # 4. 一致性数据（已在上方按比例抽取）
    active_consistency_sampled = consistency_sampled if 'consistency' in cats else 0

    # 更新 FetchLog
    fetch_log.task_generated = True
    fetch_log.task_generate_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    fetch_log.task_sample_percent = sample_percent

    db.session.commit()

    total_entered = active_diff_count + updated_missed + updated_unreviewable + active_consistency_sampled

    return jsonify({
        'success': True,
        'message': '生成成功',
        'diff_count': active_diff_count,
        'missed_count': updated_missed,
        'unreviewable_count': updated_unreviewable,
        'consistency_sampled': active_consistency_sampled,
        'consistency_total': consistency_total,
        'sample_percent': sample_percent,
        'total_entered': total_entered,
        'task_generated': True,
        'task_generate_time': fetch_log.task_generate_time,
        'task_sample_percent': sample_percent,
        'categories': cats,
    })


# ---------------------------------------------------------------------------
# API-8.5: POST /api/dispatch/regenerate-task-pool（v3.1）
# 智能撤回未被领取的任务，并重新生成任务池条目
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/regenerate-task-pool', methods=['POST'])
@login_required
def api_regenerate_task_pool():
    """重新生成任务池（智能撤回 + 重新生成）

    v1.1 五分类（checkbox 多选模式）：
      Step 1 - 智能撤回：已分配但未标注的任务撤回
      Step 2 - 重新生成：根据 categories 数组纳入数据

    categories 参数（数组，默认 ['diff','missed','unreviewable','consistency']）：
      diff          → 差异数据（modelb_consistent=False，已互检）
      missed        → 漏审数据（并发跳过）
      unreviewable  → 无法审核数据（modelb_result='无法审核'）
      consistency   → 一致性数据（按比例抽样）
    """
    if current_user.role != 'admin':
        return jsonify({'success': False, 'error': '权限不足，仅管理员可操作'}), 403

    data = request.get_json() or {}
    batch_id = str(data.get('batch_id', '')).strip()
    rule_name = str(data.get('rule_name', '')).strip()
    instance_code = str(data.get('instance', '')).strip() or None
    sample_percent = float(data.get('sample_percent', 5.0))
    categories = data.get('categories')  # v1.1: 数组

    if not batch_id:
        return jsonify({'success': False, 'error': 'batch_id 不能为空'}), 400

    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({'success': False, 'error': '批次不存在'}), 404

    cats = categories if isinstance(categories, list) else ['diff', 'missed', 'unreviewable', 'consistency']

    import logging
    logger = logging.getLogger('werkzeug')
    logger.error(f'[RegenerateTaskPool] batch_id={batch_id} categories={cats} sample_percent={sample_percent}')

    # ========== Step 1：智能撤回 ==========
    revoke_filter = db.and_(
        RawData.task_status == 'assigned',
        db.and_(RawData.annotator != '', RawData.annotator.isnot(None)),
        db.or_(RawData.check_result == '', RawData.check_result.is_(None)),
        db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
        RawData.modelb_reviewed == False,
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
        rec.task_status = 'unassigned'

    logger.error(f'[RegenerateTaskPool] 撤回 {revoked_count} 条')

    # ========== Step 2：重新生成（按 categories 纳入）==========
    base_filter = [RawData.fetch_batch_id == batch_id]
    if instance_code:
        base_filter.append(RawData.instance_code == instance_code)

    # 差异数据
    diff_filter = base_filter + [
        RawData.modelb_reviewed == True,
        RawData.modelb_consistent == False,
        RawData.modelb_result != '无法审核',   # v1.1：无法审核不计入差异数据
    ]
    active_diff_count = RawData.query.filter(*diff_filter).count() if 'diff' in cats else 0

    # 无法审核数据
    unreviewable_filter = base_filter + [RawData.modelb_reviewed == True, RawData.modelb_result == '无法审核']
    active_unreviewable = RawData.query.filter(*unreviewable_filter).count() if 'unreviewable' in cats else 0

    # 漏审数据
    missed_filter = base_filter + [RawData.modelb_reviewed == False]
    if 'missed' in cats:
        missed_records = RawData.query.filter(*missed_filter).all()
        for rec in missed_records:
            rec.modelb_reviewed = True
            rec.modelb_result = '漏审'
            rec.modelb_reason = '漏审-重新生成'
            rec.modelb_consistent = None
        updated_missed = len(missed_records)
    else:
        updated_missed = 0

    # 一致性数据抽样
    consistent_filter = base_filter + [RawData.modelb_reviewed == True, RawData.modelb_consistent == True]
    import random
    if 'consistency' in cats:
        consistent_all = RawData.query.filter(*consistent_filter).all()
        if sample_percent > 0 and sample_percent < 100:
            threshold = sample_percent / 100.0
            sampled_records = [r for r in consistent_all if (r.random_num or random.random()) < threshold]
            active_sampled = len(sampled_records)
        elif sample_percent >= 100:
            sampled_records = consistent_all
            active_sampled = len(sampled_records)
        else:
            sampled_records = []
            active_sampled = 0
    else:
        sampled_records = []
        active_sampled = 0

    fetch_log.task_generated = True
    if not fetch_log.task_generate_time:
        fetch_log.task_generate_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if 'missed' in cats:
        fetch_log.review_status = 'pending'

    db.session.commit()

    pool_filter = [
        RawData.fetch_batch_id == batch_id,
        db.or_(RawData.revoked_batch == '', RawData.revoked_batch.is_(None)),
        db.or_(
            RawData.task_status == 'assigned',
            db.and_(RawData.task_status == '', RawData.task_status.isnot(None)),
            RawData.task_status.is_(None),
        ),
        RawData.modelb_reviewed == False,
    ]
    if instance_code:
        pool_filter.append(RawData.instance_code == instance_code)
    total_in_pool = RawData.query.filter(*pool_filter).count()

    total_reentered = active_diff_count + updated_missed + active_unreviewable + active_sampled
    logger.error(f'[RegenerateTaskPool] 完成: 撤回={revoked_count}, 重新纳入={total_reentered}, 任务池={total_in_pool}')

    return jsonify({
        'success': True,
        'revoked_count': revoked_count,
        'regenerated_diff': active_diff_count,
        'regenerated_missed': updated_missed,
        'regenerated_unreviewable': active_unreviewable,
        'regenerated_sampled': active_sampled,
        'total_in_pool': total_in_pool,
        'categories': cats,
        'message': f'重新生成成功。撤回 {revoked_count} 条，重新纳入 {total_reentered} 条。',
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


# ---------------------------------------------------------------------------
# API-10: GET /api/dispatch/available-dates
# 获取指定规则下所有待分配数据的数据日期及其数量（用于分配弹窗日期筛选）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/available-dates', methods=['GET'])
@login_required
def api_available_dates():
    from sqlalchemy import func, or_ as sql_or
    from services.fetch_service import get_instance_rule_mapping
    rule_name = request.args.get('rule_name', '').strip()

    # 获取该规则绑定的实例列表
    instance_rule_map = get_instance_rule_mapping()
    matched_instances = [inst for inst, r in instance_rule_map.items() if r == rule_name]

    # 待分配记录过滤条件（与 api_task_pool / api_assign 保持一致）
    base_filter = [
        RawData.modelb_reviewed == True,
        sql_or(RawData.annotator == '', RawData.annotator.is_(None)),
        sql_or(RawData.check_result == '', RawData.check_result.is_(None)),
    ]
    if matched_instances:
        raw_filter = base_filter + [sql_or(*[RawData.instance_code == inst for inst in matched_instances])]
    else:
        raw_filter = base_filter

    # 按 created_date 分组计数（支持 YYYY/MM/DD 和 YYYYMMDD 两种格式）
    # 统一归一化为 MM/DD 格式返回
    records = RawData.query.filter(*raw_filter).all()

    date_count_map = {}
    for r in records:
        cd = r.created_date
        if not cd:
            continue
        # 归一化为 MM/DD
        if '/' in cd:
            parts = cd.split('/')
            if len(parts) == 3:
                mm, dd = parts[1], parts[2]
            else:
                continue
        else:
            if len(cd) == 8:
                mm, dd = cd[4:6], cd[6:8]
            else:
                continue
        key = f"{mm}/{dd}"
        date_count_map[key] = date_count_map.get(key, 0) + 1

    dates = [{'date': k, 'count': v} for k, v in sorted(date_count_map.items())]
    return jsonify({'success': True, 'dates': dates})


# ---------------------------------------------------------------------------
# API-11: GET /api/dispatch/today-assigned
# 获取今日从 DispatchLog 统计的分配总量（概览卡片用）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/today-assigned', methods=['GET'])
@login_required
def api_today_assigned():
    today_cutoff = bj_today_utc()
    today_assigned = db.session.query(db.func.sum(DispatchLog.count)).filter(
        DispatchLog.created_at >= today_cutoff,
    ).scalar() or 0
    return jsonify({'success': True, 'today_assigned': today_assigned})


# ---------------------------------------------------------------------------
# API-12: GET /api/dispatch/allocated-trend
# 获取近 N 天每日分配趋势（趋势图用）
# ---------------------------------------------------------------------------

@dispatch_bp.route('/api/dispatch/allocated-trend', methods=['GET'])
@login_required
def api_allocated_trend():
    days = int(request.args.get('days', 7))
    days = min(days, 90)  # 最多90天

    today_cutoff = bj_today_utc()
    start_date = today_cutoff - timedelta(days=days - 1)

    # 按 created_at 日期分组统计
    results = db.session.query(
        db.func.date(DispatchLog.created_at).label('date'),
        db.func.sum(DispatchLog.count).label('count'),
    ).filter(
        DispatchLog.created_at >= start_date,
    ).group_by(
        db.func.date(DispatchLog.created_at)
    ).order_by(db.func.date(DispatchLog.created_at)).all()

    # 补全缺失日期（当天无分配则 count=0）
    trend_map = {str(r.date): r.count for r in results}
    trend = []
    for i in range(days):
        d = start_date + timedelta(days=i)
        d_str = d.strftime('%Y-%m-%d')
        trend.append({'date': d_str, 'count': trend_map.get(d_str, 0)})

    return jsonify({'success': True, 'trend': trend})
