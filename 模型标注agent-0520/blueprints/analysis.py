# -*- coding: utf-8 -*-
"""
智能分析中心蓝图
提供标注员问题分析、知识库搜索等功能
"""
from flask import Blueprint, jsonify, request
from models import db, User, Annotation, RawData, QcRecord, SqlTemplate, SqlConfig
from sqlalchemy import func, or_, and_
from datetime import datetime, timedelta
import random

analysis_bp = Blueprint('analysis', __name__, url_prefix='/api/analysis')


@analysis_bp.route('/annotator-issues', methods=['GET'])
def get_annotator_issues():
    """
    获取标注员问题分析数据
    返回：标注员统计、错误类型统计、一致性分析
    """
    try:
        # ========== 1. 标注员统计 ==========
        # 从Annotation表获取标注员标注数据，从QcRecord获取修正记录
        # 这里使用Mock数据演示，实际项目中应从数据库查询

        # Mock 标注员统计数据
        annotator_stats = [
            {
                "name": "张三",
                "group": "浙江网超审核规则",
                "total": 200,
                "corrected": 15,
                "correction_rate": 7.5,
                "top_error_tags": ["类目判断错误", "引流信息识别错误"],
                "trend": [5, 8, 2, 6, 4, 9, 3, 7, 5, 8]
            },
            {
                "name": "李四",
                "group": "浙江乐采网超审核规则",
                "total": 180,
                "corrected": 22,
                "correction_rate": 12.2,
                "top_error_tags": ["主图主体识别错误", "旗舰店识别错误"],
                "trend": [10, 12, 8, 15, 11, 9, 14, 16, 13, 22]
            },
            {
                "name": "王五",
                "group": "其他乐采网超审核规则",
                "total": 150,
                "corrected": 8,
                "correction_rate": 5.3,
                "top_error_tags": ["类目判断错误"],
                "trend": [3, 5, 4, 2, 6, 3, 4, 5, 6, 8]
            },
            {
                "name": "赵六",
                "group": "浙江网超审核规则",
                "total": 220,
                "corrected": 35,
                "correction_rate": 15.9,
                "top_error_tags": ["引流信息识别错误", "主图主体识别错误", "旗舰店识别错误"],
                "trend": [12, 18, 15, 20, 22, 19, 25, 28, 30, 35]
            },
            {
                "name": "钱七",
                "group": "浙江乐采网超审核规则",
                "total": 190,
                "corrected": 12,
                "correction_rate": 6.3,
                "top_error_tags": ["销售属性错误"],
                "trend": [4, 6, 5, 8, 7, 9, 6, 10, 8, 12]
            }
        ]

        # ========== 2. 错误类型统计 ==========
        error_type_stats = [
            {
                "tag": "类目判断错误",
                "count": 30,
                "annotator_count": 3,
                "main_rule": "浙江网超审核规则",
                "example_solution": "建议在提示词中明确主图主体判别标准，当主图包含多个商品时应标注为'混合'"
            },
            {
                "tag": "引流信息识别错误",
                "count": 25,
                "annotator_count": 4,
                "main_rule": "浙江网超审核规则",
                "example_solution": "引流信息包括公众号、微信号、QQ号等，需仔细检查商品详情图中的联系方式"
            },
            {
                "tag": "主图主体识别错误",
                "count": 18,
                "annotator_count": 2,
                "main_rule": "浙江乐采网超审核规则",
                "example_solution": "主图主体应为商品本身，若包含模特需明确是否影响主体判断"
            },
            {
                "tag": "旗舰店识别错误",
                "count": 15,
                "annotator_count": 3,
                "main_rule": "其他乐采网超审核规则",
                "example_solution": "旗舰店需同时满足：1.店铺名称包含'官方旗舰店' 2.提供完整资质证明"
            },
            {
                "tag": "销售属性错误",
                "count": 10,
                "annotator_count": 2,
                "main_rule": "浙江网超审核规则",
                "example_solution": "销售属性需与实际销售信息一致，如预售商品需标注为'预售'"
            }
        ]

        # ========== 3. 一致性分析 ==========
        # 统计同一规则下标注员之间的标注差异
        consistency = [
            {
                "rule": "浙江网超审核规则",
                "tag": "旗舰店识别错误",
                "annotator_diff_rate": 0.15,
                "details": "标注员A与B的判断差异率为15%，主要分歧在于授权店的判断"
            },
            {
                "rule": "浙江乐采网超审核规则",
                "tag": "主图主体识别错误",
                "annotator_diff_rate": 0.12,
                "details": "标注员C与D的判断差异率为12%，多主体商品判断标准不一致"
            },
            {
                "rule": "其他乐采网超审核规则",
                "tag": "类目判断错误",
                "annotator_diff_rate": 0.08,
                "details": "标注员E与F的判断差异率为8%，相对稳定"
            }
        ]

        # ========== 4. 顶部统计卡片数据 ==========
        total_annotators = len(annotator_stats)
        total_corrected = sum(a["corrected"] for a in annotator_stats)
        error_type_count = len(error_type_stats)
        # 修正率超过20%的标注员数
        focus_annotators = len([a for a in annotator_stats if a["correction_rate"] > 20])

        overview_stats = {
            "total_annotators": total_annotators,
            "total_corrected": total_corrected,
            "error_type_count": error_type_count,
            "focus_annotators": focus_annotators
        }

        return jsonify({
            "success": True,
            "data": {
                "overview": overview_stats,
                "annotator_stats": annotator_stats,
                "error_type_stats": error_type_stats,
                "consistency": consistency
            }
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@analysis_bp.route('/annotator-detail', methods=['GET'])
def get_annotator_detail():
    """
    获取指定标注员的详细错误案例
    query参数: annotator_name
    """
    annotator_name = request.args.get('annotator_name', '')

    if not annotator_name:
        return jsonify({
            "success": False,
            "error": "缺少标注员名称"
        }), 400

    # Mock 详细错误案例数据
    detail_cases = [
        {
            "id": 1,
            "product_name": "健身器材套装 家用多功能仰卧起坐板",
            "original_annotation": "错误",
            "corrected_annotation": "正确",
            "correction_reason": "主图为单一商品，符合合规要求",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-10 14:30:00"
        },
        {
            "id": 2,
            "product_name": "得力订书机省力型迷你",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "检测到详情图中含有引流信息",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-10 15:20:00"
        },
        {
            "id": 3,
            "product_name": "儿童lego积木拼装玩具",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "类目判断错误，应归类为玩具而非文具",
            "corrected_by": "质检员B",
            "corrected_at": "2026-05-11 09:15:00"
        },
        {
            "id": 4,
            "product_name": "Nike运动鞋男款跑步鞋",
            "original_annotation": "错误",
            "corrected_annotation": "正确",
            "correction_reason": "旗舰店识别正确",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-11 10:30:00"
        },
        {
            "id": 5,
            "product_name": "美的挂烫机家用蒸汽熨斗",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "主图主体识别错误，包含多个商品",
            "corrected_by": "质检员B",
            "corrected_at": "2026-05-11 11:45:00"
        },
        {
            "id": 6,
            "product_name": "戴森吸尘器V12",
            "original_annotation": "忽略",
            "corrected_annotation": "错误",
            "correction_reason": "不应忽略，需判断是否为品牌logo",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-11 14:00:00"
        },
        {
            "id": 7,
            "product_name": "九阳破壁机家用",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "标题检测到违禁词",
            "corrected_by": "质检员B",
            "corrected_at": "2026-05-12 09:30:00"
        },
        {
            "id": 8,
            "product_name": "海澜之家男士衬衫",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "检测到敏感标识",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-12 10:15:00"
        },
        {
            "id": 9,
            "product_name": "Apple iPhone 14手机壳",
            "original_annotation": "正确",
            "corrected_annotation": "错误",
            "correction_reason": "类目判断错误，应归类为手机配件而��数��",
            "corrected_by": "质检员B",
            "corrected_at": "2026-05-12 11:00:00"
        },
        {
            "id": 10,
            "product_name": "SK-II神仙水精华液",
            "original_annotation": "错误",
            "corrected_annotation": "正确",
            "correction_reason": "符合美妆类目要求",
            "corrected_by": "质检员A",
            "corrected_at": "2026-05-12 14:30:00"
        }
    ]

    # 根据标注员名称筛选（Mock逻辑）
    if annotator_name == "张三":
        cases = detail_cases[:6]
    elif annotator_name == "李四":
        cases = detail_cases[3:9]
    elif annotator_name == "王五":
        cases = detail_cases[:4]
    elif annotator_name == "赵六":
        cases = detail_cases[5:]
    elif annotator_name == "钱七":
        cases = detail_cases[2:7]
    else:
        cases = detail_cases[:10]

    return jsonify({
        "success": True,
        "data": {
            "annotator_name": annotator_name,
            "cases": cases,
            "total": len(cases)
        }
    })


@analysis_bp.route('/search', methods=['GET'])
def search_knowledge():
    """
    知识库跨表搜索
    搜索范围：
    - Annotation.note（标注备注）
    - QcRecord.solution（质检修正的解决方案）
    - SqlTemplate.prompt_text（提示词内容）
    - Config表中的标签说明等
    """
    q = request.args.get('q', '')

    if not q or len(q) < 2:
        return jsonify({
            "success": False,
            "error": "搜索关键词至少2个字符"
        }), 400

    # Mock搜索结果
    search_results = []

    # 1. 从标注备注中搜索
    annotation_results = [
        {
            "source_type": "annotation_note",
            "title": "标注备注记录 #1234",
            "summary": "标注员张三在商品PD10001上标注为'正确'，备注为空",
            "content": "annotation: 商品标注结果为正确，标注备注无",
            "link": "/annotation_list.html?id=1234"
        },
        {
            "source_type": "annotation_note",
            "title": "标注备注记录 #1567",
            "summary": "标注员李四标注为'错误'，标注备注：类目判断错误",
            "content": "annotation: 商品标注结果为错误，标注备注为'类目判断错误'",
            "link": "/annotation_list.html?id=1567"
        }
    ]
    search_results.extend(annotation_results)

    # 2. 从质检解决方案中搜索
    qc_results = [
        {
            "source_type": "qc_solution",
            "title": "质检修正解决方案 #QC20260508001",
            "summary": "针对'类目判断错误'的解决方案：建议明确主图主体判别标准",
            "content": "solution: 建议在提示词中明确主图主体判别标准，当主图包含多个商品时应标注为'混合'",
            "link": "/qc_center.html?batch=QC20260508001"
        },
        {
            "source_type": "qc_solution",
            "title": "质检修正解决方案 #QC20260507002",
            "summary": "针对'引流信息识别错误'的解决方案：需仔细检查详情图",
            "content": "solution: 引流信息包括公众号、微信号、QQ号等，需仔细检查商品详情图中的联系方式",
            "link": "/qc_center.html?batch=QC20260507002"
        }
    ]
    search_results.extend(qc_results)

    # 3. 从提示词模板中搜索
    prompt_results = [
        {
            "source_type": "prompt_template",
            "title": "浙江网超审核规则 - 标注提示词",
            "summary": "包含主图主体识别规则的提示词内容",
            "content": "prompt: 主图主体应为商品本身，若包含模特需明确是否影响主体判断...",
            "link": "/rule_config.html?rule=zjwc"
        },
        {
            "source_type": "prompt_template",
            "title": "旗舰店识别规则",
            "summary": "旗舰店识别判断标准及提示词",
            "content": "prompt: 旗舰店需同时满足：1.店铺名称包含'官方旗舰店' 2.提供完整资质证明",
            "link": "/rule_config.html?rule=flagship"
        }
    ]
    search_results.extend(prompt_results)

    # 4. 从配置表中搜索
    config_results = [
        {
            "source_type": "config",
            "title": "标签配置 - 错误类型说明",
            "summary": "系统标签配置中关于错误类型的说明",
            "content": "config: 类目判断错误 - 商品分类与实际不符; 引流信息识别错误 - 含有联系方式",
            "link": "/label_config.html"
        }
    ]
    search_results.extend(config_results)

    # 过滤匹配的结果（简单的Mock逻辑）
    matched_results = []
    keywords = q.lower()
    for item in search_results:
        if keywords in item["title"].lower() or keywords in item["summary"].lower():
            matched_results.append(item)

    # 如果没有匹配，返回所有结果（演示用）
    if not matched_results:
        matched_results = search_results[:4]

    return jsonify({
        "success": True,
        "data": {
            "query": q,
            "results": matched_results,
            "total": len(matched_results)
        }
    })


@analysis_bp.route('/knowledge-sources', methods=['GET'])
def get_knowledge_sources():
    """
    获取知识库内容源列表，用于知识库Tab展示
    """
    # Mock 知识库来源配置
    sources = [
        {
            "id": "badcase",
            "name": "Badcase解决方案",
            "description": "来自Badcase管理的解决方案汇总",
            "count": 156,
            "enabled": True
        },
        {
            "id": "qc_solution",
            "name": "质检修正记录",
            "description": "质检修正记录中的解决方案",
            "count": 89,
            "enabled": True
        },
        {
            "id": "error_type",
            "name": "标注员常犯错误类型",
            "description": "标注员常犯错误类型及其解释说明",
            "count": 12,
            "enabled": True
        },
        {
            "id": "prompt_rules",
            "name": "提示词规则",
            "description": "系统配置中的提示词规则内容",
            "count": 8,
            "enabled": True
        },
        {
            "id": "label_config",
            "name": "标签说明",
            "description": "标注标签配置及说明",
            "count": 25,
            "enabled": True
        }
    ]

    return jsonify({
        "success": True,
        "data": sources
    })


@analysis_bp.route('/overview', methods=['GET'])
def get_analysis_overview():
    """
    获取智能分析中心概览数据
    用于默认Tab的统计展示
    """
    # Mock 概览数据
    overview_data = {
        "total_annotations": 12450,
        "total_qc_records": 890,
        "correction_rate": 7.15,
        "pending_review": 45,
        "trend": {
            "dates": ["05-06", "05-07", "05-08", "05-09", "05-10", "05-11", "05-12"],
            "annotations": [1800, 1650, 1720, 1890, 2100, 1950, 2340],
            "corrections": [120, 135, 98, 145, 162, 148, 178]
        }
    }

    return jsonify({
        "success": True,
        "data": overview_data
    })