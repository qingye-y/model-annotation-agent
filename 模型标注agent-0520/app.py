# -*- coding: utf-8 -*-
from flask import Flask, render_template
from flask_login import LoginManager
from models import db, User, SqlTemplate
from config import SECRET_KEY, SQLALCHEMY_DATABASE_URI, SQLALCHEMY_TRACK_MODIFICATIONS
from werkzeug.security import generate_password_hash
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 上传文件大小限制 10MB

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

app.register_blueprint(auth_bp)
app.register_blueprint(data_fetch_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(sql_config_bp)
app.register_blueprint(model_review_bp)
app.register_blueprint(prompt_rules_bp)
app.register_blueprint(analysis_bp)

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
def page_dispatch():
    return render_template('dispatch_center.html')

@app.route('/annotation_list.html')
def page_annotation():
    return render_template('annotation_list.html')

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
            ("modelb_reviewed", "BOOLEAN DEFAULT 0")
        ]
        for col_name, col_type in raw_data_cols:
            try:
                db.session.execute(text(f"ALTER TABLE raw_data ADD COLUMN {col_name} {col_type}"))
                print(f"已添加列 {col_name}")
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

        db.session.commit()
    except Exception as e:
        print(f"数据库迁移检查完成: {e}")
    
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
            print(f"[启动清理] 批次 {task.batch_id} 已超时（fetch_time={task.fetch_time}），自动标记为失败")
        if stuck_tasks:
            db.session.commit()
            print(f"[启动清理] 已清理 {len(stuck_tasks)} 个超时任务")
    except Exception as e:
        print(f"[启动清理] 清理超时任务失败: {e}")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
