from flask import Blueprint, request, jsonify
from flask_login import login_required
import requests
import random
import threading
import time
import re
import json
import os
from datetime import datetime, timedelta
try:
    import pandas as pd
except ImportError:
    pd = None
from config import ENV_CONFIG, FETCH_PAGE_SIZE, IDATA_COOKIE as DEFAULT_COOKIE, IDATA_DATASOURCE_TYPE
from models import db, RawData, FetchLog, SqlTemplate, Config

data_fetch_bp = Blueprint('data_fetch', __name__)


def to_beijing_time(dt):
    """将 UTC datetime 转换为北京时间（UTC+8）
    
    参数：dt - datetime 对象（可以是 naive 或 aware）
    返回：格式化的北京时�"YYYY-MM-DD HH:MM:SS" 字符串
    """
    if not dt:
        return ''
    
    # 如果是 naive datetime，视为 UTC
    if dt.tzinfo is None:
        dt = dt + timedelta(hours=8)
    else:
        # 如果有时区信息，转换为 UTC 后再加8小时
        dt = dt.replace(tzinfo=None) + timedelta(hours=8)
    
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def get_idata_cookie():
    """从数据库获取 Cookie，优先数据库，没有则返回默认 Cookie"""
    sqlconfig = Config.query.filter_by(key='IDATA_COOKIE').first()
    if sqlconfig and sqlconfig.value:
        return sqlconfig.value
    return DEFAULT_COOKIE


def replace_params(sql_template, params):
    """替换SQL中的占位符"""
    result = sql_template
    for key, value in params.items():
        result = result.replace('${' + key + '}', str(value))
    return result


def build_sql(instance, start_date, end_date):
    """根据实例和日期构建取数 SQL"""
    year = start_date[:4]
    return f"""
    select
      c.shop_id as "供应商id",
      c.`标签`,
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
      c.shop_name as "店铺名称",
      r.item_name as "商品名称",
      r.category_name as "类目",
      r.main_imgs as "主图",
      r.detail_imgs as "详情图",
      r.sku_imgs as "sku图",
      r.spu_imgs as "spu图",
      case
        when r.instance_code in ('GXLCY','YNLCY')
          then concat('https://www.lecaiyun.com/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
        when r.instance_code in ('HWCS','ZJWC','HNLCWC')
          then concat('https://www.zcygov.cn/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
      end as "商品链接",
      r.check_result as "标注结果：1=正确 0=错误",
      r.annotation as "备注",
      r.instance_code as "实例编码",
      date_format(r.gmt_created_time,'%Y/%m/%d') as "创建日期",
      '' as "标注人",
      RAND() as "随机数",
      new.`变更类别`,
      r.gmt_created_time as "创建时间"
    from dwd.dwd_itm_audit_app_ai_result_detail_inc_y r
    left join dwd.dwd_itm_audit_reject_detail_y d on r.app_id = d.app_id
    LEFT JOIN (
      select
        instance_code,
        cast(item_id as varchar) as id,
        cast(shop_id as varchar) as shop_id,
        shop_name,
        CASE
          when publish_channel = 1 then 'PCWEB'
          when publish_channel = 3 then '开放平台'
          when publish_channel = 17 then '商家引用创建'
          when publish_channel = 18 then '页面添加卖场发布'
        end as "标签"
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
    and r.pt = '{year}' and d.pt = '{year}'
    and r.instance_code = '{instance}'
    """


def execute_sql_query(sql, instance, env):
    """执行 SQL 查询，返回 JSON 数据
    iData getData 接口返回结构: {"success": true, "data": {"values": [...], "count": N, ...}}
    """
    api_url = ENV_CONFIG[env]['query_api_url']
    # 优先从数据库读取 Cookie，否则使用默认配置
    cookie = get_idata_cookie()
    headers = {
        'Content-Type': 'application/json',
        'Cookie': cookie
    }
    payload = {
        'sql': sql,
        'instance': instance,
        'datasourceType': IDATA_DATASOURCE_TYPE
    }
    resp = requests.post(api_url, json=payload, headers=headers, timeout=180)
    resp.raise_for_status()
    result = resp.json()

    # 检查 iData 返回的业务状态
    if isinstance(result, dict) and result.get('success') is False:
        error_msg = result.get('error', result.get('message', 'iData 接口返回错误'))
        raise Exception(f"iData 错误: {error_msg}")

    # iData getData 返回结构: {"success": true, "data": {"values": [...], "count": N, ...}}
    if isinstance(result, dict):
        if 'data' in result and isinstance(result['data'], dict):
            data_obj = result['data']
            # 明细查询返回 values 数组
            if 'values' in data_obj:
                return data_obj['values']
            # count 查询返回 values 数组（包含 count 值）
            return data_obj
        elif 'data' in result:
            return result['data']
        elif 'result' in result:
            return result['result']
    return result


def fetch_data_from_idata(env, instance, start_date, end_date, sample_percent):
    """核心函数：从 iData 拉取数据，使用窗口函数翻页，随机抽样，返回结果"""

    # 1. 构建 COUNT SQL 查询总数（按AI审核结果分组获取合规数和违规数）
    detail_sql = build_sql(instance, start_date, end_date)
    # 使用 GROUP BY 方式，返回多行，每行是一个状态及其计数
    count_sql = f"SELECT `AI审核结果`, COUNT(*) as count FROM ({detail_sql}) t GROUP BY `AI审核结果`"

    try:
        count_result = execute_sql_query(count_sql, instance, env)
        print(f"[DEBUG] 实例 {instance} COUNT结果: {count_result}")

        # 解析结果：遍历各行，根据状态值赋值
        total_count = 0
        original_compliant = 0
        original_non_compliant = 0

        # 确保 count_result 是列表
        if not isinstance(count_result, list):
            count_result = [count_result]

        for row in count_result:
            if not isinstance(row, dict):
                continue
            
            # 获取状态和计数
            status = ''
            count = 0
            
            # 尝试获取状态值
            for key in ['AI审核结果', 'ai_result', 'ai_audit_result', '审核结果', 'status']:
                if key in row:
                    status = str(row[key] or '')
                    break
            
            # 尝试获取计数值
            for key in ['count', 'cnt', 'COUNT(*)', 'TOTAL']:
                if key in row:
                    count = int(row[key] or 0)
                    break
            
            # 累加总数
            total_count += count
            
            # 根据状态赋值
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

    # 2. 计算抽样条数
    if sample_percent >= 100:
        sample_count = total_count
    else:
        sample_count = max(1, int(total_count * sample_percent / 100))
    
    # 3. 使用窗口函数翻页获取明细数据
    all_data = []
    offset = 0
    query_timeout = 60  # 每次请求超时60秒
    
    while len(all_data) < sample_count:
        start_row = offset + 1
        end_row = offset + FETCH_PAGE_SIZE
        
        # 使用窗口函数分页
        paginated_sql = f"""SELECT * FROM (
          SELECT t.*, ROW_NUMBER() OVER (ORDER BY `随机数` DESC) as rn
          FROM (
            {detail_sql}
          ) t
        ) tmp
        WHERE tmp.rn BETWEEN {start_row} AND {end_row}"""
        
        try:
            batch = execute_sql_query(paginated_sql, instance, env)
            if isinstance(batch, list):
                all_data.extend(batch)
                if len(batch) < FETCH_PAGE_SIZE:
                    # 没有更多数据
                    break
            else:
                break
        except Exception as e:
            print(f"[ERROR] 实例 {instance} 分页取数失败 (offset={offset}): {e}")
            break
        
        offset += FETCH_PAGE_SIZE
    
    # 4. 随机抽样
    if len(all_data) > sample_count:
        sampled_data = random.sample(all_data, sample_count)
    else:
        sampled_data = all_data

    # 5. 统计抽样数据的合规/违规（仅用于内部展示日志，不覆盖原始统计）
    compliant_count = 0
    violation_count = 0
    for d in sampled_data:
        val = d.get('AI审核结果', d.get('ai_result', ''))
        if val == '合规':
            compliant_count += 1
        elif val == '违规':
            violation_count += 1

    # 6. 返回原始统计（来自 COUNT SQL）和抽样统计
    return {
        "fetched_data": sampled_data,
        "total_fetched": len(sampled_data),
        "compliant_count": compliant_count,  # 抽样数据的合规数（仅用于展示）
        "non_compliant_count": violation_count,  # 抽样数据的违规数（仅用于展示）
        "original_total": total_count,  # 线上全量总数（来自 COUNT SQL）
        "original_compliant": original_compliant,  # 线上全量合规数（来自 COUNT SQL）
        "original_non_compliant": original_non_compliant  # 线上全量违规数（来自 COUNT SQL）
    }


def fetch_data_with_template(env, instance, sql_template, sample_percent):
    """使用自定义SQL模板拉取数据，使用窗口函数翻页"""

    # 1. 构建 COUNT SQL 查询总数（按状态分组获取合规数和违规数）
    # 使用 GROUP BY 方式，返回多行，每行是一个状态及其计数
    # 尝试获取"AI审核结果"字段的别名
    ai_result_field = 'AI审核结果'  # 默认值
    if 'as "' in sql_template.lower():
        # 尝试从 SQL 模板中提取别名
        import re
        match = re.search(r'as\s+["\'](\w+结果)["\']', sql_template, re.IGNORECASE)
        if match:
            ai_result_field = match.group(1)
    
    count_sql = f"SELECT `{ai_result_field}`, COUNT(*) as count FROM ({sql_template}) t GROUP BY `{ai_result_field}`"

    try:
        count_result = execute_sql_query(count_sql, instance, env)
        print(f"[DEBUG] 模板实例 {instance} COUNT结果: {count_result}")

        # 解析结果：遍历各行，根据状态值赋值
        total_count = 0
        original_compliant = 0
        original_non_compliant = 0

        # 确保 count_result 是列表
        if not isinstance(count_result, list):
            count_result = [count_result]

        for row in count_result:
            if not isinstance(row, dict):
                continue
            
            # 获取状态和计数
            status = ''
            count = 0
            
            # 尝试获取状态值
            for key in [ai_result_field, 'AI审核结果', 'ai_result', 'ai_audit_result', '审核结果', 'status']:
                if key in row:
                    status = str(row[key] or '')
                    break
            
            # 尝试获取计数值
            for key in ['count', 'cnt', 'COUNT(*)', 'TOTAL']:
                if key in row:
                    count = int(row[key] or 0)
                    break
            
            # 累加总数
            total_count += count
            
            # 根据状态赋值
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

    # 2. 计算抽样条数
    if sample_percent >= 100:
        sample_count = total_count
    else:
        sample_count = max(1, int(total_count * sample_percent / 100))

    # 3. 使用窗口函数翻页获取明细数据
    all_data = []
    offset = 0
    
    while len(all_data) < sample_count:
        start_row = offset + 1
        end_row = offset + FETCH_PAGE_SIZE
        
        # 使用窗口函数分页
        paginated_sql = f"""SELECT * FROM (
          SELECT t.*, ROW_NUMBER() OVER (ORDER BY `随机数` DESC) as rn
          FROM (
            {sql_template}
          ) t
        ) tmp
        WHERE tmp.rn BETWEEN {start_row} AND {end_row}"""
        
        try:
            batch = execute_sql_query(paginated_sql, instance, env)
            if isinstance(batch, list):
                all_data.extend(batch)
                if len(batch) < FETCH_PAGE_SIZE:
                    # 没有更多数据
                    break
            else:
                break
        except Exception as e:
            print(f"[ERROR] 实例 {instance} 分页取数失败 (offset={offset}): {e}")
            break
        
        offset += FETCH_PAGE_SIZE

    # 4. 随机抽样
    if len(all_data) > sample_count:
        sampled_data = random.sample(all_data, sample_count)
    else:
        sampled_data = all_data

    # 5. 统计抽样数据的合规/违规（仅用于内部展示日志，不覆盖原始统计）
    compliant_count = 0
    violation_count = 0
    for d in sampled_data:
        val = (d.get('AI审核结果') or d.get('ai_result') or 
               d.get('ai_audit_result') or d.get('审核结果') or '')
        if val == '合规' or val == '1' or val == 'PASS':
            compliant_count += 1
        elif val == '违规' or val == '0' or val == 'REJECT':
            violation_count += 1

    # 6. 返回原始统计（来自 COUNT SQL）和抽样统计
    return {
        "fetched_data": sampled_data,
        "total_fetched": len(sampled_data),
        "compliant_count": compliant_count,  # 抽样数据的合规数（仅用于展示）
        "non_compliant_count": violation_count,  # 抽样数据的违规数（仅用于展示）
        "original_total": total_count,  # 线上全量总数（来自 COUNT SQL）
        "original_compliant": original_compliant,  # 线上全量合规数（来自 COUNT SQL）
        "original_non_compliant": original_non_compliant  # 线上全量违规数（来自 COUNT SQL）
    }


@data_fetch_bp.route('/api/data-fetch', methods=['POST'])
def api_data_fetch():
    """数据拉取接口"""
    data = request.get_json()
    env = data.get('env')
    instances = data.get('instances', [])
    sample_percent = data.get('sample_percent', 100)
    # 确保sample_percent不为0或None
    if not sample_percent or sample_percent <= 0:
        sample_percent = 100
    start_date = data.get('start_date', '')
    end_date = data.get('end_date', '')

    # 新增：模板ID和参数
    template_id = data.get('template_id')
    params = data.get('params', {})

    if not env:
        return jsonify({"success": False, "message": "参数不完整：缺少环境"}), 400

    # 如果传了template_id，从数据库获取SQL模板（使用SqlTemplate表）
    sql_template = None
    template_instances = []
    if template_id:
        config = SqlTemplate.query.get(template_id)
        if config:
            sql_template = config.sql_text
            # 获取模板中定义的实例列表
            if config.instances:
                template_instances = [inst.strip() for inst in config.instances.split(',') if inst.strip()]

            # 合并日期参数
            if start_date:
                params['start_date'] = start_date
            if end_date:
                params['end_date'] = end_date
            # 合并year参数（从start_date提取）
            if start_date and len(start_date) >= 4:
                params['year'] = start_date[:4]

    # 确定实际要查询的实例列表
    # 优先级：前端传入的instances > 模板定义的instances
    if not instances and template_instances:
        # 前端没选实例，但模板定义了实例，使用模板的实例
        final_instances = template_instances
    elif instances and template_instances:
        # 前端选了实例，和模板定义的实例取交集
        final_instances = [inst for inst in instances if inst in template_instances]
        if not final_instances:
            # 如果没有交集，警告并使用模板的实例
            final_instances = template_instances
    elif instances:
        # 没有模板，使用前端的实例
        final_instances = instances
    elif template_instances:
        # 没有前端实例但有模板实例
        final_instances = template_instances
    else:
        # 都没有，返回错误
        return jsonify({"success": False, "message": "请选择至少一个实例"}), 400

    # 生成批次号
    batch_id = "BAT-" + datetime.now().strftime('%Y%m%d%H%M%S')

    # 先插入 running 状态的日志
    log = FetchLog(
        batch_id=batch_id,
        env=env,
        instances=','.join(final_instances),
        sample_percent=sample_percent,
        total_fetched=0,
        original_total=0,
        compliant_count=0,
        non_compliant_count=0,
        status='running',
        source='fetch',
        data_start_date=start_date,  # 记录数据开始日期
        data_end_date=end_date    # 记录数据结束日期
    )
    db.session.add(log)
    db.session.commit()

    # 立即更新一次进度，标记为开始查询
    log.total_fetched = 0
    log.original_total = 0
    log.status = 'running'
    db.session.commit()

    all_fetched = []
    total_compliant = 0
    total_non_compliant = 0
    total_original_compliant = 0
    total_original_violation = 0
    total_skipped = 0
    all_original_totals = {}

    for instance in final_instances:
        try:
            # 如果有自定义SQL模板，使用模板；否则使用默认SQL
            if sql_template:
                # 对每个实例单独替换参数并执行
                instance_params = params.copy()
                instance_params['instance'] = instance
                instance_sql = replace_params(sql_template, instance_params)
                
                # 先查询COUNT，获取原始数据量
                count_sql = f"SELECT COUNT(*) as cnt FROM (\n{instance_sql}\n) t"
                try:
                    count_result = execute_sql_query(count_sql, instance, env)
                    instance_count = 0
                    if isinstance(count_result, list) and len(count_result) > 0:
                        first_row = count_result[0]
                        if isinstance(first_row, dict):
                            for key in ['cnt', 'COUNT(*)', 'count', 'total']:
                                if key in first_row:
                                    instance_count = int(first_row[key])
                                    break
                    # 更新当前实例的原始数量
                    all_original_totals[instance] = instance_count
                    # 更新日志进度
                    log.original_total = sum(all_original_totals.values())
                    db.session.commit()
                except Exception as count_err:
                    print(f"[DEBUG] 实例 {instance} COUNT查询失败: {count_err}")
                
                result = fetch_data_with_template(env, instance, instance_sql, sample_percent)
            else:
                result = fetch_data_from_idata(env, instance, start_date, end_date, sample_percent)
            # 存入数据库
            # 去重：查询历史数据中相同日期和实例的product_id
            existing_product_ids = set()
            if start_date and end_date:
                # 将日期格式转换为斜杠格式
                start_date_slash = f"{start_date[:4]}/{start_date[4:6]}/{start_date[6:8]}"
                end_date_slash = f"{end_date[:4]}/{end_date[4:6]}/{end_date[6:8]}"
                
                existing_records = RawData.query.filter(
                    RawData.created_date >= start_date_slash,
                    RawData.created_date <= end_date_slash,
                    RawData.instance_code == instance
                ).all()
                
                for rec in existing_records:
                    if rec.product_id:
                        existing_product_ids.add(rec.product_id)
                
                print(f"[DEBUG] 实例 {instance} 日期范围 {start_date_slash}~{end_date_slash} 已有 {len(existing_product_ids)} 条历史记录")

            # 过滤掉重复数据
            unique_fetched_data = []
            skipped_count = 0
            for row in result['fetched_data']:
                product_id = str(row.get('商品id', ''))
                if product_id and product_id in existing_product_ids:
                    skipped_count += 1
                    continue
                unique_fetched_data.append(row)
                existing_product_ids.add(product_id)  # 防止同批次内重复
            
            print(f"[DEBUG] 实例 {instance} 本次拉取 {len(result['fetched_data'])} 条，跳过重复 {skipped_count} 条")
            
            # 累加跳过数量
            total_skipped += skipped_count
            
            # 基于去重后的数据统计合规/违规
            unique_compliant = 0
            unique_violation = 0
            for d in unique_fetched_data:
                val = d.get('AI审核结果', d.get('ai_result', ''))
                if val == '合规':
                    unique_compliant += 1
                elif val == '违规':
                    unique_violation += 1
            
            # 累加去重后的合规/违规数
            total_compliant += unique_compliant
            total_non_compliant += unique_violation
            
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

            total_compliant += result['compliant_count']
            total_non_compliant += result['non_compliant_count']
            all_fetched.extend(result['fetched_data'])
            all_original_totals[instance] = result['original_total']

            # 累加原始合规/违规数
            if 'original_compliant' in result:
                total_original_compliant += result['original_compliant']
            if 'original_non_compliant' in result:
                total_original_violation += result['original_non_compliant']

            # 每获取完一个实例，更新日志
            log.total_fetched = len(all_fetched)
            log.original_total = sum(all_original_totals.values())
            log.compliant_count = total_compliant
            log.non_compliant_count = total_non_compliant
            log.original_compliant = total_original_compliant
            log.original_non_compliant = total_original_violation
            log.skipped_duplicates = total_skipped
            db.session.commit()

        except Exception as e:
            print(f"[ERROR] 实例 {instance} 拉取失败: {e}")
            # 更新状态为失败
            log.status = 'failed'
            db.session.commit()
            return jsonify({
                "success": False,
                "message": f"实例 {instance} 拉取失败: {str(e)}"
            }), 500

    total_fetched = len(all_fetched)

    # 计算总原始数据量
    total_original = sum(all_original_totals.values())

    # 使用实际统计的原始合规/违规数（从各实例返回结果累加）
    original_compliant_total = total_original_compliant
    original_non_compliant_total = total_original_violation

    # 如果没有统计到（兼容旧代码），使用抽样数据推算
    if (original_compliant_total == 0 and original_non_compliant_total == 0) and sample_percent and sample_percent > 0 and sample_percent < 100:
        ratio = 100.0 / sample_percent
        original_compliant_total = int(total_compliant * ratio)
        original_non_compliant_total = int(total_non_compliant * ratio)

    # 更新日志为完成
    log.total_fetched = total_fetched
    log.compliant_count = total_compliant
    log.non_compliant_count = total_non_compliant
    log.status = 'completed'
    log.data_start_date = start_date  # 确保数据日期被记录
    log.data_end_date = end_date
    log.original_total = total_original  # 线上原始总数
    log.original_compliant = original_compliant_total  # 线上原始合规数（推算）
    log.original_non_compliant = original_non_compliant_total  # 线上原始违规数（推算）
    log.skipped_duplicates = total_skipped  # 跳过的重复数据条数
    db.session.commit()

    # 异步启动模型B互检（Mock）
    def mock_model_b():
        time.sleep(3)
        print(f"[ModelB] 批次 {batch_id} 互检完成（Mock）")

    threading.Thread(target=mock_model_b, daemon=True).start()

    # 获取模板名称（如果有）
    template_name = ''
    if template_id:
        config = SqlTemplate.query.get(template_id)
        if config:
            template_name = config.name

    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "total_fetched": total_fetched,
        "compliant_count": total_compliant,
        "non_compliant_count": total_non_compliant,
        "original_total": total_original,
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


@data_fetch_bp.route('/api/task-batches/running', methods=['GET'])
def api_task_batches_running():
    """获取当前正在执行的任务
    
    支持查询参数：
    - source: 数据来源筛选（fetch/upload）
    """
    source = request.args.get('source', '')
    
    # 构建查询
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
        return jsonify({
            "running": False
        })


# ========== 任务历史接口 ==========
@data_fetch_bp.route('/api/task-batches', methods=['GET'])
def api_task_batches():
    """获取拉取批次列表

    支持查询参数：
    - env: 环境筛选（云环境/乐采云环境）
    - date_from: 开始日期 YYYYMMDD
    - date_to: 结束日期 YYYYMMDD
    - source: 数据来源筛选（fetch/upload）
    - start_date: 开始日期 YYYY-MM-DD（兼容性）
    - end_date: 结束日期 YYYY-MM-DD（兼容性）
    - instances: 实例筛选，逗号分隔多个实例（如 ZJWC,HWCS）
    - rule: 规则名称筛选，根据 INSTANCE_RULE_MAPPING 过滤
    """
    env = request.args.get('env', '')
    date_from = request.args.get('date_from', '')
    date_to = request.args.get('date_to', '')
    source = request.args.get('source', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')
    instances = request.args.get('instances', '')
    rule = request.args.get('rule', '')

    # 如果指定了规则，从映射中获取实例列表
    if rule and not instances:
        mapping_config = Config.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
        if mapping_config and mapping_config.value:
            try:
                import json as json_lib
                mapping = json_lib.loads(mapping_config.value)
                # 找出该规则对应的所有实例
                rule_instances = [k for k, v in mapping.items() if v == rule]
                if rule_instances:
                    instances = ','.join(rule_instances)
            except:
                pass

    # 构建查询
    query = FetchLog.query

    if env:
        query = query.filter(FetchLog.env == env)

    # 开始日期（支持多种格式）
    effective_start = date_from or start_date
    if effective_start:
        try:
            # 尝试解析 YYYYMMDD
            if len(effective_start) == 8:
                from_date = datetime.strptime(effective_start, '%Y%m%d')
            else:
                from_date = datetime.strptime(effective_start, '%Y-%m-%d')
            query = query.filter(FetchLog.fetch_time >= from_date)
        except:
            pass

    # 结束日期（支持多种格式）
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

    # 实例筛选
    if instances:
        instance_list = [i.strip() for i in instances.split(',') if i.strip()]
        if instance_list:
            # 使用 LIKE 查询匹配任一实例
            from sqlalchemy import or_
            filters = []
            for inst in instance_list:
                filters.append(FetchLog.instances.like('%' + inst + '%'))
            query = query.filter(or_(*filters))

    # 按时间倒序
    batches = query.order_by(FetchLog.fetch_time.desc()).all()
    
    result = []
    for b in batches:
        # 查询该批次的互检状态
        total_items = b.total_fetched or 0
        reviewed_count = 0
        if total_items > 0:
            # 从 RawData 表查询已互检数量
            reviewed_count = RawData.query.filter_by(
                fetch_batch_id=b.batch_id,
                modelb_reviewed=True
            ).count()
        
        # 从 FetchLog 表获取互检状态
        db_review_status = b.review_status or 'pending'
        review_status_map = {
            'pending': '未互检',
            'running': '互检中',
            'completed': '已互检',
            'failed': '互检失败'
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
            "total_items": total_items
        })
    
    return jsonify({"batches": result})


@data_fetch_bp.route('/api/task-batches/<batch_id>/items', methods=['GET'])
def api_task_batch_items(batch_id):
    """获取指定批次的明细数据

    支持分页参数：
    - page: 页码（默认1）
    - per_page: 每页条数（默认50）
    """
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)

    # 查询该批次的全量数据（用于统计）
    full_query = RawData.query.filter_by(fetch_batch_id=batch_id)
    total = full_query.count()

    # 计算全量统计
    all_items = full_query.all()
    instance_stats = {}
    total_compliant = 0
    total_non_compliant = 0

    # 模型B互检统计
    modelb_stats = {
        'A合规B违规': 0,
        'A违规B合规': 0,
        'A合规B合规': 0,
        'A违规B违规': 0,
        '未互检': 0
    }

    for item in all_items:
        code = item.instance_code or '未知'
        if code not in instance_stats:
            instance_stats[code] = {'total': 0, 'compliant': 0, 'non_compliant': 0}

        instance_stats[code]['total'] += 1
        # 使用 LIKE 查询处理编码问题
        if item.ai_result and ('合规' in str(item.ai_result) or 'å' in str(item.ai_result)):
            instance_stats[code]['compliant'] += 1
            total_compliant += 1
        else:
            instance_stats[code]['non_compliant'] += 1
            total_non_compliant += 1

        # 模型B互检统计
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

    # 构建 summary
    instances_list = []
    for code in sorted(instance_stats.keys()):
        instances_list.append({
            'instance_code': code,
            'total': instance_stats[code]['total'],
            'compliant': instance_stats[code]['compliant'],
            'non_compliant': instance_stats[code]['non_compliant']
        })

    summary = {
        'total': {
            'total': total,
            'compliant': total_compliant,
            'non_compliant': total_non_compliant
        },
        'modelb': modelb_stats,
        'instances': instances_list
    }

    # 分页查询
    items_query = full_query.order_by(RawData.id.desc()).offset((page - 1) * per_page).limit(per_page)
    items = items_query.all()

    result = []
    for item in items:
        # 截断过长的图片URL
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
            # 模型B审核结果
            "modelb_result": item.modelb_result,
            "modelb_reason": item.modelb_reason,
            "modelb_consistent": item.modelb_consistent,
            "modelb_reviewed": item.modelb_reviewed
        })

    return jsonify({
        "items": result,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
        "summary": summary
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
    """删除指定批次"""
    # 查找该批次记录
    log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not log:
        return jsonify({"success": False, "message": "批次不存在"}), 404

    # 检查是否正在执行中
    if log.status == 'running':
        return jsonify({"success": False, "message": "正在执行的任务无法删除"}), 400

    try:
        # 删除该批次的明细数据
        RawData.query.filter_by(fetch_batch_id=batch_id).delete()
        # 删除批次记录
        db.session.delete(log)
        db.session.commit()
        return jsonify({"success": True, "message": "批次已删除"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"删除失败: {str(e)}"}), 500


@data_fetch_bp.route('/api/task-batches/clear', methods=['DELETE'])
def api_clear_all_batches():
    """清空所有历史批次"""
    # 检查是否有正在执行的任务
    running_task = FetchLog.query.filter_by(status='running').first()
    if running_task:
        return jsonify({"success": False, "message": "有任务正在执行中，无法清空"}), 400

    try:
        # 删除所有明细数据
        db.session.query(RawData).delete()
        # 删除所有批次记录
        db.session.query(FetchLog).delete()
        db.session.commit()
        return jsonify({"success": True, "message": "所有历史记录已清空"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"清空失败: {str(e)}"}), 500


# ========== 文件上传接口 ==========
def get_instance_rule_mapping():
    """从配置中获取实例与规则的映射关系"""
    mapping_config = Config.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
    if mapping_config and mapping_config.value:
        try:
            import json as json_lib
            return json_lib.loads(mapping_config.value)
        except:
            pass
    # 默认映射
    return {
        'ZJWC': '浙江网超审核规则',
        'HWCS': '浙江乐采网超审核规则',
        'HNLCWC': '其他乐采网超审核规则',
        'YNLCY': '其他乐采网超审核规则',
        'GXLCY': '其他乐采网超审核规则'
    }

@data_fetch_bp.route('/api/upload/for-review', methods=['POST'])
def api_upload_for_review():
    """文件上传机审接口
    
    接收 Excel 文件并解析，存入 RawData 表
    根据实例编码自动匹配审核规则
    """
    # 检查是否安装了 pandas
    if pd is None:
        return jsonify({"success": False, "message": "请安装 pandas 库: pip install pandas openpyxl"}), 500
    
    # 检查文件
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "请选择文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"success": False, "message": "文件名为空"}), 400
    
    # 检查文件类型
    allowed_exts = ['.xlsx', '.xls']
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_exts:
        return jsonify({"success": False, "message": f"仅支持 Excel 文件: {', '.join(allowed_exts)}"}), 400
    
    # 获取实例-规则映射
    instance_rule_mapping = get_instance_rule_mapping()
    
    # 获取所有唯一的实例编码，用于记录日志
    all_instances = set()
    
    env = '云环境'
    instances = ''
    
    # 读取 Excel 文件
    try:
        # 保存到临时文件
        temp_path = os.path.join('/tmp', file.filename)
        file.save(temp_path)
        
        # 读取 Excel
        df = pd.read_excel(temp_path)
        
        # 清理临时文件
        os.remove(temp_path)
    except Exception as e:
        return jsonify({"success": False, "message": f"读取文件失败: {str(e)}"}), 500
    
    if df.empty:
        return jsonify({"success": False, "message": "文件为空"}), 400
    
    # 列名映射配置（标准字段名 -> 数据库字段名）
    column_mapping = {
        '商品id': 'product_id',
        '商品ID': 'product_id',
        '商品名称': 'product_name',
        '商品名称': 'product_name',
        '类目': 'category',
        'AI审核结果': 'ai_result',
        'AI审核结果': 'ai_result',
        '审核单结果': 'audit_result',
        '审核id': 'audit_id',
        'AI审核id': 'ai_audit_id',
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
    
    # 检查必填字段（至少需要一个商品ID）
    product_id_cols = ['商品id', '商品ID']
    has_product_id = any(col in df.columns for col in product_id_cols)
    if not has_product_id:
        return jsonify({"success": False, "message": "文件缺少必填字段：商品id"}), 400
    
    # 处理列名 - 统一转换为数据库字段名
    df.columns = [column_mapping.get(col, col) for col in df.columns]
    
    # 生成批次号
    batch_id = "UPLOAD-" + datetime.now().strftime('%Y%m%d%H%M%S')
    
    # 统计
    total_rows = len(df)
    compliant_count = 0
    violation_count = 0
    
    # 存入数据库
    matched_rules = set()  # 记录匹配到的规则
    unmatched_instances = set()  # 记录未匹配到规则的实例
    
    for _, row in df.iterrows():
        # 处理每一行数据
        product_id = str(row.get('product_id', ''))
        
        # 如果没有商品ID则跳过
        if not product_id or product_id == 'nan':
            continue
        
        # 获取实例编码
        instance_code = str(row.get('instance_code', '')).strip()
        if not instance_code:
            instance_code = instances.split(',')[0] if instances else ''
        
        all_instances.add(instance_code)
        
        # 根据实例编码匹配规则
        rule_name = instance_rule_mapping.get(instance_code, '')
        if rule_name:
            matched_rules.add(rule_name)
        else:
            unmatched_instances.add(instance_code)
            rule_name = '未匹配'
        
        # 统计合规/违规
        ai_result = str(row.get('ai_result', ''))
        if ai_result in ['合规', '1', 'PASS']:
            compliant_count += 1
        elif ai_result in ['违规', '0', 'REJECT']:
            violation_count += 1
        
        # 创建 RawData 记录
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
            instance_code=str(row.get('instance_code', instances.split(',')[0] if instances else '')),
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
    
    # 记录 FetchLog - 使用实例编码
    log_instances = ','.join(sorted(all_instances)) if all_instances else instances
    
    # 记录匹配到的规则信息（如果有未匹配的，加到备注中）
    rule_info = ''
    if matched_rules:
        rule_info = '规则:' + ','.join(sorted(matched_rules))
    if unmatched_instances:
        rule_info += ('; ' if rule_info else '') + '未匹配:' + ','.join(sorted(unmatched_instances))
    
    # 上传任务默认使用当天作为数据日期
    upload_date = datetime.now().strftime('%Y%m%d')
    log = FetchLog(
        batch_id=batch_id,
        env=env,
        instances=log_instances,
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
    
    # 异步启动模型B互检（Mock）
    def mock_model_b():
        time.sleep(3)
        print(f"[ModelB] 批次 {batch_id} 互检完成（Mock）")
    
    threading.Thread(target=mock_model_b, daemon=True).start()
    
    return jsonify({
        "success": True,
        "batch_id": batch_id,
        "total": total_rows,
        "compliant_count": compliant_count,
        "non_compliant_count": violation_count,
        "matched_rules": list(matched_rules) if matched_rules else [],
        "unmatched_instances": list(unmatched_instances) if unmatched_instances else [],
        "instances": log_instances
    })


# ========== 导入模板下载接口 ==========
@data_fetch_bp.route('/api/upload/template', methods=['GET'])
def api_download_template():
    """下载导入模板 Excel 文件"""
    # 检查是否安装了 pandas/openpyxl
    if pd is None:
        return jsonify({"success": False, "message": "请安装 pandas 库: pip install pandas openpyxl"}), 500
    
    try:
        # 创建包含标准列名的 DataFrame
        columns = [
            '商品id', '商品名称', '类目', 'AI审核结果', 'AI拒绝原因', 
            'AI拒绝解释', '审核单结果', '人审拒绝项', '拒绝原因', 
            '人审意见', '店铺名称', '主图', '详情图', 'sku图', 
            'spu图', '商品链接', '实例编码', '创建日期'
        ]
        
        # 创建空DataFrame
        df = pd.DataFrame(columns=columns)
        
        # 保存到内存
        from io import BytesIO
        output = BytesIO()
        
        # 使用 openpyxl 引擎写入
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='导入模板')
        
        output.seek(0)
        
        # 返回文件
        from flask import send_file
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='template.xlsx'
        )
    
    except Exception as e:
        return jsonify({"success": False, "message": f"生成模板失败: {str(e)}"}), 500