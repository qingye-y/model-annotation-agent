# -*- coding: utf-8 -*-
from flask import Blueprint, request, jsonify
import requests
import random
import threading
import time
import os
from datetime import datetime
from models import db, RawData, FetchLog, SqlTemplate, SqlConfig
from blueprints.prompt_rules import read_rule_content, DEFAULT_EXTENSION
from services.fetch_service import extract_violation_keywords, extract_error_reason, update_daily_stats_inconsistency

model_review_bp = Blueprint('model_review', __name__)


def get_modelb_config():
    """从数据库获取模型B配置"""
    config = {}
    
    # 获取 API URL
    api_url = SqlConfig.query.filter_by(key='MODELB_API_URL').first()
    if api_url and api_url.value:
        config['api_url'] = api_url.value
    
    # 获取 API Key
    api_key = SqlConfig.query.filter_by(key='MODELB_API_KEY').first()
    if api_key and api_key.value:
        config['api_key'] = api_key.value
    
    # 获取模型名称
    model_name = SqlConfig.query.filter_by(key='MODELB_MODEL_NAME').first()
    if model_name and model_name.value:
        config['model_name'] = model_name.value
    else:
        config['model_name'] = 'gpt-4'
    
    # 获取并发数量（默认1，串行）
    concurrency = SqlConfig.query.filter_by(key='MODELB_CONCURRENCY').first()
    try:
        config['concurrency'] = int(concurrency.value) if concurrency and concurrency.value else 1
    except (ValueError, TypeError):
        config['concurrency'] = 1
    
    return config


def build_prompt(product_data, prompt_template, ai_result):
    """构建提示词，根据AI审核结果差异化处理"""

    # 提取主要拒绝点：使用统一的错误原因提取方法
    ai_reject_reason = product_data.get('ai_reject_reason', '')
    focus_point = extract_error_reason(ai_reject_reason) or ai_reject_reason[:20]
    
    if ai_result == '违规':
        # 违规数据：精简为只分析主要拒绝点
        prompt = f"""请审核以下商品的主要违规点。

商品名称：{product_data.get('product_name', '')}
类目：{product_data.get('category', '')}
AI审核结果：{ai_result}
主要违规点：{focus_point}

请返回严格的JSON格式：{{"result": "合规/违规", "reason": "简短的拒绝原因（不超过20字）", "detail": "详细的违规说明，包含具体依据"}}。如果审核通过，reason和detail可省略。不要输出任何其他评论性文字。"""
    else:
        # 合规数据：精简审核
        prompt = f"""请审核以下商品是否合规。

商品名称：{product_data.get('product_name', '')}
类目：{product_data.get('category', '')}
AI原审核结果：{ai_result}

请返回严格的JSON格式：{{"result": "合规/违规", "reason": "简短的拒绝原因（不超过20字）", "detail": "详细的审核说明"}}。如果审核通过，reason和detail可省略。不要输出任何其他评论性文字。"""

    # 统计传入的图片数量
    image_count = 0
    for field in ['main_image', 'detail_image', 'sku_image']:
        val = product_data.get(field, '')
        if val:
            image_count += len([u for u in val.split(',') if u.strip() and u.strip().startswith('http')])

    if image_count > 0:
        main_count = len([u for u in product_data.get('main_image','').split(',') if u.strip() and u.strip().startswith('http')]) if product_data.get('main_image') else 0
        detail_count = len([u for u in product_data.get('detail_image','').split(',') if u.strip() and u.strip().startswith('http')]) if product_data.get('detail_image') else 0
        sku_count = len([u for u in product_data.get('sku_image','').split(',') if u.strip() and u.strip().startswith('http')]) if product_data.get('sku_image') else 0
        prompt += f"\n\n⚠️ 请结合以上{image_count}张商品图片（主图{main_count}张 + 详情图{detail_count}张 + SKU图{sku_count}张）进行审核判断。注意图片中的商品信息、文字、水印、广告等细节。"

    return prompt


def call_modelb(product_data, prompt_template, api_config):
    """调用模型B API进行审核
    
    参数：
      - product_data: dict，包含商品数据
      - prompt_template: 提示词模板
      - api_config: dict，包含 api_url, api_key, model_name
    
    返回：{ "result": "合规/违规", "reason": "...", "detail": "..." } 或 { "error": "错误信息" }
    """
    import re
    
    ai_result = product_data.get('ai_result', '合规')
    
    # 构建提示词
    prompt = build_prompt(product_data, prompt_template, ai_result)
    
    # 检查是否配置了模型B
    if not api_config.get('api_url') or not api_config.get('api_key'):
        # Mock 模式
        return mock_modelb_result(product_data)
    
    # 收集所有图片 URL（主图 + 详情图 + SKU图，spu图不传）
    images = []
    for field in ['main_image', 'detail_image', 'sku_image']:
        val = product_data.get(field, '')
        if not val:
            continue
        for u in val.split(','):
            u = u.strip()
            if u and u.startswith('http'):
                images.append(u)

    # 构建 API 请求 — 图文混合 content
    if images:
        # 带图模式：content 为数组，含 text + image_url
        content = [{"type": "text", "text": prompt}]
        for img_url in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": img_url}
            })
        system_content = "你是一个商品审核专家，负责判断商品是否合规。请结合所有图片仔细检查商品的主图、详情图、SKU图。只需返回JSON，不要输出其他内容。"
    else:
        # 无图模式：content 为纯文本
        content = prompt
        system_content = "你是一个商品审核专家，负责判断商品是否合规。只需返回JSON，不要输出其他内容。"

    messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": content}
    ]

    payload = {
        "model": api_config.get('model_name', 'gpt-4'),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_config.get('api_key')}"
    }
    
    try:
        # 发送请求
        resp = requests.post(
            api_config['api_url'],
            json=payload,
            headers=headers,
            timeout=120  # 带图时需更长超时
        )
        resp.raise_for_status()

        result = resp.json()

        # 解析返回结果
        if 'choices' in result and len(result['choices']) > 0:
            content = result['choices'][0].get('message', {}).get('content', '')

            # 尝试解析 JSON 格式的返回
            import json as json_mod
            import re
            # 1. 先剥离 <think>...</think> 和 ```json...``` 等包裹
            clean_content = content
            # 去掉 <think>...</think> 块
            clean_content = re.sub(r'<think>.*?</think>', '', clean_content, flags=re.DOTALL)
            # 去掉 ```json ... ``` 包裹
            clean_content = re.sub(r'```(?:json)?\s*', '', clean_content)
            clean_content = re.sub(r'```', '', clean_content)
            clean_content = clean_content.strip()
            # 2. 匹配 JSON：找第一个 {...} 且有 result 字段
            json_match = re.search(r'\{[^{}]*"result"\s*:\s*"[^"]*"[^{}]*\}', clean_content, re.DOTALL)
            if not json_match:
                # 回退：找大括号块（支持换行内的简短JSON）
                json_match = re.search(r'\{[^{}]+"result"[^{}]+\}', clean_content, re.DOTALL)
            if json_match:
                try:
                    json_data = json_mod.loads(json_match.group())
                    modelb_result = json_data.get('result', '')
                    modelb_reason = json_data.get('reason', '')
                    modelb_detail = json_data.get('detail', '')
                    if modelb_result in ['合规', '违规']:
                        return {
                            "result": modelb_result,
                            "reason": modelb_reason[:50] if modelb_reason else '',  # 简短原因，不超过50字
                            "detail": modelb_detail[:1000] if modelb_detail else ''  # 详细说明，最多1000字
                        }
                except:
                    pass

            # JSON解析失败，使用文本判断
            # 判断结果
            if '合规' in content and '违规' not in content:
                modelb_result = '合规'
            elif '违规' in content:
                modelb_result = '违规'
            else:
                modelb_result = '合规'  # 默认合规

            return {
                "result": modelb_result,
                "reason": content[:50],  # 简短截取
                "detail": content[:1000]  # 详细说明
            }
        else:
            return {"error": "API返回格式异常"}
    
    except requests.exceptions.RequestException as e:
        # 请求失败，重试一次
        try:
            time.sleep(1)
            resp = requests.post(
                api_config['api_url'], 
                json=payload, 
                headers=headers, 
                timeout=120
            )
            resp.raise_for_status()
            result = resp.json()
            
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                
                if '合规' in content and '违规' not in content:
                    modelb_result = '合规'
                elif '违规' in content:
                    modelb_result = '违规'
                else:
                    modelb_result = '合规'
                
                return {
                    "result": modelb_result,
                    "reason": content[:50],
                    "detail": content[:1000]
                }
        except:
            pass
        
        # 重试也失败，使用 Mock
        return mock_modelb_result(product_data)
    
    except Exception as e:
        # 其他异常，使用 Mock
        return mock_modelb_result(product_data)


def mock_modelb_result(product_data):
    """Mock 模式：返回模拟的模型B结果
    
    不一致率在 8-15% 之间随机
    """
    ai_result = product_data.get('ai_result', '合规')
    
    # 8-15% 不一致率
    inconsistent_rate = random.uniform(0.08, 0.15)
    
    if random.random() < inconsistent_rate:
        # 不一致：AI结果为合规时，模型B返回违规；反之亦然
        if ai_result == '合规':
            modelb_result = '违规'
        else:
            modelb_result = '合规'
        reason = f"[Mock] 复审发现该商品{'应判定为违规' if modelb_result == '违规' else '应判定为合规'}"
        detail = f"[Mock] 经复审判断，该商品{'存在违规行为' if modelb_result == '违规' else '符合合规要求'}，与原审核结果不一致。"
    else:
        # 一致
        modelb_result = ai_result
        reason = "[Mock] 复审结果与AI审核一致"
        detail = "[Mock] 经复审判断，与AI原审核结果一致，无需额外说明。"
    
    return {
        "result": modelb_result,
        "reason": reason,
        "detail": detail
    }


def get_prompt_template(batch_id, instance_code):
    """获取提示词模板

    优先级：
    1. 从 SqlTemplate 获取 modelb_prompt
    2. 从 prompt_rules/ 目录读取 .md 文件（根据实例规则映射）
    3. 使用默认提示词
    """
    default_prompt = """请作为商品审核专家，判断以下商品是否合规。

商品名称：{product_name}
类目：{category}
店铺名称：{shop_name}
主图：{main_image}
AI审核结果：{ai_result}
AI拒绝原因：{ai_reject_reason}

请判断这个商品是否合规，只需回答"合规"或"违规"，并简要说明原因。"""

    # 1. 尝试从 SqlTemplate 获取
    if instance_code:
        template = SqlTemplate.query.filter(
            SqlTemplate.instances.contains(instance_code)
        ).first()

        if template and template.modelb_prompt:
            return template.modelb_prompt

    # 2. 尝试从 prompt_rules/ 目录读取 Markdown 文件
    if instance_code:
        rule_name = get_rule_name_by_instance(instance_code)
        if rule_name:
            rule_content = read_rule_content(rule_name)
            if rule_content:
                # 将 Markdown 规则转换为提示词
                return convert_rule_to_prompt(rule_content, instance_code)

    return default_prompt


def get_rule_name_by_instance(instance_code):
    """根据实例编码获取对应的规则名称

    从 INSTANCE_RULE_MAPPING 配置中查找
    """
    mapping_config = SqlConfig.query.filter_by(key='INSTANCE_RULE_MAPPING').first()

    default_mapping = {
        "ZJWC": "浙江网超审核规则",
        "HWCS": "浙江乐采网超审核规则",
        "YNLCY": "其他乐采网超审核规则",
        "GXLCY": "其他乐采网超审核规则",
        "HNLCWC": "其他乐采网超审核规则"
    }

    if mapping_config and mapping_config.value:
        try:
            import json
            mapping = json.loads(mapping_config.value)
            return mapping.get(instance_code, default_mapping.get(instance_code))
        except:
            pass

    return default_mapping.get(instance_code)


def convert_rule_to_prompt(rule_content, instance_code):
    """将 Markdown 规则内容转换为提示词格式

    提取 Markdown 中的标题和要点，组成提示词
    """
    if not rule_content:
        return None

    lines = rule_content.strip().split('\n')
    prompt_parts = ["请作为商品审核专家，根据以下审核规则判断商品是否合规。"]

    # 提取标题和内容
    in_section = None
    section_content = []

    for line in lines:
        line = line.strip()

        # Markdown 标题检测
        if line.startswith('#'):
            # 保存之前的章节
            if in_section and section_content:
                prompt_parts.append('- ' + in_section + ': ' + ' '.join(section_content[:2]))

            # 提取新章节标题
            in_section = line.lstrip('#').strip()
            section_content = []
        elif line.startswith('- ') or line.startswith('* '):
            # 列表项
            item = line.lstrip('- *').strip()
            if item and len(section_content) < 2:
                section_content.append(item)
        elif line and not line.startswith('##'):
            # 普通段落
            if len(section_content) < 2:
                section_content.append(line[:50])

    # 保存最后一个章节
    if in_section and section_content:
        prompt_parts.append('- ' + in_section + ': ' + ' '.join(section_content[:2]))

    # 添加商品数据占位符
    prompt_parts.append("\n待审核商品信息：")
    prompt_parts.append("商品名称：{product_name}")
    prompt_parts.append("类目：{category}")
    prompt_parts.append("店铺名称：{shop_name}")
    prompt_parts.append("主图：{main_image}")
    prompt_parts.append("AI审核结果：{ai_result}")
    prompt_parts.append("AI拒绝原因：{ai_reject_reason}")

    prompt_parts.append("\n请判断这个商品是否合规，只需回答'合规'或'违规'，并简要说明原因。")

    return '\n'.join(prompt_parts)


def run_modelb_review(batch_id):
    """异步执行模型B互检（支持并发）
    
    对指定批次的所有 RawData 记录调用模型B
    并发数从 SqlConfig MODELB_CONCURRENCY 读取，默认 1（串行）
    """
    from app import app
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    with app.app_context():
        print(f"[ModelB] 开始互检批次 {batch_id}")
        
        # 获取批次信息
        fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
        if not fetch_log:
            print(f"[ModelB] 批次 {batch_id} 不存在")
            return
        
        # 获取模型B配置（含并发数）
        api_config = get_modelb_config()
        concurrency = api_config.get('concurrency', 1)
        print(f"[ModelB] 并发数: {concurrency}")
        
        # 获取该批次下所有未互检的记录
        pending_records = RawData.query.filter_by(
            fetch_batch_id=batch_id,
            modelb_reviewed=False
        ).all()
        
        total = len(pending_records)
        
        if total == 0:
            print(f"[ModelB] 批次 {batch_id} 无需互检的数据")
            fetch_log.review_status = 'completed'
            db.session.commit()
            return
        
        # 更新互检状态
        fetch_log.review_status = 'running'
        db.session.commit()
        
        # 获取提示词模板
        instance_code = fetch_log.instances.split(',')[0] if fetch_log.instances else ''
        prompt_template = get_prompt_template(batch_id, instance_code)
        
        # 将记录转为 dict 列表（脱离 session），供工作线程使用
        record_dicts = []
        for rec in pending_records:
            record_dicts.append({
                'id': rec.id,
                'product_name': rec.product_name or '',
                'category': rec.category or '',
                'shop_name': rec.shop_name or '',
                'main_image': rec.main_image or '',
                'detail_image': rec.detail_image or '',
                'sku_image': rec.sku_image or '',
                'ai_result': rec.ai_result or '合规',
                'ai_reject_reason': rec.ai_reject_reason or '',
                'ai_explain': rec.ai_explain or '',
                'instance_code': rec.instance_code or ''
            })
        
        # 工作函数：单条记录处理
        def _review_single(rec_dict, batch_id, prompt_template, api_config):
            """处理单条记录（在独立线程中执行，拥有独立 DB session）"""
            from app import app as flask_app
            from models import db as worker_db, RawData as WorkerRawData
            
            with flask_app.app_context():
                rec_id = rec_dict['id']
                try:
                    product_data = {
                        'product_name': rec_dict['product_name'],
                        'category': rec_dict['category'],
                        'shop_name': rec_dict['shop_name'],
                        'main_image': rec_dict['main_image'],
                        'detail_image': rec_dict.get('detail_image', ''),
                        'sku_image': rec_dict.get('sku_image', ''),
                        'ai_result': rec_dict['ai_result'],
                        'ai_reject_reason': rec_dict['ai_reject_reason'],
                        'ai_explain': rec_dict['ai_explain'],
                        'instance_code': rec_dict['instance_code']
                    }
                    
                    # 调用模型B
                    result = call_modelb(product_data, prompt_template, api_config)
                    
                    # 写入数据库（独立 session）
                    record = WorkerRawData.query.get(rec_id)
                    if record and not record.modelb_reviewed:
                        if 'error' in result:
                            record.modelb_result = record.ai_result
                            record.modelb_reason = result.get('error', '调用失败')
                            record.modelb_consistent = True
                        else:
                            record.modelb_result = result.get('result', record.ai_result)
                            record.modelb_reason = result.get('reason', '')[:200]
                            record.modelb_detail = result.get('detail', '')[:2000]
                            record.modelb_consistent = (record.modelb_result == record.ai_result)
                        
                        record.modelb_reviewed = True
                        worker_db.session.commit()
                        
                        return {
                            'id': rec_id,
                            'consistent': record.modelb_consistent
                        }
                    return None
                
                except Exception as e:
                    print(f"[ModelB] 线程处理记录 {rec_id} 失败: {e}")
                    try:
                        with flask_app.app_context():
                            record = WorkerRawData.query.get(rec_id)
                            if record and not record.modelb_reviewed:
                                record.modelb_result = record.ai_result
                                record.modelb_reason = f"处理异常: {str(e)}"
                                record.modelb_consistent = True
                                record.modelb_reviewed = True
                                worker_db.session.commit()
                    except:
                        pass
                    return None
        
        # 并发执行（concurrency=1 时实际退化为串行）
        inconsistent_count = 0
        completed = 0
        
        if concurrency <= 1:
            # 串行模式（原逻辑）
            for i, rec_dict in enumerate(record_dicts):
                # 检查中止信号：先 expire 确保从 DB 读到最新值
                db.session.expire_all()
                fetch_log_check = FetchLog.query.filter_by(batch_id=batch_id).first()
                if fetch_log_check and fetch_log_check.abort_flag:
                    print(f"[ModelB] 批次 {batch_id} 收到中止信号，在第 {i+1}/{total} 条停止")
                    fetch_log_check.review_status = 'aborted'
                    fetch_log_check.abort_flag = False
                    db.session.commit()
                    return
                
                result = _review_single(rec_dict, batch_id, prompt_template, api_config)
                if result:
                    completed += 1
                    if not result.get('consistent', True):
                        inconsistent_count += 1
                    if completed % 10 == 0:
                        print(f"[ModelB] 批次 {batch_id} 进度: {completed}/{total}")
        else:
            # 并发模式
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                future_map = {}
                for rec_dict in record_dicts:
                    future = executor.submit(_review_single, rec_dict, batch_id, prompt_template, api_config)
                    future_map[future] = rec_dict['id']
                
                for future in as_completed(future_map):
                    # 每条完成后检查中止信号
                    db.session.expire_all()
                    fetch_log_check = FetchLog.query.filter_by(batch_id=batch_id).first()
                    if fetch_log_check and fetch_log_check.abort_flag:
                        print(f"[ModelB] 批次 {batch_id} 收到中止信号，取消剩余 {len(record_dicts) - completed} 条")
                        fetch_log_check.review_status = 'aborted'
                        fetch_log_check.abort_flag = False
                        db.session.commit()
                        # 取消未完成的任务
                        for f in future_map:
                            if not f.done():
                                f.cancel()
                        return
                    
                    result = future.result()
                    if result:
                        completed += 1
                        if not result.get('consistent', True):
                            inconsistent_count += 1
                        if completed % 10 == 0:
                            print(f"[ModelB] 批次 {batch_id} 进度: {completed}/{total}")
        
        # 更新批次统计
        fetch_log_final = FetchLog.query.filter_by(batch_id=batch_id).first()
        if fetch_log_final:
            # 重新精确统计不一致数
            actual_inconsistent = RawData.query.filter_by(
                fetch_batch_id=batch_id,
                modelb_reviewed=True,
                modelb_consistent=False
            ).count()
            fetch_log_final.inconsistent_count = actual_inconsistent or inconsistent_count
            fetch_log_final.review_status = 'completed'
            db.session.commit()
        
        # 写入 DailyStats 的不一致数据
        update_daily_stats_inconsistency(fetch_log_final or fetch_log)
        
        print(f"[ModelB] 批次 {batch_id} 互检完成，共 {actual_inconsistent if 'actual_inconsistent' in dir() else inconsistent_count} 条不一致")


# ========== API 接口 ==========

@model_review_bp.route('/api/model-review/trigger', methods=['POST'])
def api_trigger_review():
    """手动触发模型B互检
    
    接收 JSON：{ "batch_id": "BAT-xxx", "force": true/false }
    force=true 表示强制重新互检（先重置已有结果）
    """
    data = request.get_json()
    batch_id = data.get('batch_id')
    force = data.get('force', False)
    
    if not batch_id:
        return jsonify({"success": False, "message": "缺少批次号"}), 400
    
    # 检查批次是否存在
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({"success": False, "message": "批次不存在"}), 404
    
    # 如果是强制重新互检，先重置所有记录
    if force:
        RawData.query.filter_by(fetch_batch_id=batch_id).update({
            'modelb_reviewed': False,
            'modelb_result': None,
            'modelb_reason': None,
            'modelb_consistent': None
        })
        fetch_log.inconsistent_count = 0
        db.session.commit()
    
    # 检查是否已有正在运行的互检任务（强制模式下忽略）
    if not force and fetch_log.review_status == 'running':
        return jsonify({"success": False, "message": "该批次互检进行中，请稍后再试"}), 400
    
    # 更新互检状态为 running
    fetch_log.review_status = 'running'
    db.session.commit()
    
    # 启动异步任务
    threading.Thread(target=run_modelb_review, args=(batch_id,), daemon=True).start()
    
    return jsonify({
        "success": True,
        "message": "互检任务已提交"
    })


@model_review_bp.route('/api/model-review/abort/<batch_id>', methods=['PUT'])
def api_abort_review(batch_id):
    """中止指定批次的模型B互检

    将 abort_flag 设为 True，run_modelb_review 线程在下一条处理前检测到标志后停止
    """
    # expire 缓存确保读到 DB 最新值
    db.session.expire_all()
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({"success": False, "message": "批次不存在"}), 404

    if fetch_log.review_status != 'running':
        return jsonify({"success": False, "message": f"互检不在运行中（当前状态：{fetch_log.review_status}），无法中止"}), 400

    try:
        fetch_log.abort_flag = True
        db.session.commit()
        return jsonify({"success": True, "message": "中止信号已发送，线程将在当前条处理完毕后停止"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"中止失败: {str(e)}"}), 500


@model_review_bp.route('/api/model-review/status/<batch_id>', methods=['GET'])
def api_review_status(batch_id):
    """获取互检进度

    返回：{
        "total": N,
        "reviewed": M,
        "inconsistent": K,
        "status": "running/completed/aborted",
        "model_name": "MiniMax-M2.7",
        "prompt_info": "通用默认"
    }
    """
    # expire_all 确保读到 DB 最新值（worker 线程已更新的 review_status）
    db.session.expire_all()
    fetch_log = FetchLog.query.filter_by(batch_id=batch_id).first()
    if not fetch_log:
        return jsonify({
            "total": 0,
            "reviewed": 0,
            "inconsistent": 0,
            "status": "not_found",
            "model_name": "",
            "prompt_info": ""
        })

    # 统计已互检数量
    reviewed_count = RawData.query.filter_by(
        fetch_batch_id=batch_id,
        modelb_reviewed=True
    ).count()

    total = fetch_log.total_fetched or 0
    inconsistent = fetch_log.inconsistent_count or 0

    # 获取模型名称
    model_name = ""
    model_cfg = SqlConfig.query.filter_by(key='MODELB_MODEL_NAME').first()
    if model_cfg and model_cfg.value:
        model_name = model_cfg.value
    else:
        model_name = "Mock"

    # 获取提示词信息（根据 instances 匹配 SqlTemplate）
    prompt_info = "通用默认"
    if fetch_log.instances:
        first_instance = fetch_log.instances.split(',')[0].strip()
        template = SqlTemplate.query.filter(
            SqlTemplate.instances.contains(first_instance)
        ).first()
        if template and template.modelb_prompt:
            prompt_info = template.name or "通用默认"

    return jsonify({
        "total": total,
        "reviewed": reviewed_count,
        "inconsistent": inconsistent,
        "status": fetch_log.review_status or 'pending',
        "model_name": model_name,
        "prompt_info": prompt_info
    })


@model_review_bp.route('/api/config/modelb-test', methods=['POST'])
def api_modelb_test():
    """测试模型B连接
    
    发送测试请求验证 API Key 是否有效
    """
    data = request.get_json()
    api_url = data.get('api_url')
    api_key = data.get('api_key')
    model_name = data.get('model_name', 'gpt-4')
    
    if not api_url or not api_key:
        return jsonify({"success": False, "message": "请提供 API 地址和 Key"}), 400
    
    # 构建测试请求
    messages = [
        {"role": "system", "content": "你是一个商品审核专家。"},
        {"role": "user", "content": "请回复'测试成功'即可"}
    ]
    
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.1
    }
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        
        if resp.status_code == 200:
            result = resp.json()
            if 'choices' in result:
                return jsonify({"success": True, "message": "连接成功"})
        
        return jsonify({
            "success": False, 
            "message": f"请求失败，状态码: {resp.status_code}"
        })
    
    except requests.exceptions.RequestException as e:
        return jsonify({
            "success": False, 
            "message": f"连接失败: {str(e)}"
        })
    except Exception as e:
        return jsonify({
            "success": False, 
            "message": f"测试失败: {str(e)}"
        })


@model_review_bp.route('/api/config/set', methods=['POST'])
def api_config_set():
    """保存模型B配置到数据库
    
    接收 JSON：{ "key": "MODELB_xxx", "value": "..." }
    """
    data = request.get_json()
    key = data.get('key')
    value = data.get('value')
    
    if not key or value is None:
        return jsonify({"success": False, "message": "参数不完整"}), 400
    
    # 保存到 Config 表
    config = SqlConfig.query.filter_by(key=key).first()
    if config:
        config.value = value
    else:
        config = SqlConfig(key=key, value=value)
        db.session.add(config)
    
    db.session.commit()
    
    return jsonify({"success": True, "message": "保存成功"})


@model_review_bp.route('/api/config/get/<key>', methods=['GET'])
def api_config_get(key):
    """获取模型B配置（单key，兼容旧接口）"""
    config = SqlConfig.query.filter_by(key=key).first()
    
    if config:
        # 对于 Key 类型，只返回是否存在
        if key == 'MODELB_API_KEY':
            return jsonify({"exists": bool(config.value)})
        return jsonify({"value": config.value})
    
    return jsonify({"exists": False})


@model_review_bp.route('/api/config/modelb', methods=['GET'])
def api_modelb_get():
    """获取模型B完整配置
    
    返回：{
        "api_url": "...",
        "api_key": "***",
        "model_name": "...",
        "supplier": "...",
        "is_configured": true/false,
        "masked_key": "前5后5"
    }
    """
    # 读取各配置项
    api_url_cfg = SqlConfig.query.filter_by(key='MODELB_API_URL').first()
    api_key_cfg = SqlConfig.query.filter_by(key='MODELB_API_KEY').first()
    model_name_cfg = SqlConfig.query.filter_by(key='MODELB_MODEL_NAME').first()
    supplier_cfg = SqlConfig.query.filter_by(key='MODELB_SUPPLIER').first()
    concurrency_cfg = SqlConfig.query.filter_by(key='MODELB_CONCURRENCY').first()
    
    api_url = api_url_cfg.value if api_url_cfg else ''
    api_key = api_key_cfg.value if api_key_cfg else ''
    model_name = model_name_cfg.value if model_name_cfg else ''
    supplier = supplier_cfg.value if supplier_cfg else ''
    try:
        concurrency = int(concurrency_cfg.value) if concurrency_cfg and concurrency_cfg.value else 1
    except (ValueError, TypeError):
        concurrency = 1
    
    # 判断是否已经配置（以 api_key 是否有值为准）
    is_configured = bool(api_key)
    
    # 生成掩码 key
    if api_key and len(api_key) > 10:
        masked_key = api_key[:5] + '***' + api_key[-5:]
    elif api_key:
        masked_key = api_key[:2] + '***' + api_key[-2:] if len(api_key) > 4 else '***'
    else:
        masked_key = ''
    
    return jsonify({
        "api_url": api_url,
        "api_key": "***",
        "model_name": model_name,
        "supplier": supplier,
        "concurrency": concurrency,
        "is_configured": is_configured,
        "masked_key": masked_key
    })


@model_review_bp.route('/api/config/modelb', methods=['POST'])
def api_modelb_set():
    """保存模型B完整配置
    
    接收 JSON：{
        "api_url": "...",
        "api_key": "...",
        "model_name": "...",
        "supplier": "..."
    }
    如果 api_key 为空字符串，则保留数据库中已有的值（不覆盖）
    """
    data = request.get_json()
    api_url = data.get('api_url', '').strip()
    api_key = data.get('api_key', '').strip()
    model_name = data.get('model_name', '').strip()
    supplier = data.get('supplier', '').strip()
    concurrency = data.get('concurrency')
    
    if not api_url:
        return jsonify({"success": False, "message": "API 地址不能为空"}), 400
    if not model_name:
        return jsonify({"success": False, "message": "模型名称不能为空"}), 400
    
    # 保存 API URL
    cfg_url = SqlConfig.query.filter_by(key='MODELB_API_URL').first()
    if cfg_url:
        cfg_url.value = api_url
    else:
        db.session.add(SqlConfig(key='MODELB_API_URL', value=api_url))
    
    # 保存模型名称
    cfg_model = SqlConfig.query.filter_by(key='MODELB_MODEL_NAME').first()
    if cfg_model:
        cfg_model.value = model_name
    else:
        db.session.add(SqlConfig(key='MODELB_MODEL_NAME', value=model_name))
    
    # 保存供应商
    cfg_supplier = SqlConfig.query.filter_by(key='MODELB_SUPPLIER').first()
    if cfg_supplier:
        cfg_supplier.value = supplier
    else:
        db.session.add(SqlConfig(key='MODELB_SUPPLIER', value=supplier))
    
    # 保存并发数量
    if concurrency is not None:
        try:
            concurrency_val = str(int(concurrency))
        except (ValueError, TypeError):
            concurrency_val = '1'
        cfg_concurrency = SqlConfig.query.filter_by(key='MODELB_CONCURRENCY').first()
        if cfg_concurrency:
            cfg_concurrency.value = concurrency_val
        else:
            db.session.add(SqlConfig(key='MODELB_CONCURRENCY', value=concurrency_val))
    
    # 保存 API Key（仅当传入值非空时）
    cfg_key = SqlConfig.query.filter_by(key='MODELB_API_KEY').first()
    if api_key:
        if cfg_key:
            cfg_key.value = api_key
        else:
            db.session.add(SqlConfig(key='MODELB_API_KEY', value=api_key))
    elif not cfg_key or not cfg_key.value:
        # 传入为空且数据库也没有，视为未配置
        pass
    
    db.session.commit()
    
    # 重新读取判断状态
    final_key = SqlConfig.query.filter_by(key='MODELB_API_KEY').first()
    is_configured = bool(final_key and final_key.value)
    
    # 生成掩码 key
    raw_key = final_key.value if final_key else ''
    if raw_key and len(raw_key) > 10:
        masked_key = raw_key[:5] + '***' + raw_key[-5:]
    elif raw_key:
        masked_key = raw_key[:2] + '***' + raw_key[-2:] if len(raw_key) > 4 else '***'
    else:
        masked_key = ''
    
    return jsonify({
        "success": True,
        "message": "配置已保存",
        "is_configured": is_configured,
        "masked_key": masked_key,
        "supplier": supplier,
        "model_name": model_name,
        "concurrency": concurrency if concurrency is not None else 1
    })