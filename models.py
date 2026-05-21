from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime

db = SQLAlchemy()

class User(UserMixin, db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username = db.Column(db.String(100), unique=True)
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), default='annotator')
    name = db.Column(db.String(100))
    daily_quota = db.Column(db.Integer, default=200)
    is_active = db.Column(db.Boolean, default=True)

class RawData(db.Model):
    __tablename__ = 'raw_data'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    supplier_id = db.Column(db.String(100))
    label = db.Column(db.String(200))
    ai_audit_id = db.Column(db.String(100))
    audit_id = db.Column(db.String(100))
    product_id = db.Column(db.String(100))
    ai_result = db.Column(db.String(20))
    audit_result = db.Column(db.String(50))
    human_reject_item = db.Column(db.String(200))
    reject_reason = db.Column(db.Text)
    human_comment = db.Column(db.Text)
    ai_reject_reason = db.Column(db.Text)
    ai_explain = db.Column(db.Text)
    shop_name = db.Column(db.String(200))
    product_name = db.Column(db.String(500))
    category = db.Column(db.String(200))
    main_image = db.Column(db.Text)
    detail_image = db.Column(db.Text)
    sku_image = db.Column(db.Text)
    spu_image = db.Column(db.Text)
    product_link = db.Column(db.Text)
    check_result = db.Column(db.String(50))
    annotation = db.Column(db.Text)
    instance_code = db.Column(db.String(50))
    created_date = db.Column(db.String(50))
    annotator = db.Column(db.String(100))
    random_num = db.Column(db.Float)
    change_category = db.Column(db.String(200))
    gmt_created = db.Column(db.String(50))
    fetch_batch_id = db.Column(db.String(100))
    source = db.Column(db.String(20), default='fetch')  # 数据来源：fetch（线上获取）/ upload（文件上传）
    modelb_result = db.Column(db.String(20))  # 模型B审核结果
    modelb_reason = db.Column(db.String(200))  # 模型B审核原因（简短）
    modelb_detail = db.Column(db.Text)  # 模型B审核详细说明
    modelb_consistent = db.Column(db.Boolean)  # 双模型是否一致
    modelb_reviewed = db.Column(db.Boolean, default=False)  # 是否已互检
    computed_error_reason = db.Column(db.String(200))  # 从AI拒绝原因提取的简短原因标签
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class FetchLog(db.Model):
    __tablename__ = 'fetch_log'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    batch_id = db.Column(db.String(100))
    env = db.Column(db.String(50))
    instances = db.Column(db.String(500))
    sample_percent = db.Column(db.Integer)
    total_fetched = db.Column(db.Integer)  # 抽样后拉取的条数
    original_total = db.Column(db.Integer, default=0)  # 线上原始总数
    original_compliant = db.Column(db.Integer, default=0)  # 线上原始合规数
    original_non_compliant = db.Column(db.Integer, default=0)  # 线上原始违规数
    compliant_count = db.Column(db.Integer)
    non_compliant_count = db.Column(db.Integer)
    inconsistent_count = db.Column(db.Integer, default=0)  # 双模型不一致数量
    fetch_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='completed')  # 数据拉取状态
    review_status = db.Column(db.String(20), default='pending')  # 互检状态：pending/running/completed/failed/aborted
    abort_flag = db.Column(db.Boolean, default=False)  # 互检中止标志，设为 True 时线程停止
    source = db.Column(db.String(20), default='fetch')  # 数据来源：fetch（线上获取）/ upload（文件上传）
    data_start_date = db.Column(db.String(10))  # 数据覆盖的开始日期，格式 YYYYMMDD
    data_end_date = db.Column(db.String(10))    # 数据覆盖的结束日期，格式 YYYYMMDD
    skipped_duplicates = db.Column(db.Integer, default=0)

class DailyStats(db.Model):
    __tablename__ = 'daily_stats'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    stat_date = db.Column(db.String(10), nullable=False)
    instance_code = db.Column(db.String(50), nullable=False)
    total_count = db.Column(db.Integer, default=0)
    compliant_count = db.Column(db.Integer, default=0)
    non_compliant_count = db.Column(db.Integer, default=0)
    inconsistent_count = db.Column(db.Integer, default=0, comment='机审不一致数量')
    inconsistent_rate = db.Column(db.Float, default=0.0, comment='机审不一致率')
    error_reasons = db.Column(db.Text)
    batch_id = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        # 数据库实际约束：(stat_date, instance_code) 两列唯一
        # 注意：batch_id 不参与唯一约束，同一日期+实例的多次拉取会覆盖而非累加batch_id
        db.UniqueConstraint('stat_date', 'instance_code', name='uq_daily_stats_date_instance'),
    )

class SqlConfig(db.Model):
    """通用配置表（key-value）"""
    __tablename__ = 'config'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SqlTemplate(db.Model):
    __tablename__ = 'sql_template'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), nullable=False)
    env = db.Column(db.String(50))
    instances = db.Column(db.String(500))
    api_url = db.Column(db.String(500))
    sql_text = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(50), default='detail', comment='类型：detail/count/sample/reason/daily')
    params_json = db.Column(db.Text)
    modelb_enabled = db.Column(db.Boolean, default=False)  # 是否启用模型B互检
    modelb_prompt = db.Column(db.Text)  # 模型B使用的提示词（如果为空则复用原始提示词）
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Annotation(db.Model):
    __tablename__ = 'annotation'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    raw_data_id = db.Column(db.Integer, db.ForeignKey('raw_data.id'))
    annotator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    result = db.Column(db.String(20))
    error_tag = db.Column(db.String(200))
    note = db.Column(db.Text)
    is_submitted = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class FetchPipeline(db.Model):
    """取数管道配置表 — 定义各环境的 SQL 执行顺序"""
    __tablename__ = 'fetch_pipeline'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    env = db.Column(db.String(50), nullable=False, comment='环境名：云环境/乐采云环境')
    sort_order = db.Column(db.Integer, nullable=False, default=0, comment='执行序号，1→2→3...')
    sql_template_id = db.Column(db.Integer, db.ForeignKey('sql_template.id'), nullable=False, comment='关联的SQL模板')
    step_name = db.Column(db.String(100), comment='步骤名，如"COUNT总数"、"合规抽样"')
    enabled = db.Column(db.Boolean, default=True, comment='是否启用此步骤')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    sql_template = db.relationship('SqlTemplate', backref='pipelines')


class QcRecord(db.Model):
    """质检修正记录表"""
    __tablename__ = 'qc_record'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    raw_data_id = db.Column(db.Integer, db.ForeignKey('raw_data.id'))
    annotation_id = db.Column(db.Integer, db.ForeignKey('annotation.id'))  # 原始标注ID
    annotator_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # 被质检的标注员ID
    qc_user_id = db.Column(db.Integer, db.ForeignKey('user.id'))  # 质检人ID（一般为admin）
    original_result = db.Column(db.String(20))  # 原标注结果：correct/error/ignore
    original_note = db.Column(db.Text)  # 原标注备注
    corrected_result = db.Column(db.String(20))  # 修正后结果
    corrected_note = db.Column(db.Text)  # 修正后备注
    solution = db.Column(db.Text)  # 解决方案/改进建议
    is_notified = db.Column(db.Boolean, default=False)  # 是否已通知标注员
    batch_id = db.Column(db.String(100))  # 质检批次号
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
