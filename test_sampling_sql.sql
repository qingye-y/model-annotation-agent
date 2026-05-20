-- ============ 方案A：PARTITION BY 去重 + ROW_NUMBER 采样 ============
-- 两层窗口函数：_rn 去重，rn 采样
-- ZJWC 20260518 合规数据，采样第1、5、10个唯一审核id
SELECT ranked.* FROM (
  SELECT t.*, ROW_NUMBER() OVER (PARTITION BY t.`审核id` ORDER BY 1) as _rn,
         ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
  FROM (
    select
      shop_id as "供应商id",
      `标签`,
      cast(r.ai_result_id as varchar) as "AI审核id",
      cast(r.app_id as varchar) as "审核id",
      cast(r.goods_id as varchar) as "商品id",
      IF(r.ai_result = 1, '合规', '违规') as "AI审核结果",
      case
        when r.audit_status = 0 then '待人工审核'
        when r.audit_status = 3 then '人工审核通过'
        when r.audit_status = 4 then '人工审核不通过'
        when r.audit_status = 5 then '自动审核通过'
      end as "审核单结果",
      d.reject_field as "人审拒绝项",
      d.reject_detail as "拒绝原因",
      r.audit_idea as "人审意见",
      r.reject_reason as "AI拒绝原因",
      r.reject_detail as "AI拒绝解释",
      shop_name as "店铺名称",
      r.item_name as "商品名称",
      r.category_name as "类目",
      r.main_imgs as "主图",
      r.detail_imgs as "详情图",
      r.sku_imgs as "sku图",
      r.spu_imgs as "spu图",
      concat('https://www.zcygov.cn/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id) as "商品链接",
      r.check_result as "标注结果：1=正确  0=错误",
      r.annotation as "备注",
      r.instance_code as "实例编码",
      date_format(r.gmt_created_time,'%Y/%m/%d') as "创建日期",
      '' as "标注人",
      RAND() as "随机数",
      `变更类别`,
      r.gmt_created_time as "创建时间"
    from dwd.dwd_itm_audit_app_ai_result_detail_inc_y r
    left join dwd.dwd_itm_audit_reject_detail_y d on r.app_id = d.app_id and d.pt = '2026'
    LEFT JOIN (
      select instance_code, cast(item_id as varchar) as id, cast(shop_id as varchar) as shop_id, shop_name,
        CASE when publish_channel = 1 then 'PCWEB'
          when publish_channel = 3 then '网超协议价'
          when publish_channel = 17 then '全省联合采购'
          when publish_channel = 18 then '全省自行采购'
        end "标签"
      from dim.dim_itm_basic_info_detail_d
      where instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
    ) c on r.goods_id = c.id and r.instance_code = c.instance_code
    left join (
      select item_id,
        array_join(array_distinct(array_agg(audit_type_name)), ',') as "变更类别"
      from dwd.dwd_itm_audit_detail_inc_y
      where pt = '2026'
      and instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
      and substr(cast(gmt_created_time as varchar),1,10) between '20260518' and '20260518'
      and audit_status_name <> '已撤回'
      group by 1
    ) as new on new.item_id = r.goods_id
    where date_format(r.gmt_created_time,'%Y%m%d') between '20260518' and '20260518'
    and r.pt = '2026'
    and r.instance_code = 'ZJWC'
  ) t
  WHERE t.`AI审核结果` = '合规'
) ranked
WHERE ranked._rn = 1 AND ranked.rn IN (1,5,10)
;
