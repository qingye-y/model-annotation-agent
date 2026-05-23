# -*- coding: utf-8 -*-
"""数据获取服务模块"""

import re
import json
import random
import hashlib
import requests
from datetime import datetime, timedelta
from config import ENV_CONFIG, FETCH_PAGE_SIZE, IDATA_COOKIE as DEFAULT_COOKIE, IDATA_DATASOURCE_TYPE


def get_idata_cookie():
    """获取 iData 的 Cookie（无参数版本，与蓝图兼容）

    从数据库获取 Cookie，优先数据库，没有则返回默认 Cookie
    """
    from models import SqlConfig

    # 从数据库读取 key='IDATA_COOKIE' 的配置
    config = SqlConfig.query.filter_by(key='IDATA_COOKIE').first()
    if config and config.value:
        return config.value

    # 回退到默认 Cookie
    return DEFAULT_COOKIE


def replace_params(sql_template, params):
    """替换 SQL 模板中的参数"""
    sql = sql_template
    for key, value in params.items():
        placeholder = '${' + key + '}'
        sql = sql.replace(placeholder, str(value))
    return sql


def get_pipeline_sqls(env):
    """读取指定环境的取数管道配置（FetchPipeline + SqlTemplate），按 sort_order 返回已启用的 SQL 列表

    Args:
        env: 环境名（云环境 / 乐采云环境）

    Returns:
        List[dict] — 每步信息：{id, step_name, sort_order, enabled, sql_template_id, sql_text, category}
    """
    from models import FetchPipeline, SqlTemplate

    pipelines = FetchPipeline.query.filter_by(env=env, enabled=True)\
        .order_by(FetchPipeline.sort_order).all()

    result = []
    for p in pipelines:
        tpl = p.sql_template
        result.append({
            'id': p.id,
            'step_name': p.step_name,
            'sort_order': p.sort_order,
            'enabled': p.enabled,
            'sql_template_id': p.sql_template_id,
            'sql_text': tpl.sql_text if tpl else None,
            'category': tpl.category if tpl else None,
        })
    return result


def get_pipeline_sql_by_category(env, category):
    """按 category 查单条管道 SQL

    Args:
        env: 环境名
        category: SQL 类别（count/daily/sample/reason/detail）

    Returns:
        dict — {sql_text, category} 或 None
    """
    from models import FetchPipeline, SqlTemplate

    p = FetchPipeline.query.join(SqlTemplate)\
        .filter(FetchPipeline.env == env,
                FetchPipeline.enabled == True,
                SqlTemplate.category == category)\
        .order_by(FetchPipeline.sort_order).first()

    if not p or not p.sql_template:
        return None
    return {
        'sql_text': p.sql_template.sql_text,
        'category': p.sql_template.category,
    }


def replace_pipeline_params(sql_template, **kwargs):
    """统一替换管道 SQL 模板中的占位符

    Args:
        sql_template: 原始 SQL 模板字符串（含 {detail_sql} / {positions} 等占位符）
        **kwargs: key=value 占位符键值对

    Returns:
        替换后的 SQL 字符串
    """
    result = sql_template
    for key, val in kwargs.items():
        placeholder = '{' + key + '}'
        result = result.replace(placeholder, str(val))
    return result


def build_sql(instance, start_date, end_date):
    """构建完整的 SQL 查询语句"""
    from models import SqlTemplate

    # 获取默认 SQL 模板
    template_obj = SqlTemplate.query.filter(
        SqlTemplate.instances.like(f'%{instance}%')
    ).first()

    if not template_obj:
        # 使用备用 SQL
        sql = f"""
        select
          shop_id as "供应商id",
          `标签`,
          cast(r.ai_result_id as varchar) as "AI审核id",
          cast(r.app_id as varchar) as "审核id",
          cast(r.goods_id as varchar) as "商品id",
          IF(r.ai_result = 1, '合规', '违规') as "AI审核结果",
          case
            when r.audit_status = 0 then '待审核'
            when r.audit_status = 3 then '审核驳回'
            when r.audit_status = 4 then '审核通过'
            when r.audit_status = 5 then '商家撤回'
          end as "审核单结果",
          d.reject_field as "人审拒绝项",
          d.reject_detail as "拒绝原因",
          r.audit_idea as "人审意见",
          r.reject_reason as "AI拒绝原因",
          r.reject_detail as "AI拒绝解释",
          shop_name as "店铺名称",
          r.item_name as "商品名称",
          r.category_name as "类目",
          r.main_imgs as "主图",
          r.detail_imgs as "详情图",
          r.sku_imgs as "sku图",
          r.spu_imgs as "spu图",
          case when r.instance_code in ('GXLCY','YNLCY')
            then concat('https://www.lecaiyun.com/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
            when r.instance_code in ('HWCS','ZJWC','HNLCWC')
            then concat('https://www.zcygov.cn/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
          end as "商品链接",
          r.check_result as "标注结果：1=正确  0=错误",
          r.annotation as "备注",
          r.instance_code as "实例编码",
          date_format(r.gmt_created_time,'%Y/%m/%d') as "创建日期",
          '' as "标注人",
          RAND() as "随机数",
          `变更类别`,
          r.gmt_created_time as "创建时间"
        from dwd.dwd_itm_audit_app_ai_result_detail_inc_y r
        left join dwd.dwd_itm_audit_reject_detail_y d on r.app_id = d.app_id and d.pt = '{year}'
        LEFT JOIN (
          select
            instance_code,
            cast(item_id as varchar) as id,
            cast(shop_id as varchar) as shop_id,
            shop_name,
            CASE when publish_channel = 1 then 'PCWEB'
              when publish_channel = 3 then '开放平台'
              when publish_channel = 17 then '商家引用创建'
              when publish_channel = 18 then '页面添加卖场发布'
            end "标签"
          from dim.dim_itm_basic_info_detail_d
          where instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
        ) c on r.goods_id = c.id and r.instance_code = c.instance_code
        left join (
          select
            item_id,
            array_join(array_distinct(array_agg(audit_type_name)), ',') as "变更类别"
          from dwd.dwd_itm_audit_detail_inc_y
          where pt = '{year}'
          and instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
          and substr(cast(gmt_created_time as varchar),1,10) between '{start_date}' and '{end_date}'
          and audit_status_name <> '已撤回'
          group by 1
        ) as new on new.item_id = r.goods_id
        where date_format(r.gmt_created_time,'%Y%m%d') between '{start_date}' and '{end_date}'
        and r.pt = '{year}'
        and r.instance_code = '{instance}'
        """
    else:
        sql = template_obj.sql_text

    # 替换参数
    year = start_date[:4] if start_date else '2026'
    sql = replace_params(sql, {
        'start_date': start_date,
        'end_date': end_date,
        'instance': instance,
        'year': year
    })

    print(f"[DEBUG 审计] build_sql 完成: instance={instance}, range={start_date}~{end_date}, year={year}")
    print(f"[DEBUG 审计] 生成的基础SQL (前500字符):\n{sql[:500]}")
    return sql


def execute_sql_query(sql, instance, env):
    """执行 iData SQL 查询（env 为必填参数，与蓝图兼容）"""
    from models import SqlConfig

    # 获取环境配置
    if env is None:
        # 根据实例判断环境
        env = '乐采云环境' if instance in ['YNLCY', 'GXLCY'] else '云环境'

    env_config = ENV_CONFIG.get(env, {})
    query_api_url = env_config.get('query_api_url')

    if not query_api_url:
        return {'error': f'未找到环境 {env} 的 API URL'}

    # 获取 Cookie（无参数版本）
    cookie = get_idata_cookie()

    # 构建请求：以完整的 cookie 字符串作为 HTTP Cookie header（与原始成功代码一致）
    # payload 中不传 cookie 字段
    headers = {'Content-Type': 'application/json', 'Cookie': cookie}

    payload = {
        'sql': sql,
        'instance': instance,
        'datasourceType': IDATA_DATASOURCE_TYPE
    }

    try:
        resp = requests.post(
            query_api_url,
            json=payload,
            headers=headers,
            timeout=300
        )
        resp.raise_for_status()
        result = resp.json()

        # iData 返回格式：{success: true, data: {values: [...], count: N, headers: [...]}}
        if isinstance(result, dict) and result.get('success') is False:
            error_msg = result.get('error', result.get('message', 'iData 接口返回错误'))
            return {'error': error_msg}

        # 正确解析 data 中的 values 数组
        if isinstance(result, dict) and 'data' in result:
            data_obj = result['data']
            if isinstance(data_obj, dict) and 'values' in data_obj:
                return data_obj['values']
            return data_obj
        elif 'data' in result:
            return result['data']
        elif 'result' in result:
            return result['result']
        return result

    except requests.exceptions.RequestException as e:
        return {'error': str(e)}


def extract_violation_keywords(reject_reason):
    """从拒绝原因JSON中提取违规标签

    处理逻辑：
    - 解析JSON，提取所有value
    - 对每个value按 | 分割，对每个片段按规则从上到下匹配
    - 优先级：规则列表从上到下，先匹配到的优先
    - 无任何匹配时返回 ['其他']

    规则来源：提示词_V24 + 线上50,000条违规记录探查结果（2026-05-15）
    """
    import re

    if not reject_reason:
        return []

    # ========== 解析JSON，提取所有value片段 ==========
    segments = []
    raw_str = str(reject_reason).strip()

    if raw_str.startswith('{'):
        try:
            parsed = json.loads(raw_str)
            if isinstance(parsed, dict):
                for val in parsed.values():
                    val_str = str(val or '').strip()
                    if val_str and val_str not in ('None', '无', '空'):
                        # 按 | 分割（部分AI返回多级原因用|分隔）
                        for seg in val_str.split('|'):
                            seg = seg.strip()
                            if seg:
                                segments.append(seg)
        except (json.JSONDecodeError, TypeError, ValueError):
            segments.append(raw_str)
    else:
        for seg in raw_str.split('|'):
            seg = seg.strip()
            if seg:
                segments.append(seg)

    if not segments:
        return []

    # ========== 规则定义（按优先级从高到低排列）==========
    # 格式：(标签名, [关键词列表])
    # 只要片段中包含任意一个关键词，即匹配该标签
    RULES = [
        # 一、资质类（党徽国旗、政治敏感标志）
        ('特殊资质缺失', ['国旗', '党徽', '国徽', '党旗', '资质', '需提供', '批准', '文件']),
        # 二、图片质量类（水印违规）
        ('水印', ['水印', '商贸', '商城', '贸易', '科技', '智汇选']),
        ('马赛克', ['马赛克']),
        ('盗图', ['盗图']),
        # 三、信息不一致类
        ('类目错放', ['类目错放', '类目', '末级', '匹配']),
        ('图文不一致', ['图文', '不一致', '不符', '一致', '实际']),
        ('销售属性错误', ['销售属性', '属性', '销售', '数量', '颜色']),
        ('SKU图不一致', ['张图', 'sku', '配件']),
        ('关键属性不一致', ['参数', '规格', '型号', '尺寸']),
        # 四、内容违规类
        ('站外引流', ['引流', '京东', '旗舰店', '淘宝', '天猫', '联系方式', '微信号', '电话', '抖音', '二维码', '优惠', '客服', '积分', '促销', '供应链']),
        ('无关信息', ['无关']),
        ('多主体', ['多主体', '主体', '多个', '未以', '为主', '不明']),
        ('商品清单', ['清单', '表格']),
        ('品类词堆砌', ['堆砌', '混放']),
        # 五、禁售限制类
        ('禁售商品', ['禁售', '限制', '当前', '网超', '消防', '雨衣', '乐器', '证书', '定制', '垃圾桶']),
        # 六、内容质量类（编码/格式）
        ('标题无关词', ['词语', '违禁词', '生僻字', '繁体字', '字符', '乱码', '数字串', '字符串', '特殊符号', '意义']),
        ('AI生成', ['ai生成', '疑似虚假', '虚假']),
        # 七、特殊品类
        ('书籍版权页', ['版权页', '书籍', 'isbn', '出版']),
        # 八、兜底（已在上面处理，此处不重复规则）
    ]

    matched = []
    matched_labels = set()

    for seg in segments:
        seg_lower = seg.lower()
        for label, kws in RULES:
            if label in matched_labels:
                continue  # 已匹配过的标签跳过
            for kw in kws:
                if kw in seg_lower:
                    matched.append(label)
                    matched_labels.add(label)
                    break  # 找到即停，不重复追加

    # 兜底：无任何匹配
    if not matched:
        return ['其他']

    # 去重：同一标签最多计1次（跨 segments 去重 + 确保无重复追加）
    deduplicated = list(dict.fromkeys(matched))
    return deduplicated  # 返回去重后的标签列表


def extract_error_reason(reject_reason):
    """提取简短错误原因（用于摘要展示）"""
    if not reject_reason:
        return ''

    # 取前20个字符
    reason = reject_reason.strip()[:20]
    return reason


def generate_daily_stats(instance, start_date, end_date, batch_id,
                         original_total=0, original_compliant=0, original_non_compliant=0,
                         error_reasons=None, error_reasons_by_date=None,
                         daily_counts_by_date=None):
    """按日期+实例维度生成统计快照（基于 RawData 实际业务日期分组）

    数据来源（核心原则）：
    - 从 RawData 查询该批次+实例的所有记录
    - 按 created_date 字段分组，统计每天的 total / compliant / non_compliant
    - daily_counts_by_date（推荐）：若传入（来自 iData GROUP BY 日期查询），
      则直接使用准确的每日计数，不依赖比例分配
    - 无 daily_counts_by_date 时：iData original_total/compliant/non_compliant
      按各天 RawData 占比等比例分配到每天（估算法，不如 daily_counts 精确）
    - error_reasons：若传入 error_reasons_by_date（来自 iData 线上全量），
      则直接使用，不再从 RawData 解析；
      若未传入，则从 RawData 每条记录的 ai_reject_reason JSON 解析提取

    写入规则：
    - 唯一约束：(stat_date, instance_code, batch_id)
    - 有旧记录 → 覆盖所有字段
    - 无旧记录 → 新建
    """
    from models import db, DailyStats, RawData

    if original_total <= 0:
        print(f"[DailyStats][DEBUG 审计] 跳过: original_total={original_total}, 无有效数据")
        return

    print(f"[DailyStats][DEBUG 审计] 开始: instance={instance}, range={start_date}~{end_date}, "
          f"batch={batch_id}, original_total={original_total}, "
          f"original_compliant={original_compliant}, original_non_compliant={original_non_compliant}, "
          f"has_error_reasons_by_date={'YES' if error_reasons_by_date else 'NO'}")

    # ========== 第一步：从 RawData 按 created_date 实际分组统计 ==========
    raw_records = RawData.query.filter(
        RawData.fetch_batch_id == batch_id,
        RawData.instance_code == instance
    ).all()
    print(f"[DailyStats][DEBUG 审计] RawData 查询结果: batch={batch_id}, instance={instance}, 共{len(raw_records)}条记录")

    # ========== 第一步：从 RawData 按 created_date 实际分组统计 ==========
    # created_date 格式：'2026/05/03' 或 '20260503'
    raw_records = RawData.query.filter(
        RawData.fetch_batch_id == batch_id,
        RawData.instance_code == instance
    ).all()

    # 按 created_date 分组统计
    # created_date 格式可能是 '2026/05/03'（带斜杠）或 '20260503'（纯数字）
    daily_raw_stats = {}  # {date_str_normalized: {'total': N, 'compliant': N, 'non_compliant': N, 'reasons': {reason: count}}}
    total_raw = 0

    for r in raw_records:
        raw_date = str(r.created_date or '').strip()
        if not raw_date:
            continue

        # 统一格式：将 '2026/05/03' 转为 '20260503'
        date_str = raw_date.replace('/', '').replace('-', '')
        if len(date_str) != 8:
            continue

        if date_str not in daily_raw_stats:
            daily_raw_stats[date_str] = {
                'total': 0, 'compliant': 0, 'non_compliant': 0, 'reasons': {}
            }

        daily_raw_stats[date_str]['total'] += 1
        ai_res = str(r.ai_result or '').strip()
        if ai_res in ('合规', '1', 'PASS'):
            daily_raw_stats[date_str]['compliant'] += 1
        elif ai_res in ('违规', '0', 'REJECT'):
            daily_raw_stats[date_str]['non_compliant'] += 1
            # 解析 ai_reject_reason JSON，提取关键词（从key映射）
            raw_reject = str(r.ai_reject_reason or '').strip()
            if raw_reject and raw_reject not in ('None', '无', '空', ''):
                # 使用extract_violation_keywords提取key作为关键词
                keywords = extract_violation_keywords(raw_reject)
                if keywords:
                    # 每个标签单独计数，不合并
                    for kw in keywords:
                        daily_raw_stats[date_str]['reasons'][kw] = \
                            daily_raw_stats[date_str]['reasons'].get(kw, 0) + 1
                else:
                    # 如果无法解析key，再尝试解析value作为后备
                    try:
                        parsed = json.loads(raw_reject)
                        if isinstance(parsed, dict):
                            for field, reason in parsed.items():
                                reason_str = str(reason or '').strip()
                                if reason_str and reason_str not in ('None', '无', '空'):
                                    reason_key = reason_str[:15]
                                    daily_raw_stats[date_str]['reasons'][reason_key] = \
                                        daily_raw_stats[date_str]['reasons'].get(reason_key, 0) + 1
                                    break
                            else:
                                daily_raw_stats[date_str]['reasons']['其他'] = \
                                    daily_raw_stats[date_str]['reasons'].get('其他', 0) + 1
                        else:
                            daily_raw_stats[date_str]['reasons']['其他'] = \
                                daily_raw_stats[date_str]['reasons'].get('其他', 0) + 1
                    except (json.JSONDecodeError, TypeError, ValueError):
                        daily_raw_stats[date_str]['reasons']['其他'] = \
                            daily_raw_stats[date_str]['reasons'].get('其他', 0) + 1

        total_raw += 1

    if not daily_raw_stats:
        print(f"[DailyStats] 警告: RawData 中无有效记录 (batch={batch_id}, inst={instance})")
        # 如果有线上按日期分区的 error_reasons，按日期写入
        if error_reasons_by_date:
            date_keys = sorted(error_reasons_by_date.keys())
            n_days = len(date_keys)
            if n_days > 0:
                base_total = original_total // n_days
                base_compliant = original_compliant // n_days
                base_non_compliant = original_non_compliant // n_days
                remainder_total = original_total - base_total * n_days
                remainder_compliant = original_compliant - base_compliant * n_days
                remainder_non_compliant = original_non_compliant - base_non_compliant * n_days

                for i, date_str in enumerate(date_keys):
                    # 前 n_days-1 天用 base，最后一天补齐余数
                    rec_total = base_total + (remainder_total if i == n_days - 1 else 0)
                    rec_compliant = base_compliant + (remainder_compliant if i == n_days - 1 else 0)
                    rec_non_compliant = base_non_compliant + (remainder_non_compliant if i == n_days - 1 else 0)
                    day_reasons = error_reasons_by_date[date_str]
                    _upsert_daily_stat(db, DailyStats, date_str, instance, batch_id,
                                       rec_total, rec_compliant, rec_non_compliant, day_reasons)

                db.session.commit()
                print(f"[DailyStats] 使用线上数据写入 {n_days} 天（RawData 为空）: batch={batch_id}")
                return

        # Fallback: 创建或更新占位记录（无线上分区数据）
        if original_total > 0:
            from models import DailyStats as DailyStatsModel
            daily_stat = DailyStatsModel.query.filter_by(
                stat_date=start_date,
                instance_code=instance
            ).first()
            if daily_stat:
                daily_stat.total_count = original_total
                daily_stat.compliant_count = original_compliant
                daily_stat.non_compliant_count = original_non_compliant
                daily_stat.batch_id = batch_id
                daily_stat.error_reasons = '{}'
            else:
                daily_stat = DailyStatsModel(
                    stat_date=start_date,
                    instance_code=instance,
                    batch_id=batch_id,
                    total_count=original_total,
                    compliant_count=original_compliant,
                    non_compliant_count=original_non_compliant,
                    error_reasons='{}'
                )
                db.session.add(daily_stat)
        else:
            return
        db.session.commit()
        return

    print(f"[DailyStats] RawData 实际分布: {len(daily_raw_stats)} 天, raw_total={total_raw}")

    # ========== 第二步：确定每日计数 ==========
    # 优先使用 iData 按日 GROUP BY 的精确计数（daily_counts_by_date）
    # 否则按 RawData 占比等比例分配 iData 全量（估算法）
    for date_str, day_data in daily_raw_stats.items():
        if daily_counts_by_date and date_str in daily_counts_by_date:
            # 使用 iData 按日精确计数
            day_count = daily_counts_by_date[date_str]
            rec_total = day_count.get('total', 0)
            rec_compliant = day_count.get('compliant', 0)
            rec_non_compliant = day_count.get('non_compliant', 0)
        else:
            # 按比例分配 iData 全量（估算法，不精确）
            raw_day_total = day_data['total']
            if total_raw > 0:
                ratio = raw_day_total / total_raw
                rec_total = round(original_total * ratio)
                rec_compliant = round(original_compliant * ratio)
                rec_non_compliant = round(original_non_compliant * ratio)
            else:
                rec_total = rec_compliant = rec_non_compliant = 0

        # error_reasons：优先使用线上全量按日期分区数据
        # 关键：只要有线上数据（error_reasons_by_date 非空），就必须用线上数据
        # 不允许 fallback 到 RawData 抽样数据（抽样会导致 reasons 远小于 non_compliant_count）
        if error_reasons_by_date and len(error_reasons_by_date) > 0:
            # 该日期有数据 → 用线上数据；该日期无数据 → 用空字典（不 fallback 到 RawData）
            day_reasons = error_reasons_by_date.get(date_str, {})
            if date_str in error_reasons_by_date:
                print(f"[DailyStats] {date_str}: 使用线上全量 reasons ({len(day_reasons)} 个标签)")
            else:
                print(f"[DailyStats] {date_str}: 线上无数据，写入空 reasons（不 fallback 到 RawData 抽样）")
        else:
            # 只有在完全没有线上数据时，才 fallback 到 RawData 当天实际统计
            day_reasons = day_data['reasons']
            print(f"[DailyStats] {date_str}: 无线上数据，使用 RawData reasons")

        _upsert_daily_stat(db, DailyStats, date_str, instance, batch_id,
                           rec_total, rec_compliant, rec_non_compliant, day_reasons)

    db.session.commit()
    print(f"[DailyStats] 生成完成: {len(daily_raw_stats)} 天, original_total={original_total}, batch={batch_id}")


def _upsert_daily_stat(db, DailyStats, date_str, instance, batch_id,
                       rec_total, rec_compliant, rec_non_compliant, day_reasons):
    """Upsert 一条 DailyStats 记录

    注意：数据库实际约束为 UNIQUE(stat_date, instance_code)，非 (stat_date, instance_code, batch_id)。
    查询时按 (stat_date, instance_code) 两列查找（不以 batch_id 为条件），
    找到后覆盖所有字段（batch_id 也更新为新值）。
    """
    daily_stat = DailyStats.query.filter(
        DailyStats.stat_date == date_str,
        DailyStats.instance_code == instance
    ).first()

    if daily_stat:
        daily_stat.batch_id = batch_id
        daily_stat.total_count = rec_total
        daily_stat.compliant_count = rec_compliant
        daily_stat.non_compliant_count = rec_non_compliant
        # 合并 error_reasons（累加到已有）
        existing_reasons = {}
        if daily_stat.error_reasons:
            try:
                existing_reasons = json.loads(daily_stat.error_reasons)
            except (json.JSONDecodeError, TypeError):
                existing_reasons = {}
        merged_reasons = dict(existing_reasons)
        for tag, count in day_reasons.items():
            merged_reasons[tag] = merged_reasons.get(tag, 0) + count
        daily_stat.error_reasons = json.dumps(merged_reasons, ensure_ascii=False) if merged_reasons else None
        daily_stat.updated_at = datetime.utcnow()
    else:
        daily_stat = DailyStats(
            stat_date=date_str,
            instance_code=instance,
            batch_id=batch_id,
            total_count=rec_total,
            compliant_count=rec_compliant,
            non_compliant_count=rec_non_compliant,
            error_reasons=json.dumps(day_reasons, ensure_ascii=False) if day_reasons else None
        )
        db.session.add(daily_stat)


def update_daily_stats_inconsistency(fetch_log):
    """互检完成后，将不一致数据按业务日期拆分写入 DailyStats

    逻辑规则：
    - 找到同一 (stat_date + instance_code) 的所有已有记录（所有批次）
    - 将新批次的不一致数按 total_count 比例分配到各批次记录
    - 如果没有任何记录，则新建一条（total=0 的占位记录）
    - 不清零任何已有字段，只累加 inconsistent_count
    """
    from models import db, DailyStats
    from datetime import datetime, timedelta

    batch_id = fetch_log.batch_id
    start = fetch_log.data_start_date
    end = fetch_log.data_end_date
    inconsistent_total = fetch_log.inconsistent_count or 0
    instances = fetch_log.instances.split(',') if fetch_log.instances else []

    if not start or not end or inconsistent_total == 0:
        print(f"[DailyStats] 跳过: 无日期或无不一致数据 (batch={batch_id})")
        return

    try:
        start_dt = datetime.strptime(start, '%Y%m%d')
        end_dt = datetime.strptime(end, '%Y%m%d')
    except ValueError as e:
        print(f"[DailyStats] 日期解析失败: {e} (batch={batch_id})")
        return

    days = (end_dt - start_dt).days + 1
    per_day = inconsistent_total // days
    remainder = inconsistent_total % days

    print(f"[DailyStats] 开始写入不一致数据: batch={batch_id}, "
          f"范围={start}~{end}, 总数={inconsistent_total}, 天数={days}, 每天={per_day}, 余数={remainder}")

    for i in range(days):
        date = start_dt + timedelta(days=i)
        date_str = date.strftime('%Y%m%d')
        count = per_day + (1 if i < remainder else 0)

        for instance in instances:
            instance = instance.strip()
            if not instance:
                continue

            # 找到该日期+实例下的所有批次记录
            all_stats = DailyStats.query.filter(
                DailyStats.stat_date == date_str,
                DailyStats.instance_code == instance
            ).all()

            if not all_stats:
                # 没有任何记录：新建一条占位记录
                stats = DailyStats(
                    stat_date=date_str,
                    instance_code=instance,
                    batch_id=batch_id,
                    total_count=0,
                    compliant_count=0,
                    non_compliant_count=0,
                    inconsistent_count=count
                )
                db.session.add(stats)
                print(f"[DailyStats] 新建占位: date={date_str}, inst={instance}, inconsistent={count}")
            else:
                # 已有记录：计算总不一致数（已有 + 新增）
                existing_inconsistent = sum((s.inconsistent_count or 0) for s in all_stats)
                total_inconsistent = existing_inconsistent + count

                # 计算各批次的 total_count 总和（用于按比例分配）
                total_weight = sum((s.total_count or 0) for s in all_stats)

                if total_weight > 0:
                    # 按 total_count 比例分配到各批次
                    for s in all_stats:
                        weight = s.total_count or 0
                        old_inconsistent = s.inconsistent_count or 0
                        s.inconsistent_count = old_inconsistent + int(count * weight / total_weight)
                        s.inconsistent_rate = round(s.inconsistent_count / weight * 100, 2) if weight > 0 else 0.0
                        s.batch_id = batch_id  # 更新为最新批次
                else:
                    # 各批次 total=0，平均分配
                    per_record = total_inconsistent // len(all_stats)
                    rem = total_inconsistent % len(all_stats)
                    for idx, s in enumerate(all_stats):
                        extra = 1 if idx < rem else 0
                        s.inconsistent_count = per_record + extra
                        s.inconsistent_rate = 0.0
                        s.batch_id = batch_id

                print(f"[DailyStats] 累加不一致: date={date_str}, inst={instance}, "
                      f"旧合计={existing_inconsistent} + 新增={count} = 总计={total_inconsistent}, "
                      f"涉及{len(all_stats)}条批次记录")

    db.session.commit()
    print(f"[DailyStats] 不一致数据写入完成: batch={batch_id}")


def get_instance_rule_mapping():
    """从配置中获取实例与规则的映射关系"""
    from models import SqlConfig
    import re as _re

    config = SqlConfig.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
    if not config or not config.value:
        return {}

    try:
        mapping = json.loads(config.value)
        # 归一化：去掉 .md/.txt 后缀
        normalized = {}
        for inst, rule in mapping.items():
            normalized[inst] = _re.sub(r'\.(md|txt)$', '', rule)
        return normalized
    except:
        return {}


def ping_idata(env, instance):
    """探活请求：发送轻量SQL验证 Cookie 有效性
    
    返回 (success: bool, error_msg: str)
    - Cookie有效 → 返回True
    - Cookie失效/无权限/连接失败 → 返回False及错误原因
    """
    probe_sql = "SELECT 1 as probe"
    try:
        result = execute_sql_query(probe_sql, instance, env)
        # execute_sql_query 返回 {'error': '...'} 时表示请求失败
        if isinstance(result, dict) and 'error' in result:
            return False, result['error']
        # 返回空列表也可能表示认证失效
        if isinstance(result, list) and len(result) == 0:
            # 再用带实例的探活确认（避免偏远日期真实无数据误判）
            probe_sql2 = f"SELECT 1 as probe FROM dwd.dwd_itm_audit_app_ai_result_detail_inc_y WHERE instance_code='{instance}' LIMIT 1"
            result2 = execute_sql_query(probe_sql2, instance, env)
            if isinstance(result2, dict) and 'error' in result2:
                return False, result2['error']
            if isinstance(result2, list) and len(result2) == 0:
                return False, "Cookie失效或查询无权限（探活SQL返回空）"
        return True, ""
    except Exception as e:
        return False, str(e)


def fetch_data_from_idata(env, instance, start_date, end_date, sample_percent, excluded_audit_ids=None):
    """核心函数：从 iData 拉取数据，使用窗口函数翻页，随机抽样，返回结果

    Args:
        excluded_audit_ids: 已抽取的审核ID集合，用于增量抽样（排除已抽取的数据）
    """
    
    # ========== 探活：确认 Cookie 有效 ==========
    ping_ok, ping_msg = ping_idata(env, instance)
    if not ping_ok:
        raise RuntimeError(f"iData 认证失败：{ping_msg}。请检查 Cookie 是否过期。")
    
    # 增量抽样：格式化排除的ID集合
    excluded_ids_set = set(excluded_audit_ids) if excluded_audit_ids else set()
    excluded_ids_str = ','.join([f"'{aid}'" for aid in excluded_ids_set]) if excluded_ids_set else None
    excluded_sql = f"AND `审核id` NOT IN ({excluded_ids_str})" if excluded_ids_str else ""
    
    # 格式化日期（去除连字符，转为 YYYYMMDD）
    start_date_fmt = start_date.replace('-', '').replace('/', '') if start_date else ''
    end_date_fmt = end_date.replace('-', '').replace('/', '') if end_date else ''

    # 获取基础 detail SQL（从 SqlTemplate 或备用逻辑）
    detail_sql = build_sql(instance, start_date_fmt, end_date_fmt)
    # 重要修复（v2）：COUNT 查询必须用 COUNT(DISTINCT `审核id`) 而非 COUNT(*)，
    # 因为 detail_sql 中的 LEFT JOIN dwd_itm_audit_reject_detail_y 会让同一 app_id
    # 产生多条记录（同一审核单有多条驳回详情），COUNT(*) 把重复行也计入。
    # 验证结论：COUNT(DISTINCT app_id) = 5,588，完全对齐 iData 基准。
    # 增量抽样：排除已抽取的审核ID
    # ========== S2: COUNT 总数统计（从管道读取）==========
    count_template = get_pipeline_sql_by_category(env, 'count')
    if count_template:
        count_sql = replace_pipeline_params(count_template['sql_text'], detail_sql=detail_sql)
        # 注入 excluded_sql（替换 WHERE 1=1 后的位置）
        count_sql = count_sql.replace('WHERE 1=1', f'WHERE 1=1 {excluded_sql}')
    else:
        # 兜底：使用原有硬编码逻辑
        count_sql = f"""SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
            FROM ({detail_sql}) t
            WHERE 1=1 {excluded_sql}
            GROUP BY `AI审核结果`"""

    total_count = 0
    original_compliant = 0
    original_non_compliant = 0

    try:
        count_result = execute_sql_query(count_sql, instance, env)
        print(f"[DEBUG 审计] {instance} COUNT查询SQL:\n{count_sql}")
        print(f"[DEBUG 审计] {instance} COUNT原始返回: {count_result}")

        if not isinstance(count_result, list):
            count_result = [count_result]

        for row in count_result:
            if not isinstance(row, dict):
                continue
            
            status = ''
            count = 0
            
            for key in ['AI审核结果', 'ai_result', 'ai_audit_result', '审核结果', 'status']:
                if key in row:
                    status = str(row[key] or '')
                    break
            
            for key in ['count', 'cnt', 'COUNT(*)', 'TOTAL']:
                if key in row:
                    count = int(row[key] or 0)
                    break
            
            total_count += count
            
            if status == '合规' or status == '1' or status == 'PASS':
                original_compliant += count
            elif status == '违规' or status == '0' or status == 'REJECT':
                original_non_compliant += count
            else:
                print(f"[WARN] 实例 {instance} 未知状态: {status}, 计数: {count}")

        print(f"[DEBUG] 实例 {instance} 解析: 总数={total_count}, 合规={original_compliant}, 违规={original_non_compliant}")
    except Exception as e:
        print(f"[ERROR] 实例 {instance} 查询总数失败: {e}")
        raise

    # error_reasons 统计移到调用方（在 data_fetch.py 中使用 RawData 统计）
    error_reasons = {}

    if total_count == 0:
        return {
            "fetched_data": [],
            "total_fetched": 0,
            "compliant_count": 0,
            "non_compliant_count": 0,
            "original_total": 0,
            "original_compliant": 0,
            "original_non_compliant": 0,
            "error_reasons": error_reasons
        }

    # ========== 分层抽样（保持合规/违规比例与线上全量一致）==========
    #
    # 通俗说明：假设线上 10000 条，其中违规 2000 条（20%）。抽 10% 时：
    #   - 从 8000 合规里抽 800 条
    #   - 从 2000 违规里抽 200 条
    #   → 最终样本中违规率也是 20%，与线上完全一致
    #
    # 技术细节：
    #   ① 先 COUNT GROUP BY AI审核结果 查询线上合规/违规各自总数
    #   ② 合规层：max(1, int(original_compliant × pct / 100)) 条
    #   ③ 违规层：max(1, int(original_non_compliant × pct / 100)) 条
    #   ④ 两层分别用 MD5(instance+date+'_C'/'_N') 作固定种子，生成不重复随机行号
    #   ⑤ 各层 SQL：WHERE AI审核结果='合规'/'违规' + ROW_NUMBER() OVER(ORDER BY MD5(审核id)) + WHERE rn IN (...)
    #   ⑥ 合并写入 RawData；original_compliant/original_non_compliant 保持不变（记录的是线上全量数字）
    #
    # 复现：种子固定，同参数两次拉取审核id完全一致
    compliant_sample_count = max(1, int(original_compliant * sample_percent / 100)) if original_compliant > 0 else 0
    non_compliant_sample_count = max(1, int(original_non_compliant * sample_percent / 100)) if original_non_compliant > 0 else 0
    
    # 补偿 LEFT JOIN 重复行导致的抽样损耗：拉取 1.5x，Python 端去重后截断
    OVERSAMPLE = 1.5
    compliant_pull_count = max(1, int(compliant_sample_count * OVERSAMPLE)) if compliant_sample_count > 0 else 0
    non_compliant_pull_count = max(1, int(non_compliant_sample_count * OVERSAMPLE)) if non_compliant_sample_count > 0 else 0
    # 拉取数不能超过线上总数
    compliant_pull_count = min(compliant_pull_count, original_compliant) if original_compliant > 0 else 0
    non_compliant_pull_count = min(non_compliant_pull_count, original_non_compliant) if original_non_compliant > 0 else 0
    
    target_count = compliant_sample_count + non_compliant_sample_count
    sample_count = compliant_pull_count + non_compliant_pull_count
    print(f"[DEBUG 审计] {instance} 分层抽样: 合规={original_compliant}→抽{compliant_sample_count}→拉{compliant_pull_count}, 违规={original_non_compliant}→抽{non_compliant_sample_count}→拉{non_compliant_pull_count}, 目标={target_count}, 拉取={sample_count}")

    # 种子1：合规抽样（MD5(instance + date_range + 'C')，固定可复现）
    seed_str_c = f"{instance}_{start_date}_{end_date}_C"
    seed_c = int(hashlib.md5(seed_str_c.encode('utf-8')).hexdigest(), 16) % 100000
    # 种子2：违规抽样（MD5(instance + date_range + 'N')，避免两类抽样位置产生关联）
    seed_str_n = f"{instance}_{start_date}_{end_date}_N"
    seed_n = int(hashlib.md5(seed_str_n.encode('utf-8')).hexdigest(), 16) % 100000

    sampled_data = []

    # ---------- 合规数据抽样 ----------
    if compliant_pull_count > 0:
        random.seed(seed_c)
        if compliant_pull_count >= original_compliant:
            compliant_positions = list(range(1, original_compliant + 1))
        else:
            compliant_positions = sorted(random.sample(range(1, original_compliant + 1), compliant_pull_count))

        positions_str_c = ','.join(map(str, compliant_positions))
        # 分批查询（iData 限制单次返回约 500 行）
        batch_size = 300
        for i in range(0, len(compliant_positions), batch_size):
            batch_positions = compliant_positions[i:i+batch_size]
            batch_pos_str = ','.join(map(str, batch_positions))
            # ========== S3: 合规数据抽样（从管道读取）==========
            sample_tpl_c = get_pipeline_sql_by_category(env, 'sample')
            if sample_tpl_c:
                compliant_sql = replace_pipeline_params(
                    sample_tpl_c['sql_text'],
                    detail_sql=detail_sql,
                    positions=batch_pos_str
                )
                # 注入 excluded_sql
                compliant_sql = compliant_sql.replace('WHERE 1=1', f'WHERE 1=1 {excluded_sql}')
            else:
                # 兜底
                compliant_sql = f"""SELECT * FROM (
                  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
                  FROM (
                    {detail_sql}
                  ) t
                  WHERE 1=1 {excluded_sql}
                    AND t.`AI审核结果` = '合规'
                ) tmp
                WHERE tmp.rn IN ({batch_pos_str})"""

            try:
                rows_c = execute_sql_query(compliant_sql, instance, env)
                if isinstance(rows_c, list):
                    sampled_data.extend(rows_c)
                    print(f"[DEBUG 审计] {instance} 合规批次{i//batch_size+1}: 请求{len(batch_positions)}行, 返回{len(rows_c)}行")
                elif isinstance(rows_c, dict) and 'error' in rows_c:
                    print(f"[ERROR] {instance} 合规批次{i//batch_size+1}失败: {rows_c['error']}")
            except Exception as e:
                print(f"[ERROR] {instance} 合规批次{i//batch_size+1}异常: {e}")
                raise
        print(f"[DEBUG 审计] {instance} 合规抽检完成: 请求{len(compliant_positions)}行, 累计{len(sampled_data)}行")

    # ---------- 违规数据抽样 ----------
    if non_compliant_pull_count > 0:
        random.seed(seed_n)
        if non_compliant_pull_count >= original_non_compliant:
            non_compliant_positions = list(range(1, original_non_compliant + 1))
        else:
            non_compliant_positions = sorted(random.sample(range(1, original_non_compliant + 1), non_compliant_pull_count))

        positions_str_n = ','.join(map(str, non_compliant_positions))
        batch_size = 300
        for i in range(0, len(non_compliant_positions), batch_size):
            batch_positions = non_compliant_positions[i:i+batch_size]
            batch_pos_str = ','.join(map(str, batch_positions))
            # ========== S4: 违规数据抽样（从管道读取）==========
            sample_tpl_n = get_pipeline_sql_by_category(env, 'sample')
            if sample_tpl_n:
                non_compliant_sql = replace_pipeline_params(
                    sample_tpl_n['sql_text'],
                    detail_sql=detail_sql,
                    positions=batch_pos_str
                )
                # 注入 excluded_sql
                non_compliant_sql = non_compliant_sql.replace('WHERE 1=1', f'WHERE 1=1 {excluded_sql}')
            else:
                # 兜底
                non_compliant_sql = f"""SELECT * FROM (
                  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
                  FROM (
                    {detail_sql}
                  ) t
                  WHERE 1=1 {excluded_sql}
                    AND t.`AI审核结果` = '违规'
                ) tmp
                WHERE tmp.rn IN ({batch_pos_str})"""

            try:
                rows_n = execute_sql_query(non_compliant_sql, instance, env)
                if isinstance(rows_n, list):
                    sampled_data.extend(rows_n)
                    print(f"[DEBUG 审计] {instance} 违规批次{i//batch_size+1}: 请求{len(batch_positions)}行, 返回{len(rows_n)}行")
                elif isinstance(rows_n, dict) and 'error' in rows_n:
                    print(f"[ERROR] {instance} 违规批次{i//batch_size+1}失败: {rows_n['error']}")
            except Exception as e:
                print(f"[ERROR] {instance} 违规批次{i//batch_size+1}异常: {e}")
                raise
        print(f"[DEBUG 审计] {instance} 违规抽检完成: 请求{len(non_compliant_positions)}行, 累计{len(sampled_data)}行")

    print(f"[DEBUG 审计] {instance} 分层抽样完成: 拉取{len(sampled_data)}行, 目标{target_count}行")

    # 按审核id去重 + 按合规/违规分组截断（补偿 LEFT JOIN 重复行损耗）
    seen_ids = set()
    deduped = []
    cmpl_count = 0
    viol_count = 0
    for d in sampled_data:
        aid = str(d.get('审核id', d.get('audit_id', ''))) if isinstance(d, dict) else ''
        if not aid or aid in seen_ids:
            continue
        seen_ids.add(aid)
        val = str(d.get('AI审核结果', d.get('ai_result', ''))) if isinstance(d, dict) else ''
        if val == '合规' and cmpl_count < compliant_sample_count:
            deduped.append(d)
            cmpl_count += 1
        elif val == '违规' and viol_count < non_compliant_sample_count:
            deduped.append(d)
            viol_count += 1
        # 如果合规和违规都已凑齐，停止
        if cmpl_count >= compliant_sample_count and viol_count >= non_compliant_sample_count:
            break
    
    print(f"[DEBUG 审计] {instance} 去重截断: {len(sampled_data)}→{len(deduped)} (合规{cmpl_count}, 违规{viol_count})")
    if len(deduped) < target_count:
        print(f"[WARN] {instance} 抽样不足: 期望{target_count}, 实际{len(deduped)}, 差{target_count-len(deduped)}")

    return {
        "fetched_data": deduped,
        "total_fetched": len(deduped),
        "compliant_count": cmpl_count,
        "non_compliant_count": viol_count,
        "original_total": total_count,
        "original_compliant": original_compliant,
        "original_non_compliant": original_non_compliant,
        "error_reasons": error_reasons,
        "excluded_count": len(excluded_ids_set),  # 增量抽样：排除的ID数量
        "is_incremental": len(excluded_ids_set) > 0  # 是否为增量抽样
    }


def fetch_error_reasons_online(env, instance, start_date, end_date, max_records=None):
    """从 iData 线上全量统计违规原因分布（用于违规原因饼图）

    实现逻辑：
    1. 基于 detail_sql 构建聚合 SQL，用 CASE WHEN 实现关键词→标签映射
    2. 优先级规则与 extract_violation_keywords() 完全一致（从上到下）
    3. 一条聚合 SQL 返回「日期×标签」的计数，无需翻页和逐条处理
    4. 返回 {'by_date': {date_str: {reason: count}}, 'global': {reason: count}}

    规则来源：提示词_V24 + 线上50,000条违规记录探查结果（2026-05-15）
    """
    # ========== 探活：确认 Cookie 有效 ==========
    ping_ok, ping_msg = ping_idata(env, instance)
    if not ping_ok:
        raise RuntimeError(f"iData 认证失败：{ping_msg}。请检查 Cookie 是否过期。")
    
    start_date_fmt = start_date.replace('-', '').replace('/', '') if start_date else ''
    end_date_fmt = end_date.replace('-', '').replace('/', '') if end_date else ''

    # 构建基础 detail SQL
    detail_sql = build_sql(instance, start_date_fmt, end_date_fmt)

    # ========== 构建 CASE WHEN 规则（与 extract_violation_keywords() 完全一致）==========
    # 格式： WHEN `AI拒绝原因` LIKE '%kw1%' OR ... THEN '标签名'
    case_when = """CASE
    -- 一、资质类
    WHEN t.`AI拒绝原因` LIKE '%国旗%' OR t.`AI拒绝原因` LIKE '%党徽%' OR t.`AI拒绝原因` LIKE '%国徽%'
      OR t.`AI拒绝原因` LIKE '%党旗%' OR t.`AI拒绝原因` LIKE '%资质%'
      OR t.`AI拒绝原因` LIKE '%需提供%' OR t.`AI拒绝原因` LIKE '%批准%'
      OR t.`AI拒绝原因` LIKE '%文件%' THEN '特殊资质缺失'
    -- 二、图片质量类
    WHEN t.`AI拒绝原因` LIKE '%水印%' OR t.`AI拒绝原因` LIKE '%商贸%'
      OR t.`AI拒绝原因` LIKE '%商城%' OR t.`AI拒绝原因` LIKE '%贸易%'
      OR t.`AI拒绝原因` LIKE '%科技%' OR t.`AI拒绝原因` LIKE '%智汇选%' THEN '水印'
    WHEN t.`AI拒绝原因` LIKE '%马赛克%' THEN '马赛克'
    WHEN t.`AI拒绝原因` LIKE '%盗图%' THEN '盗图'
    -- 三、信息不一致类
    WHEN t.`AI拒绝原因` LIKE '%类目错放%' OR t.`AI拒绝原因` LIKE '%类目%'
      OR t.`AI拒绝原因` LIKE '%末级%' OR t.`AI拒绝原因` LIKE '%匹配%' THEN '类目错放'
    WHEN t.`AI拒绝原因` LIKE '%图文%' OR t.`AI拒绝原因` LIKE '%不一致%'
      OR t.`AI拒绝原因` LIKE '%不符%' OR t.`AI拒绝原因` LIKE '%一致%'
      OR t.`AI拒绝原因` LIKE '%实际%' THEN '图文不一致'
    WHEN t.`AI拒绝原因` LIKE '%销售属性%' OR t.`AI拒绝原因` LIKE '%属性%'
      OR t.`AI拒绝原因` LIKE '%销售%' OR t.`AI拒绝原因` LIKE '%数量%'
      OR t.`AI拒绝原因` LIKE '%颜色%' THEN '销售属性错误'
    WHEN t.`AI拒绝原因` LIKE '%张图%' OR t.`AI拒绝原因` LIKE '%sku%'
      OR t.`AI拒绝原因` LIKE '%配件%' THEN 'SKU图不一致'
    WHEN t.`AI拒绝原因` LIKE '%参数%' OR t.`AI拒绝原因` LIKE '%规格%'
      OR t.`AI拒绝原因` LIKE '%型号%' OR t.`AI拒绝原因` LIKE '%尺寸%' THEN '关键属性不一致'
    -- 四、内容违规类
    WHEN t.`AI拒绝原因` LIKE '%引流%' OR t.`AI拒绝原因` LIKE '%京东%'
      OR t.`AI拒绝原因` LIKE '%旗舰店%' OR t.`AI拒绝原因` LIKE '%淘宝%'
      OR t.`AI拒绝原因` LIKE '%天猫%' OR t.`AI拒绝原因` LIKE '%联系方式%'
      OR t.`AI拒绝原因` LIKE '%微信号%' OR t.`AI拒绝原因` LIKE '%电话%'
      OR t.`AI拒绝原因` LIKE '%抖音%' OR t.`AI拒绝原因` LIKE '%二维码%'
      OR t.`AI拒绝原因` LIKE '%优惠%' OR t.`AI拒绝原因` LIKE '%客服%'
      OR t.`AI拒绝原因` LIKE '%积分%' OR t.`AI拒绝原因` LIKE '%促销%'
      OR t.`AI拒绝原因` LIKE '%供应链%' THEN '站外引流'
    WHEN t.`AI拒绝原因` LIKE '%无关%' THEN '无关信息'
    WHEN t.`AI拒绝原因` LIKE '%多主体%' OR t.`AI拒绝原因` LIKE '%主体%'
      OR t.`AI拒绝原因` LIKE '%多个%' OR t.`AI拒绝原因` LIKE '%未以%'
      OR t.`AI拒绝原因` LIKE '%为主%' OR t.`AI拒绝原因` LIKE '%不明%' THEN '多主体'
    WHEN t.`AI拒绝原因` LIKE '%清单%' OR t.`AI拒绝原因` LIKE '%表格%' THEN '商品清单'
    WHEN t.`AI拒绝原因` LIKE '%堆砌%' OR t.`AI拒绝原因` LIKE '%混放%' THEN '品类词堆砌'
    -- 五、禁售限制类
    WHEN t.`AI拒绝原因` LIKE '%禁售%' OR t.`AI拒绝原因` LIKE '%限制%'
      OR t.`AI拒绝原因` LIKE '%当前%' OR t.`AI拒绝原因` LIKE '%网超%'
      OR t.`AI拒绝原因` LIKE '%消防%' OR t.`AI拒绝原因` LIKE '%雨衣%'
      OR t.`AI拒绝原因` LIKE '%乐器%' OR t.`AI拒绝原因` LIKE '%证书%'
      OR t.`AI拒绝原因` LIKE '%定制%' OR t.`AI拒绝原因` LIKE '%垃圾桶%' THEN '禁售商品'
    -- 六、内容质量类
    WHEN t.`AI拒绝原因` LIKE '%词语%' OR t.`AI拒绝原因` LIKE '%违禁词%'
      OR t.`AI拒绝原因` LIKE '%生僻字%' OR t.`AI拒绝原因` LIKE '%繁体字%'
      OR t.`AI拒绝原因` LIKE '%字符%' OR t.`AI拒绝原因` LIKE '%乱码%'
      OR t.`AI拒绝原因` LIKE '%数字串%' OR t.`AI拒绝原因` LIKE '%字符串%'
      OR t.`AI拒绝原因` LIKE '%特殊符号%' OR t.`AI拒绝原因` LIKE '%意义%' THEN '标题无关词'
    WHEN t.`AI拒绝原因` LIKE '%ai生成%' OR t.`AI拒绝原因` LIKE '%疑似虚假%'
      OR t.`AI拒绝原因` LIKE '%虚假%' THEN 'AI生成'
    -- 七、特殊品类
    WHEN t.`AI拒绝原因` LIKE '%版权页%' OR t.`AI拒绝原因` LIKE '%书籍%'
      OR t.`AI拒绝原因` LIKE '%isbn%' OR t.`AI拒绝原因` LIKE '%出版%' THEN '书籍版权页'
    -- 八、兜底
    ELSE '其他'
  END"""

    # ========== 构建聚合 SQL（一次查询得到所有日期×标签的计数）==========
    # 重要修复（v2）：内层先按 (审核id, 创建日期, violation_tag) GROUP BY，
    # 保证每个违规记录对每个违规标签只贡献 1 次计数，然后外层 SUM 聚合。
    # 原因：detail_sql 中的 LEFT JOIN dwd_itm_audit_reject_detail_y 会让同一 app_id
    # 产生多条记录（同一审核单有多条驳回详情），简单 COUNT(*) 会把重复行也计入，
    # 导致违规原因统计虚高（实测发现 YNLCY/GXLCY 20260518 的 reasons_sum = 2 × non_compliant_count）。
    # 进一步修复：不用 DISTINCT，而是显式 GROUP BY，确保每个违规记录对每个标签只计1。
    # ========== S5: 违规原因聚合（从管道读取）==========
    reason_tpl = get_pipeline_sql_by_category(env, 'reason')
    if reason_tpl:
        agg_sql = replace_pipeline_params(reason_tpl['sql_text'], detail_sql=detail_sql)
    else:
        # 兜底：使用原有硬编码逻辑（本地 case_when 变量嵌入）
        agg_sql = f"""SELECT `创建日期`, violation_tag, SUM(cnt) as cnt
FROM (
  SELECT `审核id`, `创建日期`,
    {case_when} as violation_tag,
    COUNT(*) as cnt
  FROM (
    {detail_sql}
  ) t
  WHERE t.`AI审核结果` = '违规'
  GROUP BY `审核id`, `创建日期`, violation_tag
) tagged
GROUP BY `创建日期`, violation_tag
ORDER BY `创建日期`, cnt DESC"""

    print(f"[DEBUG] {instance} 执行聚合 SQL 统计违规原因...")

    # 执行聚合查询
    try:
        result = execute_sql_query(agg_sql, instance, env)
    except Exception as e:
        print(f"[ERROR] {instance} 聚合 SQL 执行失败: {e}")
        return {'by_date': {}, 'global': {}}

    if not isinstance(result, list) or len(result) == 0:
        print(f"[WARN] {instance} 聚合 SQL 返回空数据")
        return {'by_date': {}, 'global': {}}

    # ========== 解析聚合结果 ==========
    error_reasons_by_date = {}  # {date_str: {tag: count}}
    error_reasons_global = {}   # {tag: total_count}
    fetched = len(result)

    for row in result:
        raw_date = str(row.get('创建日期', '') or '').strip()
        tag = str(row.get('violation_tag', '') or '').strip()
        cnt = int(row.get('cnt', 0) or 0)

        # 标准化日期格式：'2026/05/03' -> '20260503'
        date_str = raw_date.replace('/', '').replace('-', '')
        if len(date_str) != 8:
            date_str = start_date_fmt

        # 按日期聚合
        error_reasons_by_date.setdefault(date_str, {})
        error_reasons_by_date[date_str][tag] = error_reasons_by_date[date_str].get(tag, 0) + cnt

        # 全局聚合
        error_reasons_global[tag] = error_reasons_global.get(tag, 0) + cnt

    print(f"[DEBUG] {instance} 聚合 SQL 统计完成: rows={fetched}, "
          f"global_tags={len(error_reasons_global)}, date_days={len(error_reasons_by_date)}")
    return {'by_date': error_reasons_by_date, 'global': error_reasons_global}


def fetch_data_with_template(env, instance, sql_template, sample_percent, start_date=None, end_date=None, excluded_audit_ids=None):
    """使用自定义SQL模板拉取数据，使用窗口函数翻页
    
    Args:
        excluded_audit_ids: 已抽取的审核ID集合，用于增量抽样（排除已抽取的数据）
    """
    
    # ========== 探活：确认 Cookie 有效 ==========
    ping_ok, ping_msg = ping_idata(env, instance)
    if not ping_ok:
        raise RuntimeError(f"iData 认证失败：{ping_msg}。请检查 Cookie 是否过期。")

    ai_result_field = 'AI审核结果'
    if 'as "' in sql_template.lower():
        match = re.search(r'as\s+["\'](\w+结果)["\']', sql_template, re.IGNORECASE)
        if match:
            ai_result_field = match.group(1)

    # 尝试从模板中识别审核ID字段名（用于 COUNT(DISTINCT)）
    audit_id_field = '审核id'
    if 'as "' in sql_template.lower():
        match = re.search(r'as\s+["\']([^"\']*审核id[^"\']*)["\']', sql_template, re.IGNORECASE)
        if match:
            audit_id_field = match.group(1)
    
    # 增量抽样：格式化排除的ID集合（在识别 audit_id_field 之后）
    excluded_ids_set = set(excluded_audit_ids) if excluded_audit_ids else set()
    excluded_ids_str = ','.join([f"'{aid}'" for aid in excluded_ids_set]) if excluded_ids_set else None
    excluded_sql = f"AND `{audit_id_field}` NOT IN ({excluded_ids_str})" if excluded_ids_str else ""
    
    # 重要修复：COUNT 必须用 DISTINCT，避免 LEFT JOIN 行倍增
    # 增量抽样：排除已抽取的审核ID
    count_sql = f"SELECT `{ai_result_field}`, COUNT(DISTINCT `{audit_id_field}`) as count FROM ({sql_template}) t WHERE 1=1 {excluded_sql} GROUP BY `{ai_result_field}`"

    total_count = 0
    original_compliant = 0
    original_non_compliant = 0

    try:
        count_result = execute_sql_query(count_sql, instance, env)
        print(f"[DEBUG] 模板实例 {instance} COUNT结果: {count_result}")

        if not isinstance(count_result, list):
            count_result = [count_result]

        for row in count_result:
            if not isinstance(row, dict):
                continue
            
            status = ''
            count = 0
            
            for key in [ai_result_field, 'AI审核结果', 'ai_result', 'ai_audit_result', '审核结果', 'status']:
                if key in row:
                    status = str(row[key] or '')
                    break
            
            for key in ['count', 'cnt', 'COUNT(*)', 'TOTAL']:
                if key in row:
                    count = int(row[key] or 0)
                    break
            
            total_count += count
            
            if status == '合规' or status == '1' or status == 'PASS':
                original_compliant += count
            elif status == '违规' or status == '0' or status == 'REJECT':
                original_non_compliant += count
            else:
                print(f"[WARN] 模板实例 {instance} 未知状态: {status}, 计数: {count}")

        print(f"[DEBUG] 模板实例 {instance} 解析: 总数={total_count}, 合规={original_compliant}, 违规={original_non_compliant}")
    except Exception as e:
        print(f"[ERROR] 实例 {instance} 查询总数失败: {e}")
        raise

    if total_count == 0:
        return {
            "fetched_data": [],
            "total_fetched": 0,
            "compliant_count": 0,
            "non_compliant_count": 0,
            "original_total": 0,
            "original_compliant": 0,
            "original_non_compliant": 0
        }

    # ========== 分层抽样（保持合规/违规比例与线上全量一致）==========
    #
    # 通俗说明：假设线上 10000 条，其中违规 2000 条（20%）。抽 10% 时：
    #   - 从 8000 合规里抽 800 条
    #   - 从 2000 违规里抽 200 条
    #   → 最终样本中违规率也是 20%，与线上完全一致
    #
    # 技术细节：
    #   ① 先 COUNT GROUP BY AI审核结果 查询线上合规/违规各自总数
    #   ② 合规层：max(1, int(original_compliant × pct / 100)) 条
    #   ③ 违规层：max(1, int(original_non_compliant × pct / 100)) 条
    #   ④ 两层分别用 MD5(instance+date+'_C'/'_N') 作固定种子，生成不重复随机行号
    #   ⑤ 各层 SQL：WHERE AI审核结果='合规'/'违规' + ROW_NUMBER() OVER(ORDER BY MD5(审核id)) + WHERE rn IN (...)
    #   ⑥ 合并写入 RawData；original_compliant/original_non_compliant 保持不变（记录的是线上全量数字）
    #
    # 复现：种子固定，同参数两次拉取审核id完全一致
    compliant_sample_count = max(1, int(original_compliant * sample_percent / 100)) if original_compliant > 0 else 0
    non_compliant_sample_count = max(1, int(original_non_compliant * sample_percent / 100)) if original_non_compliant > 0 else 0
    sample_count = compliant_sample_count + non_compliant_sample_count
    print(f"[DEBUG] 模板 {instance} 分层抽样: 合规={original_compliant}→抽{compliant_sample_count}, 违规={original_non_compliant}→抽{non_compliant_sample_count}, 总计={sample_count}")

    # 尝试从模板中识别审核ID字段名，用于 MD5 排序
    template_audit_id_field = '审核id'
    if 'as "' in sql_template.lower():
        matches = re.findall(r'as\s+["\']([^"\']*审核id[^"\']*)["\']', sql_template, re.IGNORECASE)
        if matches:
            template_audit_id_field = matches[0]

    # 种子1：合规抽样；种子2：违规抽样（加后缀避免关联）
    seed_str_c = f"{instance}_{start_date}_{end_date}_C"
    seed_c = int(hashlib.md5(seed_str_c.encode('utf-8')).hexdigest(), 16) % 100000
    seed_str_n = f"{instance}_{start_date}_{end_date}_N"
    seed_n = int(hashlib.md5(seed_str_n.encode('utf-8')).hexdigest(), 16) % 100000

    sampled_data = []

    # ---------- 合规数据抽样 ----------
    if compliant_sample_count > 0:
        random.seed(seed_c)
        if compliant_sample_count >= original_compliant:
            compliant_positions = list(range(1, original_compliant + 1))
        else:
            compliant_positions = sorted(random.sample(range(1, original_compliant + 1), compliant_sample_count))

        positions_str_c = ','.join(map(str, compliant_positions))
        compliant_sql = f"""SELECT * FROM (
          SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`{template_audit_id_field}`)) as rn
          FROM (
            {sql_template}
          ) t
          WHERE 1=1 {excluded_sql}
            AND t.`{ai_result_field}` = '合规'
        ) tmp
        WHERE tmp.rn IN ({positions_str_c})"""

        try:
            rows_c = execute_sql_query(compliant_sql, instance, env)
            if isinstance(rows_c, list):
                sampled_data.extend(rows_c)
                print(f"[DEBUG] 模板 {instance} 合规抽样: 请求{len(compliant_positions)}行, 实际返回{len(rows_c)}行")
            elif isinstance(rows_c, dict) and 'error' in rows_c:
                print(f"[ERROR] 模板 {instance} 合规抽样SQL失败: {rows_c['error']}")
            else:
                print(f"[WARN] 模板 {instance} 合规抽样返回非list: {type(rows_c)}")
        except Exception as e:
            print(f"[ERROR] 模板 {instance} 合规抽样查询异常: {e}")
            raise

    # ---------- 违规数据抽样 ----------
    if non_compliant_sample_count > 0 and original_non_compliant > 0:
        random.seed(seed_n)
        if non_compliant_sample_count >= original_non_compliant:
            non_compliant_positions = list(range(1, original_non_compliant + 1))
        else:
            non_compliant_positions = sorted(random.sample(range(1, original_non_compliant + 1), non_compliant_sample_count))

        positions_str_n = ','.join(map(str, non_compliant_positions))
        non_compliant_sql = f"""SELECT * FROM (
          SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`{template_audit_id_field}`)) as rn
          FROM (
            {sql_template}
          ) t
          WHERE 1=1 {excluded_sql}
            AND t.`{ai_result_field}` = '违规'
        ) tmp
        WHERE tmp.rn IN ({positions_str_n})"""

        try:
            rows_n = execute_sql_query(non_compliant_sql, instance, env)
            if isinstance(rows_n, list):
                sampled_data.extend(rows_n)
                print(f"[DEBUG] 模板 {instance} 违规抽样: 请求{len(non_compliant_positions)}行, 实际返回{len(rows_n)}行")
            elif isinstance(rows_n, dict) and 'error' in rows_n:
                print(f"[ERROR] 模板 {instance} 违规抽样SQL失败: {rows_n['error']}")
            else:
                print(f"[WARN] 模板 {instance} 违规抽样返回非list: {type(rows_n)}")
        except Exception as e:
            print(f"[ERROR] 模板 {instance} 违规抽样查询异常: {e}")
            raise

    print(f"[DEBUG] 模板 {instance} 最终采样: 目标{sample_count}行, 实际{len(sampled_data)}行")


    compliant_count = 0
    violation_count = 0
    for d in sampled_data:
        val = d.get('AI审核结果') or d.get('ai_result') or d.get('ai_audit_result') or d.get('审核结果') or ''
        if val in ['合规', '1', 'PASS']:
            compliant_count += 1
        elif val in ['违规', '0', 'REJECT']:
            violation_count += 1

    return {
        "fetched_data": sampled_data,
        "total_fetched": len(sampled_data),
        "compliant_count": compliant_count,
        "non_compliant_count": violation_count,
        "original_total": total_count,
        "original_compliant": original_compliant,
        "original_non_compliant": original_non_compliant,
        "excluded_count": len(excluded_ids_set),  # 增量抽样：排除的ID数量
        "is_incremental": len(excluded_ids_set) > 0  # 是否为增量抽样
    }