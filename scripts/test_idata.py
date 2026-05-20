#!/usr/bin/env python3
"""
iData API 完整链路测试 - 增加超时时间
"""
import requests
import json
from config import IDATA_COOKIE, IDATA_DATASOURCE_TYPE

# 完整取数 SQL
TEST_SQL = """
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
  where pt = '2026'
  and instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
  and substr(cast(gmt_created_time as varchar),1,10) between '2026-05-10' and '2026-05-10'
  and audit_status_name <> '已撤回'
  group by 1
) as new on new.item_id = r.goods_id
where date_format(r.gmt_created_time,'%Y%m%d') between '20260510' and '20260510'
and r.pt = '2026' and d.pt = '2026'
and r.instance_code = 'ZJWC'
limit 5
"""

HEADERS = {
    'Content-Type': 'application/json',
    'Cookie': IDATA_COOKIE
}

if __name__ == '__main__':
    print("=" * 60)
    print("iData 完整取数 SQL 测试（超时 180 秒）")
    print("=" * 60)

    url = "https://idata.cai-inc.com/api/idas/inner/fetchData/getData"
    payload = {
        'sql': TEST_SQL,
        'instance': 'ZJWC',
        'datasourceType': IDATA_DATASOURCE_TYPE
    }

    print(f"URL: {url}")
    print(f"datasourceType: {IDATA_DATASOURCE_TYPE}")
    print("-" * 60)

    try:
        resp = requests.post(url, json=payload, headers=HEADERS, timeout=180)
        print(f"状态码: {resp.status_code}")
        try:
            result = resp.json()
            if result.get('success') and 'data' in result and 'values' in result['data']:
                values = result['data']['values']
                print(f"\n成功！返回 {len(values)} 条数据")
                print(f"总条数: {result['data'].get('count', 'N/A')}")
                print(f"\n第一条数据:")
                print(json.dumps(values[0], ensure_ascii=False, indent=2))
            else:
                print(f"\n响应:\n{json.dumps(result, ensure_ascii=False, indent=2)[:2000]}")
        except:
            print(f"响应不是 JSON: {resp.text[:500]}")

    except requests.exceptions.Timeout:
        print("请求超时 (180s)。完整 SQL 执行时间太长。")
        print("建议：")
        print("  1. 在 iData 上确认这个 SQL 的执行时间")
        print("  2. 如果确实很慢，考虑简化 SQL（减少 JOIN 或缩短日期范围）")
        print("  3. 或者把 timeout 配置为更长")
    except Exception as e:
        print(f"请求失败: {e}")
