# -*- coding: utf-8 -*-
"""检查单条带图互检返回的JSON解析"""
import sys, json, re
sys.path.insert(0, '.')

from app import app
from models import RawData
from blueprints.model_review import get_modelb_config, build_prompt, get_prompt_template, call_modelb

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

    print('模型:', api_config.get('model_name'))
    print()

    result = call_modelb(product_data, prompt_template, api_config)
    print('=== call_modelb 返回 ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 分析
    if result.get('result') in ['合规', '违规']:
        print(f'\n✅ 格式正确: result={result["result"]}')
        if result.get('reason'):
            print(f'   reason: {result["reason"]}')
        else:
            print('   ⚠️ reason为空 — 可能被think标签干扰')
        if result.get('detail'):
            print(f'   detail: {result["detail"]}')
        else:
            print('   ⚠️ detail为空')
    elif 'error' in result:
        print(f'\n❌ 调用失败: {result["error"]}')
    else:
        print(f'\n⚠️ 格式异常')
