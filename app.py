# -*- coding: utf-8 -*-
from flask import Flask, render_template
from flask_login import LoginManager, current_user, login_required
from models import db, User, SqlTemplate
from config import SECRET_KEY, SQLALCHEMY_DATABASE_URI, SQLALCHEMY_TRACK_MODIFICATIONS
from werkzeug.security import generate_password_hash
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 上传文件大小限制 10MB

# 禁用模板缓存，确保每次请求都读取最新磁盘文件
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0


@app.after_request
def add_header(response):
    """禁用浏览器缓存，确保每次拿到最新资源"""
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


db.init_app(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'auth.login'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# 注册蓝图
from blueprints.auth import auth_bp
from blueprints.data_fetch import data_fetch_bp
from blueprints.dashboard import dashboard_bp
from blueprints.sql_config import sql_config_bp
from blueprints.model_review import model_review_bp
from blueprints.prompt_rules import prompt_rules_bp, init_default_rules
from blueprints.analysis import analysis_bp
from blueprints.dispatch import dispatch_bp
app.register_blueprint(auth_bp)
app.register_blueprint(data_fetch_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(sql_config_bp)
app.register_blueprint(model_review_bp)
app.register_blueprint(prompt_rules_bp)
app.register_blueprint(analysis_bp)
app.register_blueprint(dispatch_bp)

# ========== 静态页面路由 ==========
@app.route('/index.html')
def page_index():
    return render_template('index.html')

@app.route('/dashboard.html')
def page_dashboard():
    return render_template('dashboard.html')

@app.route('/model_task.html')
def page_model_task():
    return render_template('model_task.html')

@app.route('/batch-detail/<batch_id>')
def page_batch_detail(batch_id):
    return render_template('batch_detail.html')

@app.route('/dispatch_center.html')
@login_required
def page_dispatch():
    return render_template('dispatch_center.html', current_user_role=current_user.role)

@app.route('/annotation_list.html')
@login_required
def page_annotation():
    return render_template('annotation_list.html', user_role=current_user.role)

@app.route('/annotation_detail.html')
def page_annotation_detail():
    return render_template('annotation_detail.html')

@app.route('/qc_center.html')
def page_qc():
    return render_template('qc_center.html')

@app.route('/badcase_center.html')
def page_badcase():
    return render_template('badcase_center.html')

@app.route('/settings.html')
def page_settings():
    return render_template('settings.html')

@app.route('/account_management.html')
def page_account():
    return render_template('account_management.html')

@app.route('/sql_config.html')
def page_sql_config():
    return render_template('sql_config.html')

# ========== 系统配置子页面 ==========
@app.route('/rule_config.html')
def page_rule_config():
    return render_template('rule_config.html')

@app.route('/modelb_config.html')
def page_modelb_config():
    return render_template('modelb_config.html')

@app.route('/label_config.html')
def page_label_config():
    return render_template('label_config.html')

@app.route('/analysis_center.html')
def page_analysis_center():
    return render_template('analysis_center.html')

# ========== 初始化数据库并插入预设模板 ==========
with app.app_context():
    db.create_all()
    
    # 自动添加缺失的列（数据库迁移）
    from sqlalchemy import text
    try:
        # 检查并添加 sql_template 的 modelb_enabled 列
        try:
            db.session.execute(text("ALTER TABLE sql_template ADD COLUMN modelb_enabled BOOLEAN DEFAULT 0"))
            print("已添加列 modelb_enabled")
        except:
            pass  # 列已存在则忽略

        # 检查并添加 modelb_prompt 列
        try:
            db.session.execute(text("ALTER TABLE sql_template ADD COLUMN modelb_prompt TEXT"))
            print("已添加列 modelb_prompt")
        except:
            pass

        # 检查并添加 raw_data 的 modelb 相关列
        raw_data_cols = [
            ("modelb_result", "VARCHAR(20)"),
            ("modelb_reason", "TEXT"),
            ("modelb_consistent", "BOOLEAN DEFAULT 0"),
            ("modelb_reviewed", "BOOLEAN DEFAULT 0"),
            ("assigned_at", "TIMESTAMP"),
        ]
        for col_name, col_type in raw_data_cols:
            try:
                sql = "ALTER TABLE raw_data ADD COLUMN %s %s" % (col_name, col_type)
                db.session.execute(text(sql))
                print("已添加列 " + col_name)
            except:
                pass

        # 检查并添加 fetch_log 的 source 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN source VARCHAR(20) DEFAULT 'fetch'"))
            print("已添加列 source")
        except:
            pass

        # 检查并添加 fetch_log 的 inconsistent_count 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN inconsistent_count INTEGER DEFAULT 0"))
            print("已添加列 inconsistent_count")
        except:
            pass

        # 检查并添加 fetch_log 的 review_status 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN review_status VARCHAR(20) DEFAULT 'pending'"))
            print("已添加列 review_status")
        except:
            pass

        # 检查并添加 fetch_log 的 data_start_date 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN data_start_date VARCHAR(10)"))
            print("已添加列 data_start_date")
        except:
            pass

        # 检查并添加 fetch_log 的 data_end_date 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN data_end_date VARCHAR(10)"))
            print("已添加列 data_end_date")
        except:
            pass

        # 检查并添加 fetch_log 的 skipped_duplicates 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN skipped_duplicates INTEGER DEFAULT 0"))
            print("已添加列 skipped_duplicates")
        except:
            pass

        # 检查并添加 daily_stats 的 inconsistent_count 列
        try:
            db.session.execute(text("ALTER TABLE daily_stats ADD COLUMN inconsistent_count INTEGER DEFAULT 0"))
            print("已添加列 inconsistent_count")
        except:
            pass

        # 检查并添加 daily_stats 的 inconsistent_rate 列
        try:
            db.session.execute(text("ALTER TABLE daily_stats ADD COLUMN inconsistent_rate FLOAT DEFAULT 0.0"))
            print("已添加列 inconsistent_rate")
        except:
            pass

        # 检查并添加 fetch_log 的 task_generated 列（v1.4 任务调度模块）
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN task_generated BOOLEAN DEFAULT 0"))
            print("已添加列 task_generated")
        except:
            pass

        # 检查并添加 fetch_log 的 task_generate_time 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN task_generate_time VARCHAR(30)"))
            print("已添加列 task_generate_time")
        except:
            pass

        # 检查并添加 fetch_log 的 task_sample_percent 列
        try:
            db.session.execute(text("ALTER TABLE fetch_log ADD COLUMN task_sample_percent FLOAT DEFAULT 5.0"))
            print("已添加列 task_sample_percent")
        except:
            pass

        # 检查并添加 sql_template 的 category 列
        try:
            db.session.execute(text("ALTER TABLE sql_template ADD COLUMN category VARCHAR(50) DEFAULT 'detail'"))
            print("已添加列 category")
        except:
            pass

        db.session.commit()
    except Exception as e:
        print("数据库迁移检查完成: " + str(e))
    
    if not User.query.filter_by(username='admin').first():
        admin = User(
            username='admin',
            password_hash=generate_password_hash('admin123'),
            role='admin',
            name='管理员',
            daily_quota=0
        )
        db.session.add(admin)
        db.session.commit()

    # 初始化默认提示词规则文件
    init_default_rules()

    # ========== 初始化取数管道SQL模板（S2-S6）==========
    from models import SqlTemplate, FetchPipeline

    PIPELINE_SQLS = {
        'count': {
            'name': '取数-COUNT总数统计',
            'category': 'count',
            'sql_text': """SELECT `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
FROM ({detail_sql}) t
WHERE 1=1
GROUP BY `AI审核结果`"""
        },
        'daily': {
            'name': '取数-每日分组COUNT',
            'category': 'daily',
            'sql_text': """SELECT `创建日期`, `AI审核结果`, COUNT(DISTINCT `审核id`) as cnt
FROM ({detail_sql}) t
GROUP BY `创建日期`, `AI审核结果`
ORDER BY `创建日期`"""
        },
        'sample_compliant': {
            'name': '取数-合规数据翻页抽样',
            'category': 'sample',
            'sql_text': """SELECT * FROM (
  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '合规'
) tmp
WHERE tmp.rn IN ({positions})"""
        },
        'sample_non_compliant': {
            'name': '取数-违规数据翻页抽样',
            'category': 'sample',
            'sql_text': """SELECT * FROM (
  SELECT t.*, ROW_NUMBER() OVER (ORDER BY MD5(t.`审核id`)) as rn
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '违规'
) tmp
WHERE tmp.rn IN ({positions})"""
        },
        'reason': {
            'name': '取数-违规原因分布聚合',
            'category': 'reason',
            'sql_text': """SELECT `创建日期`, violation_tag, SUM(cnt) as cnt
FROM (
  SELECT `审核id`, `创建日期`,
    CASE
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
  END as violation_tag,
    COUNT(*) as cnt
  FROM ({detail_sql}) t
  WHERE t.`AI审核结果` = '违规'
  GROUP BY `审核id`, `创建日期`, violation_tag
) tagged
GROUP BY `创建日期`, violation_tag
ORDER BY `创建日期`, cnt DESC"""
        }
    }

    # 插入模板（幂等）
    for key, info in PIPELINE_SQLS.items():
        existing = SqlTemplate.query.filter_by(name=info['name']).first()
        if not existing:
            template = SqlTemplate(
                name=info['name'],
                env='云环境',
                instances='ZJWC,HWCS,HNLCWC,YNLCY,GXLCY',
                api_url='',
                sql_text=info['sql_text'],
                category=info['category']
            )
            db.session.add(template)
            db.session.flush()
            PIPELINE_SQLS[key]['_id'] = template.id
            print("已创建管道SQL模板: " + info['name'] + " (id=" + str(template.id) + ")")
        else:
            PIPELINE_SQLS[key]['_id'] = existing.id

    # 插入管道配置（幂等）
    PIPELINE_STEPS = [
        {'env': '云环境', 'order': 1, 'key': 'count', 'name': 'COUNT总数'},
        {'env': '云环境', 'order': 2, 'key': 'daily', 'name': '每日分组COUNT'},
        {'env': '云环境', 'order': 3, 'key': 'sample_compliant', 'name': '合规抽样'},
        {'env': '云环境', 'order': 4, 'key': 'sample_non_compliant', 'name': '违规抽样'},
        {'env': '云环境', 'order': 5, 'key': 'reason', 'name': '违规原因聚合'},
        {'env': '乐采云环境', 'order': 1, 'key': 'count', 'name': 'COUNT总数'},
        {'env': '乐采云环境', 'order': 2, 'key': 'daily', 'name': '每日分组COUNT'},
        {'env': '乐采云环境', 'order': 3, 'key': 'sample_compliant', 'name': '合规抽样'},
        {'env': '乐采云环境', 'order': 4, 'key': 'sample_non_compliant', 'name': '违规抽样'},
        {'env': '乐采云环境', 'order': 5, 'key': 'reason', 'name': '违规原因聚合'},
    ]
    for step in PIPELINE_STEPS:
        existing_pipe = FetchPipeline.query.filter_by(
            env=step['env'], sort_order=step['order']
        ).first()
        if not existing_pipe:
            pipe = FetchPipeline(
                env=step['env'],
                sort_order=step['order'],
                sql_template_id=PIPELINE_SQLS[step['key']]['_id'],
                step_name=step['name'],
                enabled=True
            )
            db.session.add(pipe)
            print("已创建管道步骤: " + step['env'] + " - " + step['name'])

    db.session.commit()
    print("取数管道初始化完成")

    # ========== §10 params_json 迁移：补充管道模板的系统注入参数 ==========
    import json as json_lib
    pipeline_tpls = SqlTemplate.query.filter(
        SqlTemplate.category.in_(['count', 'daily', 'sample', 'reason']),
        (SqlTemplate.params_json == None) | (SqlTemplate.params_json == '') | (SqlTemplate.params_json == '[]')
    ).all()

    for tpl in pipeline_tpls:
        # 找到对应的 S1 明细模板（同环境）
        detail_tpl = SqlTemplate.query.filter(
            SqlTemplate.category == 'detail',
            SqlTemplate.env == tpl.env
        ).first()
        detail_ref = "商品审核数据取数-" + str(tpl.env) + "(id=" + str(detail_tpl.id) + ")" if detail_tpl else "S1明细模板"

        params = [
            {"name": "detail_sql", "required": True, "system_injected": True,
             "description": "← 引用模板「" + str(detail_ref) + "」的展开SQL，系统自动注入"}
        ]

        if tpl.category == 'sample':
            params.append(
                {"name": "positions", "required": True, "system_injected": True,
                 "description": "← 系统计算的随机抽样行号列表，格式：1,15,23,44,..."}
            )

        tpl.params_json = json_lib.dumps(params, ensure_ascii=False)
        print("已补充params_json: " + tpl.name + " (id=" + str(tpl.id) + ")")

    db.session.commit()
    print("管道模板params_json迁移完成")

    # 初始化默认实例规则关联配置
    from models import SqlConfig
    import json as json_lib
    DEFAULT_INSTANCE_RULE_MAPPING = {
        "ZJWC": "浙江网超审核规则",
        "HWCS": "浙江乐采网超审核规则",
        "YNLCY": "其他乐采网超审核规则",
        "GXLCY": "其他乐采网超审核规则",
        "HNLCWC": "其他乐采网超审核规则"
    }
    existing_config = SqlConfig.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
    if not existing_config:
        new_config = SqlConfig(
            key='INSTANCE_RULE_MAPPING',
            value=json_lib.dumps(DEFAULT_INSTANCE_RULE_MAPPING, ensure_ascii=False)
        )
        db.session.add(new_config)
        db.session.commit()
        print("已初始化默认实例规则关联配置")

    # 检查并初始化预设SQL模板
    from config import ENV_CONFIG
    if SqlTemplate.query.count() == 0:
        # 云环境SQL模板
        cloud_sql = '''select
  shop_id as "供应商id",
  `标签`,
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
  shop_name as "店铺名称",
  r.item_name as "商品名称",
  r.category_name as "类目",
  r.main_imgs as "主图",
  r.detail_imgs as "详情图",
  r.sku_imgs as "sku图",
  r.spu_imgs as "spu图",
  case when r.instance_code in ('GXLCY','YNLCY')
    then concat('https://www.lecaiyun.com/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
    when r.instance_code in ('HWCS','ZJWC','HNLCWC')
    then concat('https://www.zcygov.cn/goods-center/goods/admin/audit?type=ADMINDETAIL&isAccess=true&agItemId=', r.goods_id, '&appId=', r.app_id)
  end as "商品链接",
  r.check_result as "标注结果：1=正确  0=错误",
  r.annotation as "备注",
  r.instance_code as "实例编码",
  date_format(r.gmt_created_time,'%Y/%m/%d') as "创建日期",
  '' as "标注人",
  RAND() as "随机数",
  `变更类别`,
  r.gmt_created_time as "创建时间"
from dwd.dwd_itm_audit_app_ai_result_detail_inc_y r
left join dwd.dwd_itm_audit_reject_detail_y d on r.app_id = d.app_id
LEFT JOIN (
  select
    instance_code,
    cast(item_id as varchar) as id,
    cast(shop_id as varchar) as shop_id,
    shop_name,
    CASE when publish_channel = 1 then 'PCWEB'
      when publish_channel = 3 then '开放平台'
      when publish_channel = 17 then '商家引用创建'
      when publish_channel = 18 then '页面添加卖场发布'
    end "标签"
  from dim.dim_itm_basic_info_detail_d
  where instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
) c on r.goods_id = c.id and r.instance_code = c.instance_code
left join (
  select
    item_id,
    array_join(array_distinct(array_agg(audit_type_name)), ',') as "变更类别"
  from dwd.dwd_itm_audit_detail_inc_y
  where pt = '${year}'
  and instance_code in ('GXLCY','YNLCY','HWCS','ZJWC','HNLCWC')
  and substr(cast(gmt_created_time as varchar),1,10) between '${start_date}' and '${end_date}'
  and audit_status_name <> '已撤回'
  group by 1
) as new on new.item_id = r.goods_id
where date_format(r.gmt_created_time,'%Y%m%d') between '${start_date}' and '${end_date}'
and r.pt = '${year}' and d.pt = '${year}'
and r.instance_code = '${instance}' '''

        cloud_params = json.dumps([
            {"name": "start_date", "default": "", "required": True},
            {"name": "end_date", "default": "", "required": True},
            {"name": "instance", "default": "", "required": True},
            {"name": "year", "default": "", "required": False}
        ], ensure_ascii=False)

        cloud_config = SqlTemplate(
            name="商品审核数据取数-云环境",
            env="云环境",
            instances="ZJWC,HWCS,HNLCWC",
            api_url=ENV_CONFIG["云环境"]["query_api_url"],
            sql_text=cloud_sql,
            params_json=cloud_params
        )
        db.session.add(cloud_config)

        # 乐采云环境SQL模板
        lcy_sql = cloud_sql  # SQL相同，仅实例和api_url不同

        lcy_params = json.dumps([
            {"name": "start_date", "default": "", "required": True},
            {"name": "end_date", "default": "", "required": True},
            {"name": "instance", "default": "", "required": True},
            {"name": "year", "default": "", "required": False}
        ], ensure_ascii=False)

        lcy_config = SqlTemplate(
            name="商品审核数据取数-乐采云环境",
            env="乐采云环境",
            instances="YNLCY,GXLCY",
            api_url=ENV_CONFIG["乐采云环境"]["query_api_url"],
            sql_text=lcy_sql,
            params_json=lcy_params
        )
        db.session.add(lcy_config)
        db.session.commit()
        print("预设SQL模板初始化完成")

    # ========== 启动时清理卡死的任务 ==========
    try:
        from models import FetchLog
        from datetime import datetime, timedelta
        timeout_threshold = datetime.utcnow() - timedelta(minutes=30)
        stuck_tasks = FetchLog.query.filter(
            FetchLog.status == 'running',
            FetchLog.fetch_time < timeout_threshold
        ).all()
        for task in stuck_tasks:
            task.status = 'failed'
            print("[启动清理] 批次 " + str(task.batch_id) + " 已超时（fetch_time=" + str(task.fetch_time) + "），自动标记为失败")
        if stuck_tasks:
            db.session.commit()
            print("[启动清理] 已清理 " + str(len(stuck_tasks)) + " 个超时任务")
    except Exception as e:
        print("[启动清理] 清理超时任务失败: " + str(e))


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
