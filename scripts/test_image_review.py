# -*- coding: utf-8 -*-
"""单条带图互检测试脚本"""
import sys, json
sys.path.insert(0, '.')

from app import app
from models import RawData, SqlConfig
from blueprints.model_review import get_modelb_config, call_modelb, get_prompt_template

with app.app_context():
    # 1. 取一条有图的记录
    rec = RawData.query.get(1)
    if not rec:
        print('记录不存在')
        sys.exit(1)

    print('=' * 60)
    print(f'记录ID: {rec.id}')
    print(f'商品名: {rec.product_name[:50] if rec.product_name else "无"}')
    print(f'AI审核结果: {rec.ai_result}')

    # 图片统计
    main_imgs = [u.strip() for u in (rec.main_image or '').split(',') if u.strip()]
    detail_imgs = [u.strip() for u in (rec.detail_image or '').split(',') if u.strip()]
    sku_imgs = [u.strip() for u in (rec.sku_image or '').split(',') if u.strip()]
    print(f'图片: 主图{len(main_imgs)}张 + 详情{len(detail_imgs)}张 + SKU{len(sku_imgs)}张 = 共{len(main_imgs)+len(detail_imgs)+len(sku_imgs)}张')
    if main_imgs:
        print(f'主图[0]: {main_imgs[0]}')
    if detail_imgs:
        print(f'详情[0]: {detail_imgs[0]}')

    # 2. 构建 product_data
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

    # 3. 获取当前模型配置
    api_config = get_modelb_config()
    model_name = api_config.get('model_name', 'gpt-4')
    print(f'\n模型名: {model_name}')
    print(f'API URL: {api_config.get("api_url", "无")}' if api_config.get('api_url') else '当前为 Mock 模式')

    # 4. 获取提示词
    prompt_template = get_prompt_template(rec.fetch_batch_id or '', rec.instance_code or '')

    # 5. 调用模型B
    print('\n--- 开始调用模型B ---')
    result = call_modelb(product_data, prompt_template, api_config)
    print(f'\n=== 返回结果 ===')
    print(json.dumps(result, ensure_ascii=False, indent=2))
