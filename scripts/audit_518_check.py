# -*- coding: utf-8 -*-
"""
518数据核查脚本 — 直接查询iData获取ZJWC/HWCS/HNLCWC 2026-05-18实际数据
绕过Flask，直接调服务层函数，对比各实例的COUNT和违规原因分布
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import ENV_CONFIG
from services.fetch_service import build_sql, execute_sql_query, ping_idata

INSTANCES = ['ZJWC', 'HWCS', 'HNLCWC']
ENV = '云环境'
DATE = '20260518'

def check_ping(instance):
    ok, msg = ping_idata(ENV, instance)
    print(f"[{instance}] ping: {ok} — {msg}")
    return ok

def fetch_count(instance):
    """直接COUNT(DISTINCT 审核id)查总数 + 分合规/违规"""
    start_fmt = DATE
    end_fmt = DATE
    detail_sql = build_sql(instance, start_fmt, end_fmt)
    count_sql = f"""SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
        FROM ({detail_sql}) t GROUP BY `AI审核结果`"""
    print(f"\n[{instance}] === COUNT查询 ===")
    print(f"[{instance}] SQL:\n{count_sql[:200]}...")
    try:
        result = execute_sql_query(count_sql, instance, ENV)
        print(f"[{instance}] COUNT结果: {result}")
        total = 0
        compliant = 0
        non_compliant = 0
        for row in result:
            cnt = int(row.get('cnt', 0) or 0)
            status = str(row.get('AI审核结果', '') or '')
            total += cnt
            if status in ('合规', '1', 'PASS'):
                compliant = cnt
            elif status in ('违规', '0', 'REJECT'):
                non_compliant = cnt
        print(f"[{instance}] 汇总: 合规={compliant}, 违规={non_compliant}, 总计={total}")
        return {'total': total, 'compliant': compliant, 'non_compliant': non_compliant}
    except Exception as e:
        print(f"[{instance}] COUNT查询失败: {e}")
        return None

def fetch_reasons(instance):
    """直接用聚合SQL查违规原因分布"""
    detail_sql = build_sql(instance, DATE, DATE)

    case_when = """CASE
    WHEN t.`AI拒绝原因` LIKE '%国旗%' OR t.`AI拒绝原因` LIKE '%党徽%' OR t.`AI拒绝原因` LIKE '%国徽%'
      OR t.`AI拒绝原因` LIKE '%党旗%' OR t.`AI拒绝原因` LIKE '%资质%'
      OR t.`AI拒绝原因` LIKE '%需提供%' OR t.`AI拒绝原因` LIKE '%批准%'
      OR t.`AI拒绝原因` LIKE '%文件%' THEN '特殊资质缺失'
    WHEN t.`AI拒绝原因` LIKE '%水印%' OR t.`AI拒绝原因` LIKE '%商贸%'
      OR t.`AI拒绝原因` LIKE '%商城%' OR t.`AI拒绝原因` LIKE '%贸易%'
      OR t.`AI拒绝原因` LIKE '%科技%' OR t.`AI拒绝原因` LIKE '%智汇选%' THEN '水印'
    WHEN t.`AI拒绝原因` LIKE '%马赛克%' THEN '马赛克'
    WHEN t.`AI拒绝原因` LIKE '%盗图%' THEN '盗图'
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
    WHEN t.`AI拒绝原因` LIKE '%禁售%' OR t.`AI拒绝原因` LIKE '%限制%'
      OR t.`AI拒绝原因` LIKE '%当前%' OR t.`AI拒绝原因` LIKE '%网超%'
      OR t.`AI拒绝原因` LIKE '%消防%' OR t.`AI拒绝原因` LIKE '%雨衣%'
      OR t.`AI拒绝原因` LIKE '%乐器%' OR t.`AI拒绝原因` LIKE '%证书%'
      OR t.`AI拒绝原因` LIKE '%定制%' OR t.`AI拒绝原因` LIKE '%垃圾桶%' THEN '禁售商品'
    WHEN t.`AI拒绝原因` LIKE '%词语%' OR t.`AI拒绝原因` LIKE '%违禁词%'
      OR t.`AI拒绝原因` LIKE '%生僻字%' OR t.`AI拒绝原因` LIKE '%繁体字%'
      OR t.`AI拒绝原因` LIKE '%字符%' OR t.`AI拒绝原因` LIKE '%乱码%'
      OR t.`AI拒绝原因` LIKE '%数字串%' OR t.`AI拒绝原因` LIKE '%字符串%'
      OR t.`AI拒绝原因` LIKE '%特殊符号%' OR t.`AI拒绝原因` LIKE '%意义%' THEN '标题无关词'
    WHEN t.`AI拒绝原因` LIKE '%ai生成%' OR t.`AI拒绝原因` LIKE '%疑似虚假%'
      OR t.`AI拒绝原因` LIKE '%虚假%' THEN 'AI生成'
    WHEN t.`AI拒绝原因` LIKE '%版权页%' OR t.`AI拒绝原因` LIKE '%书籍%'
      OR t.`AI拒绝原因` LIKE '%isbn%' OR t.`AI拒绝原因` LIKE '%出版%' THEN '书籍版权页'
    ELSE '其他'
    END"""

    # 用 DISTINCT 去重后的聚合SQL
    agg_sql = f"""SELECT `创建日期`, violation_tag, COUNT(*) as cnt
FROM (
  SELECT DISTINCT `审核id`, t.`创建日期`,
    {case_when} as violation_tag
  FROM (
    {detail_sql}
  ) t
  WHERE t.`AI审核结果` = '违规'
) tagged
GROUP BY `创建日期`, violation_tag
ORDER BY cnt DESC"""

    print(f"\n[{instance}] === 违规原因分布 ===")
    try:
        result = execute_sql_query(agg_sql, instance, ENV)
        print(f"[{instance}] 返回 {len(result)} 条")
        total_reasons = 0
        reasons = {}
        for row in result:
            tag = str(row.get('violation_tag', '') or '')
            cnt = int(row.get('cnt', 0) or 0)
            total_reasons += cnt
            reasons[tag] = cnt
            print(f"  {tag}: {cnt}")
        print(f"[{instance}] 违规原因总数: {total_reasons}")
        return reasons
    except Exception as e:
        print(f"[{instance}] 违规原因查询失败: {e}")
        return None

if __name__ == '__main__':
    from app import app
    with app.app_context():
        print("=" * 60)
        print("518数据核查 — 三实例对比")
        print("=" * 60)

        for inst in INSTANCES:
            print(f"\n{'='*40}")
            print(f"实例: {inst}")
            print(f"{'='*40}")
            if not check_ping(inst):
                print(f"[{inst}] ping失败，跳过")
                continue
            count_data = fetch_count(inst)
            reasons_data = fetch_reasons(inst)

        print("\n" + "=" * 60)
        print("核查完成")
