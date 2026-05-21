# -*- coding: utf-8 -*-
"""单条带图互检测试脚本 — 含原始API响应"""
import sys, json
sys.path.insert(0, '.')

from app import app
from models import RawData
from blueprints.model_review import get_modelb_config, build_prompt, get_prompt_template
import requests

with app.app_context():
    rec = RawData.query.get(1)
    
    product_data = {
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
    }

    api_config = get_modelb_config()
    prompt_template = get_prompt_template(rec.fetch_batch_id or '', rec.instance_code or '')
    prompt = build_prompt(product_data, prompt_template, rec.ai_result or '合规')

    main_imgs = [u.strip() for u in (rec.main_image or '').split(',') if u.strip().startswith('http')]
    detail_imgs = [u.strip() for u in (rec.detail_image or '').split(',') if u.strip().startswith('http')]
    sku_imgs = [u.strip() for u in (rec.sku_image or '').split(',') if u.strip().startswith('http')]
    
    # 收集图片
    images = main_imgs + detail_imgs + sku_imgs
    
    # 构建图文混合 content
    content = [{"type": "text", "text": prompt}]
    for img_url in images:
        content.append({"type": "image_url", "image_url": {"url": img_url}})

    messages = [
        {"role": "system", "content": "你是一个商品审核专家，负责判断商品是否合规。请结合所有图片仔细检查。只需返回JSON，不要输出其他内容。"},
        {"role": "user", "content": content}
    ]

    payload = {
        "model": api_config.get('model_name', 'gpt-4'),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500
    }

    print('=' * 60)
    print(f'模型: {api_config.get("model_name")}')
    print(f'图片: {len(images)} 张 (主图{len(main_imgs)}+详情{len(detail_imgs)}+SKU{len(sku_imgs)})')
    print(f'\\n提示词 (前200字):')
    print(prompt[:200] + '...')
    print(f'\\n第1张图片: {images[0][:80]}')
    print(f'\\n--- 发送请求 ---')

    resp = requests.post(
        api_config['api_url'],
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_config.get('api_key')}"
        },
        timeout=120
    )

    print(f'HTTP状态码: {resp.status_code}')
    
    result = resp.json()
    print(f'\\n=== 原始响应 (精简) ===')
    if 'choices' in result and len(result['choices']) > 0:
        content_raw = result['choices'][0].get('message', {}).get('content', '')
        print(f'model_output: {content_raw[:500]}')
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
    
    if 'usage' in result:
        print(f'\\ntoken用量: {result["usage"]}')
    
    if 'error' in result:
        print(f'\\n❌ API错误: {result["error"]}')
