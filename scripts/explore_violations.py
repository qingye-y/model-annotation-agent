#!/usr/bin/env python3
"""
线上全量违规数据探查脚本
覆盖 5 个实例（ZJWC, HWCS, HNLCWC, YNLCY, GXLCY），
每个实例至少查询 10000 条违规记录，
解析 AI拒绝原因 字段，提取关键词并统计频率。
"""

import requests
import json
import re
import time
import jieba
from collections import Counter, defaultdict
from datetime import datetime, timedelta

# ============================================================
# 配置
# ============================================================
IDATA_COOKIE = ("_zcy_log_client_uuid=dd589ad0-2fab-11f0-8393-db541f25b0e9; _ga=GA1.1.473497680.1762493730; "
                 "_ga_Z4KXEBY4VP=GS2.1.s1766111120$o5$g1$t1766111143$j37$l0$h0; cna=7f8dea107e644b638388a7f4800ca0b5; "
                 "uid=C02049; redirect=; "
                 "Authorization=eyJhbGciOiJIUzUxMiJ9.eyJyZWFsTmFtZSI6IuadjuWtkOeOpSIsIm5pY2tuYW1lIjoi6Z2S5LmfIiwibW9iaWxl"
                 "IjoiMTMxNTUxODEwMzEiLCJlbXBsb3llZUlkIjoiQzAyMDQ5IiwiaWQiOjUwMTMzOSwic3lzQWRtaW4iOjAsImF2YXRhciI6Imh0"
                 "dHBzOi8vc3RhdGljLWxlZ2FjeS5kaW5ndGFsay5jb20vbWVkaWEvbFFEUE00Ukc4cFkxZXozTkE4Zk5BOGF3bHNXaFVkbWtHMGtI"
                 "YXBkQnhyQjlBQV85NjZfOTY3LmpwZyIsImV4cCI6MTc3ODk4MjYyMywiZW1haWwiOiJsaXppeXVlQGNhaS1pbmMuY29tIiwidX"
                 "Nlcm5hbWUiOiJsaXppeXVlIn0.aYECe1VIybKfCYeNswXa_UQntrRcFms7wHHGmFSKuyESzUqki-phdAcP90Z4LWBboC8lVRHo"
                 "1eOzpI-1T9euWg")

ENV_CONFIG = {
    "云环境": {
        "query_api_url": "https://idata.cai-inc.com/api/idas/inner/fetchData/getData",
        "instances": ["ZJWC", "HWCS", "HNLCWC"]
    },
    "乐采云环境": {
        "query_api_url": "https://idata.cai-inc.com/lcy_idas/api/idas/inner/fetchData/getData",
        "instances": ["YNLCY", "GXLCY"]
    }
}

IDATA_DATASOURCE_TYPE = "hive"

# 实例中文名
INSTANCE_NAMES = {
    "ZJWC": "浙江网超",
    "HWCS": "浙江乐采网超",
    "HNLCWC": "湖南乐采网超",
    "YNLCY": "云南乐采云",
    "GXLCY": "广西乐采云"
}

# 需要过滤的泛化词（无意义词汇）
STOPWORDS = {
    '商品', '图片', '主图', '详情图', 'sku图', 'spu图',
    '商品图', '信息', '内容', '问题', '情况', '存在',
    '包含', '具有', '属于', '符合', '不符合', '不合规',
    '不合格', '违规', '不合格', '无法', '不能', '不能',
    '不得', '不得使用', '不得含有', '不得包含',
    '的', '了', '是', '在', '和', '与', '或', '及',
    '等', '类', '等类', '其他', '以上', '以下', '包括',
    '一个', '一种', '一件', '一张', '一组', '一个以上',
    '不', '有', '无', '未', '已', '未', '被',
    '请', '请检查', '请核实', '请确认', '请修改',
    '请删除', '请重新', '请上传', '请填写',
    '未', '无法', '不能', '不得', '不应', '不能',
    '图', '图上', '图片中', '图片上', '图片内',
    '图内', '图中', '图片中', '商品图片', '商品图上',
    '上', '中', '内', '前', '后', '中',
    '类', '品类', '产品', '商品', '货', '货物',
    '件', '个', '张', '次', '条', '元', '米',
    '颜色', '款式', '型号', '规格', '尺寸', '大小',
    '品牌', '名称', '描述', '说明', '介绍', '标签',
    '来源', '来源地', '产地', '生产', '生产地',
    '进行', '处理', '操作', '使用', '显示', '展示',
    '上传', '下载', '打开', '查看', '浏览', '访问',
    '确认', '核实', '验证', '检查', '审核', '审批',
    '通过', '驳回', '通过', '合格', '通过',
    '成功', '失败', '错误', '异常', '正常', '正常',
    '请确认', '请检查', '注意', '提醒', '警告',
    '可能', '或者', '也许', '大概', '应该', '应当',
    '建议', '推荐', '要求', '必须', '务必', '一定',
    '可以', '能够', '允许', '允许', '允许',
    '仅', '只', '仅限', '仅允许', '仅限',
    '等', '等等', '等等', '如', '例如', '比如',
    '及', '和', '与', '以及', '及其',
    '为', '为了', '因', '因为', '由于', '所以',
    '但', '但是', '然而', '不过', '可是',
    '如果', '假如', '要是', '只要',
    '当', '当时', '当时', '在', '正在',
    '将', '将要', '将会', '即将',
    '从', '自', '由', '到', '至', '往',
    '比', '与', '和', '同', '跟',
    '这', '那', '此', '该', '本', '其',
    '之', '的', '所', '者', '地', '得',
    '而', '且', '并', '以及', '或', '还是',
    '若', '如', '若干', '若干个',
    '则', '即', '就', '便', '于是', '因此', '因而',
    '虽然', '尽管', '即使', '即便', '哪怕',
    '无论', '不论', '不管', '只要', '只有',
    '除了', '除非', '除', '去掉',
    '重新', '再次', '继续', '持续', '保持',
    '一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
    '第一', '第二', '第三', '第四', '第五',
    '万', '千', '百', '十', '个', '条', '件', '张', '次',
    'kg', 'g', 'ml', 'L', 'cm', 'm',
    'nan', 'null', 'none', 'undefined',
    'id', 'ID', 'Id',
}

# 自定义词典 - 防止jieba错误分词
CUSTOM_WORDS = [
    '违禁词', '违禁物品', '违禁商品', '敏感词', '敏感信息',
    '水印', '马赛克', '模糊图', '模糊图片',
    '类目错放', '错放类目', '类目放置错误', '类目错误',
    '虚假宣传', '夸大宣传', '过度宣传', '绝对化宣传',
    '价格违规', '价格欺诈', '虚假价格', '价格虚假',
    '资质缺失', '资质过期', '资质不符', '资质造假',
    '侵权商品', '知识产权侵权', '商标侵权', '专利侵权',
    '侵权', '盗版', '假冒', '山寨',
    '三无产品', '三无', '无生产日期', '无保质期', '无生产许可证',
    '食品', '保健品', '药品', '医疗器械',
    '医疗', '药品', '兽药', '农药',
    '色情', '低俗', '暴恐', '血腥', '赌博', '诈骗',
    '武器', '管制刀具', '仿真枪', '枪支',
    '野生动物', '濒危动物', '保护动物', '动物制品',
    '电子烟', '烟草', '香烟', '烟丝',
    '军火', '雷管', '炸药', '易燃易爆',
    '放射性', '有毒', '有害', '污染',
    '代购', '走私', '水货', '保税',
    '刷单', '炒信', '虚假交易',
    '七天无理由', '七天', '无理由退货',
    '发票', '专票', '普票', '电子发票',
    'CCC', '3C认证', 'CE认证', 'FDA', '认证',
    'QS', '生产许可证', '经营许可证', '营业执照',
    '有机', '绿色', '无公害', '地理标志',
    '地理标志', '证明商标', '集体商标',
    '最低价', '全网最低', '销量第一', '顶级',
    '最好', '最优', '最佳', '第一', '冠军',
    '原价', '划线价', '市场价', '指导价',
    '满减', '满折', '优惠券', '红包', '返现',
    '赠品', '买赠', '包邮', '运费险',
    '刷屏', '引流', '导流', '站外引流',
    '外链', '二维码', '微信', 'QQ', '网址', '链接',
    '联系方式', '电话', '手机号', '微信号', '二维码',
    '个人联系方式', '私人联系方式',
    '绝对化', '用语', '广告法', '极限词', '最优',
    '官网', '旗舰店', '专卖店', '专营店',
    '直销', '代购', '分销', '传销',
    '正品', '原装', '原产', '国产', '进口',
    '走私', '水货', '仿品', '高仿', '精仿',
    '克隆', '复制', '仿制', '假冒',
    '劣质', '残次', '损坏', '破损', '瑕疵',
    '过期', '临期', '失效', '变质', '腐败',
    '三无', '标识不全', '标签缺失', '信息缺失',
    '误导', '诱导', '欺骗', '欺诈', '陷阱',
    '退换货', '售后', '客服', '投诉', '差评',
    '好评', '信誉', '信用', '评分',
    '秒杀', '限时', '限量', '限购', '预售',
    '定金', '订金', '尾款', '全款',
    '分期', '花呗', '信用卡', '支付',
    '积分', '会员', 'VIP', '会员价',
    '广告', '推广', '投放', '投放',
    '软文', '种草', '测评', 'KOL', '网红',
    '直播', '短视频', '视频', '音频',
    '表情包', '贴纸', '滤镜', '美颜',
    'AI生成', '人工智能', 'AI', '机器生成',
    '盗图', '盗用', '抄袭', '搬运', '复制',
    '原创', '版权', '著作权', '备案',
    '分销商', '代理商', '经销商', '批发商',
    '窜货', '乱价', '控价', '价格体系',
    '囤货', '炒货', '倒卖', '黄牛',
    '虚假', '夸大', '绝对', '最优', '顶级',
    '违规词', '违禁语', '敏感词', '禁用词',
    '涉嫌', '疑似', '可能', '存在一定',
    '需提供', '需上传', '需补充', '需提交',
    '补充资质', '提供资质', '上传资质',
    '授权', '许可', '证明', '证书', '报告',
    '检测', '检验', '检疫', '检疫证',
    '卫生', '洁净', '无菌', '消毒', '灭菌',
    '婴幼儿', '儿童', '孕妇', '哺乳期', '特殊人群',
    '处方', 'OTC', '非处方', '处方药', '非处方药',
    '医疗器械', '医疗设备', '医用', '医疗',
    '保健食品', '保健', '功能性', '保健功能',
    '化妆品', '护肤品', '彩妆', '美妆', '个护',
    '特殊化妆品', '普通化妆品', '非特殊',
    '食品添加剂', '添加剂', '色素', '防腐剂', '抗氧化剂',
    '农残', '农药残留', '兽药残留', '重金属',
    '致癌', '致畸', '致突变', '有毒有害',
    '长生不老', '减肥', '降血糖', '降血压', '增强免疫力',
    '补肾', '补肾阳', '补气血', '壮阳', '催情',
    '治疗', '疗效', '治愈', '药用', '药效',
    '祖传', '秘方', '宫廷', '民间', '传统',
    '玄学', '风水', '算命', '占卜', '塔罗',
    '迷信', '邪教', '邪教组织', '恐怖', '血腥暴力',
    '赌博', '博彩', '彩票', '赌注',
    '毒品', '麻醉', '精神药品', '成瘾性',
    '现金贷', '校园贷', '高利贷', '网贷',
    '套路贷', '714高炮', '砍头息',
]
for word in CUSTOM_WORDS:
    jieba.add_word(word)


# ============================================================
# 工具函数
# ============================================================

def get_env_for_instance(instance):
    """根据实例编码确定环境"""
    if instance in ENV_CONFIG["云环境"]["instances"]:
        return "云环境"
    elif instance in ENV_CONFIG["乐采云环境"]["instances"]:
        return "乐采云环境"
    else:
        raise ValueError(f"未知实例: {instance}")


def fix_encoding(text):
    """修复 iData 返回的编码问题。

    iData 的 reject_reason 字段中文以 UTF-8 字节嵌入 JSON 转义序列
    （如 \\xe7\\xb1\\xbb）。requests.json() 解析后，这些字节变成 Python
    字符串中的 latin1 字节序列（\\xe7 等同于 chr(0xe7)）。

    正确解码路径：latin1（恢复原始字节）→ UTF-8（正确解码中文）。

    兜底：若 latin1->utf-8 仍失败，说明数据已损坏，用 errors='replace' 替代。
    """
    if not isinstance(text, str):
        text = str(text)
    try:
        return text.encode('latin1').decode('utf-8', errors='replace')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def execute_sql(sql, instance, env, timeout=300):
    """执行 SQL 查询"""
    api_url = ENV_CONFIG[env]["query_api_url"]
    headers = {
        'Content-Type': 'application/json',
        'Cookie': IDATA_COOKIE
    }
    payload = {
        'sql': sql,
        'instance': instance,
        'datasourceType': IDATA_DATASOURCE_TYPE
    }
    print(f"  [请求] 实例={instance}, SQL长度={len(sql)}")
    resp = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()

    # 关键修复：不使用 resp.json()（它在解析前会先 text.decode('utf-8')，
    # 导致 GBK 字节被当作 UTF-8 解码而失败）。
    # 改为手动获取原始字节，尝试 UTF-8 解码，失败则用 GBK 回退。
    raw = resp.content
    try:
        text = raw.decode('utf-8')
    except UnicodeDecodeError:
        text = raw.decode('gbk', errors='replace')
    result = json.loads(text)

    if isinstance(result, dict) and result.get('success') is False:
        error_msg = result.get('error', result.get('message', 'iData 接口返回错误'))
        raise Exception(f"iData 错误: {error_msg}")

    if isinstance(result, dict) and 'data' in result and isinstance(result['data'], dict):
        data_obj = result['data']
        if 'values' in data_obj:
            values = data_obj['values']
            # 修复每行 reject_reason 字段的编码问题
            for row in values:
                if isinstance(row, dict) and 'reject_reason' in row:
                    row['reject_reason'] = fix_encoding(row['reject_reason'])
            return values
        return data_obj
    elif isinstance(result, dict) and 'data' in result:
        return result['data']
    return result


def build_window_query(instance, year, start_date, end_date, page_start, page_end):
    """构建窗口函数分页SQL（Presto兼容）
    每次取 rn BETWEEN start AND end 的 500 条记录。
    使用 ROW_NUMBER() OVER (ORDER BY RANDOM()) 实现随机抽样。
    注意：pt 只有年分（如 '2026'），不带月日。
    """
    return f"""
    SELECT reject_reason
    FROM (
      SELECT reject_reason, ROW_NUMBER() OVER (ORDER BY RANDOM()) as rn
      FROM dwd.dwd_itm_audit_app_ai_result_detail_inc_y
      WHERE instance_code = '{instance}'
        AND pt = '{year}'
        AND date_format(gmt_created_time,'%Y-%m-%d') >= '{start_date}'
        AND date_format(gmt_created_time,'%Y-%m-%d') <= '{end_date}'
        AND ai_result = 0
    ) t
    WHERE rn BETWEEN {page_start} AND {page_end}
    """


def count_violations(instance, year, start_date, end_date):
    """统计违规记录总数"""
    sql = f"""
    SELECT COUNT(*) as cnt
    FROM dwd.dwd_itm_audit_app_ai_result_detail_inc_y r
    WHERE r.instance_code = '{instance}'
      AND r.pt = '{year}'
      AND date_format(r.gmt_created_time,'%Y-%m-%d') >= '{start_date}'
      AND date_format(r.gmt_created_time,'%Y-%m-%d') <= '{end_date}'
      AND r.ai_result = 0
    """
    result = execute_sql(sql, instance, get_env_for_instance(instance))
    if isinstance(result, list) and len(result) > 0:
        row = result[0]
        for key in ['cnt', 'COUNT(*)', 'count', 'total']:
            if key in row:
                return int(row[key])
    return 0


def parse_ai_reject_reason(raw_value):
    """解析 AI拒绝原因 字段，提取所有文本内容"""
    if not raw_value or raw_value in ('', 'None', 'null', 'None', 'nan'):
        return []

    text_parts = []

    # 尝试解析为 JSON
    try:
        data = json.loads(str(raw_value))
        if isinstance(data, dict):
            # 提取所有 value
            def extract_values(obj, results):
                if isinstance(obj, dict):
                    for v in obj.values():
                        extract_values(v, results)
                elif isinstance(obj, list):
                    for item in obj:
                        extract_values(item, results)
                elif isinstance(obj, str) and obj.strip():
                    results.append(obj.strip())
                elif obj is not None and not isinstance(obj, (dict, list)):
                    results.append(str(obj))

            extract_values(data, text_parts)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    text_parts.append(item.strip())
                elif isinstance(item, dict):
                    for v in item.values():
                        if isinstance(v, str):
                            text_parts.append(v.strip())
    except (json.JSONDecodeError, TypeError, ValueError):
        # 非JSON，直接作为文本处理
        text = str(raw_value).strip()
        if text:
            text_parts.append(text)

    return text_parts


def extract_keywords(text_parts):
    """从文本片段中提取关键词"""
    keywords = []

    for text in text_parts:
        if not text or len(text) < 2:
            continue

        # 使用 jieba 分词
        words = jieba.cut(text)

        for word in words:
            word = word.strip().lower()
            # 过滤条件
            if len(word) < 2:
                continue
            if word in STOPWORDS:
                continue
            # 过滤纯 ASCII 数字/字母/符号（\w 在 Python3 中含 Unicode 汉字，需限定 ASCII）
            if re.fullmatch(r'[\d\.\-\+\%\/\w]+', word) and not any('\u4e00' <= c <= '\u9fff' for c in word):
                continue
            # 过滤含有泛化词的短词（仅 3-4 字词，2 字业务词保留以防误杀）
            skip = False
            if 3 <= len(word) <= 4:
                for sw in STOPWORDS:
                    if len(sw) >= 2 and sw in word:
                        skip = True
                        break
            if skip:
                continue

            keywords.append(word)

    return keywords


def process_instance(instance, year, start_date, end_date, target_count=20000):
    """处理单个实例的违规数据探查"""
    print(f"\n{'='*60}")
    print(f"探查实例: {instance} ({INSTANCE_NAMES[instance]})")
    print(f"{'='*60}")

    env = get_env_for_instance(instance)

    # 1. 先统计违规总数
    print(f"  [1/4] 统计违规记录总数...")
    total_violations = count_violations(instance, year, start_date, end_date)
    print(f"  违规总数: {total_violations:,}")

    if total_violations == 0:
        print(f"  ⚠️ 该实例在指定日期范围内无违规记录！")
        return {
            'instance': instance,
            'name': INSTANCE_NAMES[instance],
            'total_violations': 0,
            'fetched_count': 0,
            'keywords': {},
            'raw_samples': [],
            'status': 'NO_DATA'
        }

    # 2. 拉取违规样本（窗口函数分页，每次500条）
    page_size = 500
    max_pages = 20  # 最多翻20页 = 10,000条
    actual_pages = min(max_pages, (min(target_count, total_violations) + page_size - 1) // page_size)
    actual_limit = actual_pages * page_size
    print(f"  [2/4] 拉取违规样本（目标: {target_count:,} 条，违规总数: {total_violations:,}，分 {actual_pages} 页，每页 {page_size} 条）...")

    all_rows = []
    page_start = 1
    page_end = page_size

    for page in range(1, actual_pages + 1):
        query = build_window_query(instance, year, start_date, end_date, page_start, page_end)
        print(f"    第 {page}/{actual_pages} 页 (rn {page_start}-{page_end})...", end='', flush=True)
        try:
            rows = execute_sql(query, instance, env, timeout=300)
            if rows:
                all_rows.extend(rows)
                print(f" 获取 {len(rows)} 条")
            else:
                print(f" 空（提前终止）")
                break
        except Exception as e:
            print(f" ❌ 失败: {e}")
            break

        page_start += page_size
        page_end += page_size
        time.sleep(1)  # 避免请求过快

    fetched_count = len(all_rows)
    print(f"  累计拉取: {fetched_count:,} 条")

    if fetched_count == 0:
        print(f"  ⚠️ 拉取结果为空！")
        return {
            'instance': instance,
            'name': INSTANCE_NAMES[instance],
            'total_violations': total_violations,
            'fetched_count': 0,
            'keywords': {},
            'raw_samples': [],
            'status': 'EMPTY_RESULT'
        }

    # 3. 解析 reject_reason，提取关键词
    print(f"  [3/4] 解析 reject_reason，提取关键词...")

    all_keywords = []
    raw_reasons = []

    for row in all_rows:
        # 窗口查询只返回 reject_reason，直接取原始值
        raw_reason = row.get('reject_reason', '') if isinstance(row, dict) else str(row)
        raw_reasons.append(raw_reason)

        text_parts = parse_ai_reject_reason(raw_reason)
        keywords = extract_keywords(text_parts)
        all_keywords.extend(keywords)

    # 统计关键词频率
    keyword_freq = Counter(all_keywords)
    print(f"  提取到关键词总数: {len(all_keywords):,}")
    print(f"  不同关键词种类: {len(keyword_freq):,}")
    print(f"  Top 10 关键词: {keyword_freq.most_common(10)}")

    # 4. 采样原始原因（保留前5条用于报告展示）
    print(f"  [4/4] 完成！")

    sample_reasons = []
    for i, r in enumerate(raw_reasons[:5]):
        text_parts = parse_ai_reject_reason(r)
        sample_reasons.append({
            'index': i + 1,
            'raw': str(r)[:200],
            'parsed': text_parts[:5]
        })

    return {
        'instance': instance,
        'name': INSTANCE_NAMES[instance],
        'total_violations': total_violations,
        'fetched_count': fetched_count,
        'keywords': dict(keyword_freq),
        'raw_samples': sample_reasons,
        'status': 'SUCCESS'
    }


def aggregate_results(results):
    """汇总所有实例的关键词频率"""
    global_freq = defaultdict(int)
    instance_contribution = defaultdict(lambda: defaultdict(int))

    for res in results:
        if res['status'] != 'SUCCESS':
            continue
        inst = res['instance']
        for kw, freq in res['keywords'].items():
            global_freq[kw] += freq
            instance_contribution[kw][inst] = freq

    return dict(global_freq), dict(instance_contribution)


def group_keywords(global_freq, instance_contribution):
    """对关键词进行归类"""
    # 定义关键词类别及其关键词集合
    categories = {
        "图片质量": {
            "水印", "马赛克", "模糊", "模糊图", "模糊图片",
            "遮挡", "截断", "不清晰", "不清楚", "画质低",
            "图片不清晰", "图片模糊", "有水印", "带水印",
            "主图水印", "图片有水印", "水印遮挡",
        },
        "违禁词/敏感词": {
            "违禁词", "敏感词", "禁用词", "违规词",
            "违禁语", "敏感信息", "敏感内容",
            "政治敏感", "涉政", "涉黄", "涉暴",
            "涉赌", "涉毒", "低俗", "色情", "暴恐",
            "血腥", "赌博", "毒品", "武器",
            "迷信", "玄学", "算命", "风水",
            "邪教", "恐怖",
        },
        "类目错放": {
            "类目错放", "错放类目", "类目错误", "类目放置错误",
            "类目不符", "类目不匹配", "放错类目",
            "类目乱放", "跨类目", "跨类",
        },
        "虚假/夸大宣传": {
            "虚假宣传", "夸大宣传", "过度宣传", "虚假广告",
            "夸大", "绝对化", "极限词", "最优", "最好",
            "顶级", "第一", "冠军", "销量第一", "全网最低",
            "最好", "最佳", "最优", "最便宜", "顶级",
            "夸大功效", "虚假功效", "功效夸大",
            "绝对化用语", "绝对化宣传",
        },
        "资质问题": {
            "资质缺失", "资质过期", "资质不符", "资质造假",
            "资质不全", "缺少资质", "无资质", "资质不完整",
            "授权缺失", "无授权", "授权过期",
            "许可缺失", "无许可证", "许可过期",
            "认证缺失", "无认证", "认证过期",
            "3C认证", "CCC", "CE认证", "FDA认证",
            "QS认证", "生产许可证", "经营许可证",
        },
        "价格违规": {
            "价格违规", "价格欺诈", "虚假价格", "价格虚假",
            "价格错误", "标价错误", "价格虚标",
            "原价虚高", "划线价", "最低价", "特价虚高",
            "价格不符", "价格异常",
        },
        "侵权/盗版": {
            "侵权", "盗版", "假冒", "山寨", "仿品",
            "高仿", "精仿", "盗图", "盗用图片",
            "知识产权侵权", "商标侵权", "专利侵权",
            "版权侵权", "抄袭", "复制", "仿制",
            "克隆", "搬运",
        },
        "三无/信息缺失": {
            "三无", "三无产品", "三无商品",
            "信息缺失", "信息不全", "信息不完整",
            "标签缺失", "标签不全", "标识缺失",
            "无生产日期", "无保质期", "无生产许可证",
            "无厂家", "无品牌", "无规格",
            "说明书缺失", "无说明书",
        },
        "食品/保健品违规": {
            "食品违规", "保健品违规", "药品违规",
            "保健食品", "保健功能", "功能性",
            "医疗功效", "治疗效果", "药用功效",
            "夸大保健功能", "虚假保健功能",
            "添加禁用物质", "食品添加剂", "添加剂违规",
            "农残超标", "农药残留", "兽药残留",
            "重金属超标", "有害物质",
            "致癌", "致畸", "致突变",
            "无有机认证", "虚假有机", "绿色食品违规",
            "婴幼儿食品违规", "特殊人群食品",
            "处方药", "OTC", "非处方药",
            "医疗器械违规", "医美违规",
        },
        "禁售商品": {
            "禁售", "禁售商品", "禁售商品类",
            "电子烟", "烟草", "香烟", "烟丝",
            "野生动物", "濒危动物", "保护动物",
            "动物制品", "象牙", "犀牛角",
            "军火", "雷管", "炸药", "易燃易爆",
            "放射性", "有毒有害", "污染",
            "现金贷", "校园贷", "高利贷", "网贷",
            "套路贷", "714高炮", "砍头息",
        },
        "违规促销": {
            "违规促销", "虚假促销", "促销违规",
            "刷单", "炒信", "虚假交易",
            "好评返现", "返现", "返红包",
            "虚假销量", "销量炒作",
            "站外引流", "外链", "二维码引流",
            "诱导交易", "刷屏",
        },
        "发票/税务问题": {
            "发票违规", "虚开发票", "发票问题",
            "专票违规", "普票违规", "电子发票违规",
            "偷税漏税", "税务问题",
        },
        "其他": {
            "其他", "其它", "其他问题",
            "不符合要求", "不合规", "不合格",
            "违规", "违规商品", "违规内容",
        }
    }

    # 建立关键词到类别的反向映射
    kw_to_category = {}
    for category, kws in categories.items():
        for kw in kws:
            kw_to_category[kw] = category

    # 统计每个类别的总频率
    category_stats = defaultdict(lambda: {'total': 0, 'keywords': defaultdict(int)})

    for kw, freq in global_freq.items():
        kw_lower = kw.lower()
        matched = False

        # 精确匹配
        if kw in kw_to_category:
            cat = kw_to_category[kw]
            category_stats[cat]['total'] += freq
            category_stats[cat]['keywords'][kw] = freq
            matched = True
        else:
            # 模糊匹配（关键词是类别的子串或反过来）
            for category_kw, cat in kw_to_category.items():
                if category_kw in kw or kw in category_kw:
                    category_stats[cat]['total'] += freq
                    category_stats[cat]['keywords'][kw] = freq
                    matched = True
                    break

        if not matched:
            category_stats['其他']['total'] += freq
            category_stats['其他']['keywords'][kw] = freq

    return dict(category_stats)


def generate_report(results, global_freq, instance_contribution, category_stats):
    """生成探查报告"""
    now = datetime.now().strftime('%Y年%m月%d日 %H:%M')

    # 计算总计
    total_fetched = sum(r['fetched_count'] for r in results)
    total_violations = sum(r['total_violations'] for r in results)
    success_count = sum(1 for r in results if r['status'] == 'SUCCESS')
    fail_count = sum(1 for r in results if r['status'] != 'SUCCESS')

    # 全局关键词排序
    sorted_keywords = sorted(global_freq.items(), key=lambda x: -x[1])

    report = f"""# 线上全量违规原因探查报告

> 生成时间：{now}
> 探查范围：ZJWC（浙江网超）、HWCS（浙江乐采网超）、HNLCWC（湖南乐采网超）、YNLCY（云南乐采云）、GXLCY（广西乐采云）
> 探查说明：每个实例随机抽取违规记录，解析 reject_reason 字段，提取关键词并统计频率

---

## 一、探查执行摘要

| 指标 | 数值 |
|------|------|
| 覆盖实例数 | 5 个 |
| 成功探查 | {success_count} 个 |
| 探查失败 | {fail_count} 个 |
| 违规记录总数（估算） | {total_violations:,} 条 |
| 实际拉取样本总数 | {total_fetched:,} 条 |
| 全局不同关键词数 | {len(global_freq)} 种 |

---

## 二、各实例探查结果

### 2.1 样本量详情

| 实例编码 | 实例名称 | 违规总数（估算） | 实际拉取 | 状态 |
|----------|----------|------------------|----------|------|
"""

    for r in results:
        status_map = {
            'SUCCESS': '✅ 成功',
            'NO_DATA': '⚠️ 无数据',
            'FETCH_FAILED': '❌ 拉取失败',
            'EMPTY_RESULT': '⚠️ 结果为空',
        }
        status_text = status_map.get(r['status'], r['status'])
        report += f"| {r['instance']} | {r['name']} | {r['total_violations']:,} | {r['fetched_count']:,} | {status_text} |\n"

    report += f"""
### 2.2 各实例高频关键词（Top 15）

"""

    for r in results:
        if r['status'] != 'SUCCESS':
            continue
        top15 = Counter(r['keywords']).most_common(15)
        report += f"""#### 【{r['instance']}】{r['name']}

| 排名 | 关键词 | 频次 |
|------|--------|------|
"""
        for i, (kw, freq) in enumerate(top15, 1):
            report += f"| {i} | {kw} | {freq:,} |\n"
        report += "\n"

    report += """---

## 三、全局关键词频率总榜

### 3.1 Top 50 高频关键词

| 排名 | 关键词 | 全局频次 | 出现实例数 |
|------|--------|----------|------------|
"""

    for i, (kw, freq) in enumerate(sorted_keywords[:50], 1):
        instances_with_kw = len(instance_contribution.get(kw, {}))
        report += f"| {i} | {kw} | {freq:,} | {instances_with_kw} |\n"

    report += """
### 3.2 完整关键词列表（按频次降序）

<details>
<summary>点击展开完整关键词列表（全部 {} 种）</summary>

""".format(len(sorted_keywords))

    for i, (kw, freq) in enumerate(sorted_keywords, 1):
        instances_with_kw = len(instance_contribution.get(kw, {}))
        inst_list = list(instance_contribution.get(kw, {}).keys())
        report += f"{i}. **{kw}** — {freq:,}次（{', '.join(inst_list)}）\n"

    report += """
</details>

---

## 四、违规原因归类建议

基于全局关键词频率分布，建议将违规原因归纳为以下 **{n} 大类**：

""".format(n=len(category_stats))

    # 按总频率排序类别
    sorted_categories = sorted(category_stats.items(), key=lambda x: -x[1]['total'])

    grand_total = sum(c['total'] for c in category_stats.values())

    for i, (cat_name, cat_data) in enumerate(sorted_categories, 1):
        cat_total = cat_data['total']
        cat_pct = (cat_total / grand_total * 100) if grand_total > 0 else 0
        top_kws = Counter(cat_data['keywords']).most_common(10)

        report += f"""### 4.{i} {cat_name}（{cat_total:,} 次，{cat_pct:.1f}%）

**包含关键词（Top 10）：**
"""

        for kw, freq in top_kws:
            report += f"- {kw}（{freq:,}）"
            insts = instance_contribution.get(kw, {})
            if insts:
                inst_names = [INSTANCE_NAMES.get(k, k) for k in insts.keys()]
                report += f" — {', '.join(inst_names)}"
            report += "\n"

        report += "\n"

    report += f"""---

## 五、兜底策略：低频关键词处理方案

### 5.1 "其他"类别的定义标准

对于无法归入上述 12 大类的违规原因，使用**"其他"**标签，并按以下优先级处理：

1. **单次出现且频次 < 5 的关键词**：直接归入"其他"
2. **无法识别具体违规类型的原始文本**：归入"其他-待确认"
3. **涉及多类问题的复合违规**：归入"其他-复合违规"

### 5.2 归类规则补充说明

| 场景 | 建议归类 | 备注 |
|------|----------|------|
| 涉及图片+文字双重问题 | 以主要违规原因为准 | 若难以区分，归入更严重类别 |
| 新出现的关键词（频次低） | 先归入"其他"，人工确认后迁移 | 建议定期review关键词库 |
| 同义词合并 | 保留最常用表述为标准词 | 例："类目错放"="类目放置错误" |
| 中英文混杂 | 统一为中文表述 | 例："CCC认证"="3C认证" |

---

## 六、数据质量说明

### 6.1 探查方法
- **查询范围**：最近 90 天数据
- **抽样方式**：每个实例按 `ROW_NUMBER() OVER (ORDER BY RANDOM())` 随机抽样，窗口翻页
- **样本策略**：每实例最多 20 页 × 500 条 = 10,000 条
- **分词工具**：jieba 中文分词 + 自定义词典

### 6.2 局限性
1. iData 查询有 180s 超时限制，大实例可能需分批次探查
2. 随机抽样存在一定随机误差，不代表完全均匀分布
3. AI拒绝原因字段为 JSON 格式，解析时可能存在格式不规范的脏数据
4. 部分违规原因为复合描述，关键词提取可能存在交叉重叠

### 6.3 后续建议
1. **定期探查**：建议每季度重新执行此探查，更新关键词库
2. **人工校验**：对 Top 20 高频关键词进行人工标注确认
3. **规则迭代**：基于本报告建立初始违规标签体系，后续持续优化
4. **数据增强**：对低频关键词补充人工样本，提升分类准确率

---

## 七、附录：原始样本数据（各实例前5条）

"""

    for r in results:
        if r['status'] != 'SUCCESS' or not r['raw_samples']:
            continue
        report += f"""### 【{r['instance']}】{r['name']} — 原始 reject_reason 样本

| # | 原始值（截断200字） | 解析出的文本片段 |
|---|---------------------|------------------|
"""
        for sample in r['raw_samples']:
            raw_display = sample['raw'].replace('\n', ' ').replace('\r', '')[:200]
            parsed = '；'.join(sample['parsed'][:5]) if sample['parsed'] else '(空)'
            report += f"| {sample['index']} | {raw_display} | {parsed} |\n"
        report += "\n"

    report += """
---

*本报告由自动化探查脚本生成，仅供分析参考，不涉及任何代码修改或数据写入操作。*
"""

    return report


# ============================================================
# 主程序
# ============================================================
if __name__ == '__main__':
    print("=" * 70)
    print("线上全量违规数据探查")
    print("=" * 70)

    # 设置探查日期范围（最近90天）
    end_date = datetime.now()
    start_date = end_date - timedelta(days=90)
    year = str(end_date.year)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = end_date.strftime('%Y-%m-%d')

    print(f"\n探查日期范围：{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')}（近90天）")
    print(f"探查实例：{list(INSTANCE_NAMES.keys())}")

    # 执行探查
    all_results = []
    instances = ["ZJWC", "HWCS", "HNLCWC", "YNLCY", "GXLCY"]

    for instance in instances:
        result = process_instance(
            instance,
            year=year,
            start_date=start_str,
            end_date=end_str,
            target_count=20000  # 每个实例目标（最多20页×500=10000条）
        )
        all_results.append(result)
        # 避免请求过快
        time.sleep(2)

    # 汇总分析
    print(f"\n\n{'='*70}")
    print("汇总分析")
    print(f"{'='*70}")

    global_freq, instance_contribution = aggregate_results(all_results)
    print(f"全局不同关键词种类: {len(global_freq)}")

    category_stats = group_keywords(global_freq, instance_contribution)
    print(f"归类类别数: {len(category_stats)}")

    # 生成报告
    report = generate_report(all_results, global_freq, instance_contribution, category_stats)

    # 保存报告
    output_path = '/Users/zcy/Desktop/模型标注agent/线上全量违规原因探查报告.md'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n报告已保存至: {output_path}")
    print("\n" + "=" * 70)
    print("探查完成！")
    print("=" * 70)
