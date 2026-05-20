# -*- coding: utf-8 -*-
"""extract_violation_keywords() 验证脚本
测试用例来源：青也提供，2026-05-16
"""
import json
import sys
sys.path.insert(0, '/Users/zcy/Desktop/模型标注agent')
from services.fetch_service import extract_violation_keywords

# ========== 验证用例 ==========
test_cases = [
    # (输入JSON, 预期输出)
    ('{"主图":"包含水印"}',                   ['水印']),
    ('{"商品主图":"含国旗标志"}',              ['特殊资质缺失']),
    ('{"商品名称":"标题存在类目错放"}',        ['类目错放']),
    ('{"商品图片":"多主体，含无关信息"}',     ['多主体', '无关信息']),
    ('{"详情页":"书籍未附版权页"}',            ['书籍版权页']),
    ('{"商品属性":"规格参数与实际不符"}',      ['关键属性不一致', '图文不一致']),  # 参数→关键属性，不符→图文不一致；同时匹配
    ('{"标题":"违禁词堆砌"}',                  ['品类词堆砌', '标题无关词']),  # "堆砌"→品类词堆砌，"违禁词"→标题无关词
    ('{"主图":"含京东旗舰店引流"}',            ['站外引流']),
    ('{"标题":"品类词堆砌，标题含生僻字"}',   ['品类词堆砌', '标题无关词']),
    ('{"商品图片":"Excel格式商品清单"}',       ['商品清单']),
    # 边界情况
    ('{}',                                     []),
    ('',                                       []),
    ('{"主图":"水印|图文不符"}',              ['水印', '图文不一致']),  # 多片段
    ('{"商品图片":"包含引流信息"}',            ['站外引流']),           # 引流关键词
]

print("=" * 60)
print("extract_violation_keywords() 验证结果")
print("=" * 60)

all_pass = True
for i, (input_str, expected) in enumerate(test_cases, 1):
    result = extract_violation_keywords(input_str)
    # 比较（忽略顺序）
    result_sorted = sorted(result)
    expected_sorted = sorted(expected)
    passed = result_sorted == expected_sorted
    if not passed:
        all_pass = False
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n用例{i}: {status}")
    print(f"  输入: {input_str}")
    print(f"  预期: {expected}")
    print(f"  实际: {result}")

print("\n" + "=" * 60)
if all_pass:
    print("🎉 全部通过！")
else:
    print("⚠️ 存在失败用例，请检查规则")
print("=" * 60)
