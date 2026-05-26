from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
import re
import json
import requests
from datetime import datetime
from config import ENV_CONFIG, IDATA_COOKIE as DEFAULT_COOKIE, IDATA_DATASOURCE_TYPE
from models import db, SqlTemplate, SqlConfig

sql_config_bp = Blueprint('sql_config', __name__)

# 敏感配置项列表（返回时自动掩码处理）
SENSITIVE_KEYS = {'IDATA_COOKIE', 'MODELB_API_KEY'}


def mask_value(key, value):
    """对敏感配置值进行掩码处理

    规则：
    - 如果 value 长度 <= 10，返回 "***"
    - 如果 value 长度 > 10，返回前5位 + "****" + 后5位
    - 非敏感 key 直接返回原值
    """
    if key not in SENSITIVE_KEYS or not value:
        return value
    if len(value) <= 10:
        return '***'
    return value[:5] + '****' + value[-5:]


def scan_params(sql_text):
    """扫描SQL中的${参数名}占位符，返回参数列表"""
    pattern = r'\$\{([^}]+)\}'
    matches = re.findall(pattern, sql_text)
    return list(set(matches))


def replace_params(sql_text, params):
    """替换SQL中的占位符"""
    result = sql_text
    for key, value in params.items():
        result = result.replace('${' + key + '}', str(value))
    return result


def execute_sql_query(sql, instance, env):
    """执行SQL查询，返回JSON数据"""
    api_url = ENV_CONFIG[env]['query_api_url']
    # 优先从数据库读取 Cookie，否则使用默认配置
    cookie = get_cookie_from_db()
    headers = {
        'Content-Type': 'application/json',
        'Cookie': cookie
    }
    payload = {
        'sql': sql,
        'instance': instance,
        'datasourceType': IDATA_DATASOURCE_TYPE
    }
    resp = requests.post(api_url, json=payload, headers=headers, timeout=180)
    resp.raise_for_status()
    result = resp.json()

    if isinstance(result, dict):
        if 'data' in result and isinstance(result['data'], dict):
            data_obj = result['data']
            if 'values' in data_obj:
                return data_obj['values']
            return data_obj
        elif 'data' in result:
            return result['data']
        elif 'result' in result:
            return result['result']
    return result


@sql_config_bp.route('/api/sql-config', methods=['GET'])
def api_sql_config_list():
    """获取所有SQL配置列表
    
    支持 modelb_enabled 筛选参数
    """
    env = request.args.get('env', '')
    modelb_enabled = request.args.get('modelb_enabled', '')
    category = request.args.get('category', '')
    
    query = SqlTemplate.query
    if env:
        query = query.filter(SqlTemplate.env == env)
    if category:
        query = query.filter(SqlTemplate.category == category)
    if modelb_enabled != '':
        query = query.filter(SqlTemplate.modelb_enabled == (modelb_enabled == 'true'))
    
    configs = query.order_by(SqlTemplate.updated_at.desc()).all()
    
    result = []
    for c in configs:
        result.append({
            'id': c.id,
            'name': c.name,
            'env': c.env,
            'instances': c.instances,
            'api_url': c.api_url,
            'sql_template': c.sql_text,
            'params_json': c.params_json,
            'category': c.category or 'detail',
            'modelb_enabled': c.modelb_enabled or False,
            'modelb_prompt': c.modelb_prompt or '',
            'created_at': c.created_at.strftime('%Y-%m-%d %H:%M:%S') if c.created_at else '',
            'updated_at': c.updated_at.strftime('%Y-%m-%d %H:%M:%S') if c.updated_at else ''
        })
    
    return jsonify({'configs': result})


@sql_config_bp.route('/api/sql-config/<int:id>', methods=['GET'])
def api_sql_config_detail(id):
    """获取指定SQL配置的详情"""
    config = SqlTemplate.query.get(id)
    if not config:
        return jsonify({'success': False, 'message': '配置不存在'}), 404

    return jsonify({
        'success': True,
        'id': config.id,
        'name': config.name,
        'env': config.env,
        'instances': config.instances,
        'api_url': config.api_url,
        'sql_text': config.sql_text,
        'params_json': config.params_json,
        'category': config.category or 'detail',
        'modelb_enabled': config.modelb_enabled or False,
        'modelb_prompt': config.modelb_prompt or '',
        'created_at': config.created_at.strftime('%Y-%m-%d %H:%M:%S') if config.created_at else '',
        'updated_at': config.updated_at.strftime('%Y-%m-%d %H:%M:%S') if config.updated_at else ''
    })


@sql_config_bp.route('/api/sql-config', methods=['POST'])
def api_sql_config_create():
    """新增SQL配置"""
    data = request.get_json()
    
    name = data.get('name', '')
    env = data.get('env', '')
    instances = data.get('instances', '')
    api_url = data.get('api_url', '')
    sql_text = data.get('sql_template', '')
    params_json = data.get('params_json', '[]')
    category = data.get('category', 'detail')
    modelb_enabled = data.get('modelb_enabled', False)
    modelb_prompt = data.get('modelb_prompt', '')
    
    if not name or not env or not sql_text:
        return jsonify({'success': False, 'message': '必填字段不能为空'}), 400
    
    # 如果没有提供api_url，自动填充
    if not api_url and env in ENV_CONFIG:
        api_url = ENV_CONFIG[env].get('query_api_url', '')
    
    # 确保params_json是JSON字符串
    if isinstance(params_json, list):
        params_json = json.dumps(params_json, ensure_ascii=False)
    
    config = SqlTemplate(
        name=name,
        env=env,
        instances=instances,
        api_url=api_url,
        sql_text=sql_text,
        params_json=params_json,
        category=category,
        modelb_enabled=modelb_enabled,
        modelb_prompt=modelb_prompt
    )
    db.session.add(config)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'id': config.id,
        'message': 'SQL配置创建成功'
    })


@sql_config_bp.route('/api/sql-config/<int:id>', methods=['PUT'])
def api_sql_config_update(id):
    """修改SQL配置"""
    data = request.get_json()
    
    config = SqlTemplate.query.get(id)
    if not config:
        return jsonify({'success': False, 'message': '配置不存在'}), 404
    
    if 'name' in data:
        config.name = data['name']
    if 'env' in data:
        config.env = data['env']
    if 'instances' in data:
        config.instances = data['instances']
    if 'api_url' in data:
        config.api_url = data['api_url']
    if 'sql_template' in data:
        config.sql_text = data['sql_template']
    if 'params_json' in data:
        params_json = data['params_json']
        if isinstance(params_json, list):
            config.params_json = json.dumps(params_json, ensure_ascii=False)
        else:
            config.params_json = params_json
    if 'modelb_enabled' in data:
        config.modelb_enabled = data['modelb_enabled']
    if 'modelb_prompt' in data:
        config.modelb_prompt = data['modelb_prompt']
    if 'category' in data:
        config.category = data['category']
    
    config.updated_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'SQL配置更新成功'
    })


@sql_config_bp.route('/api/sql-config/<int:id>', methods=['DELETE'])
def api_sql_config_delete(id):
    """删除SQL配置"""
    config = SqlTemplate.query.get(id)
    if not config:
        return jsonify({'success': False, 'message': '配置不存在'}), 404
    
    db.session.delete(config)
    db.session.commit()
    
    return jsonify({
        'success': True,
        'message': 'SQL配置删除成功'
    })


@sql_config_bp.route('/api/sql-config/test', methods=['POST'])
def api_sql_config_test():
    """测试执行SQL"""
    data = request.get_json()
    
    template_id = data.get('template_id')
    params = data.get('params', {})
    
    if not template_id:
        return jsonify({'success': False, 'message': '缺少template_id参数'}), 400
    
    config = SqlTemplate.query.get(template_id)
    if not config:
        return jsonify({'success': False, 'message': 'SQL配置不存在'}), 404
    
    # 替换占位符
    final_sql = replace_params(config.sql_text, params)
    
    # 获取第一个实例来执行查询
    instance = config.instances.split(',')[0] if config.instances else config.env.split(' ')[0]
    
    try:
        import time
        start_time = time.time()
        
        # 执行查询
        result_data = execute_sql_query(final_sql, instance, config.env)
        
        # 获取总条数
        total = len(result_data) if isinstance(result_data, list) else 0
        
        # 返回前10条
        preview = result_data[:10] if isinstance(result_data, list) else []
        
        elapsed = time.time() - start_time
        
        return jsonify({
            'success': True,
            'data': preview,
            'total': total,
            'elapsed': round(elapsed, 2)
        })
    
    except Exception as e:
        return jsonify({
            'success': False,
            'message': '执行失败: ' + str(e)
        }), 500


@sql_config_bp.route('/api/sql-config/<int:id>/params', methods=['GET'])
def api_sql_config_get_params(id):
    """获取指定配置的参数定义"""
    config = SqlTemplate.query.get(id)
    if not config:
        return jsonify({'success': False, 'message': '配置不存在'}), 404
    
    # 扫描SQL中的参数
    param_names = scan_params(config.sql_text)
    
    # 解析已有的参数定义
    existing_params = []
    if config.params_json:
        try:
            existing_params = json.loads(config.params_json)
        except:
            pass
    
    # 构建参数列表
    params = []
    for name in param_names:
        # 查找默认值
        default_value = ''
        required = False
        for ep in existing_params:
            if ep.get('name') == name:
                default_value = ep.get('default', '')
                required = ep.get('required', False)
        
        params.append({
            'name': name,
            'default': default_value,
            'required': required
        })
    
    return jsonify({
        'success': True,
        'params': params
    })


# ====== Cookie 配置接口 ======

def get_cookie_from_db():
    """从数据库获取 Cookie，优先数据库，没有则返回默认 Cookie"""
    config = SqlConfig.query.filter_by(key='IDATA_COOKIE').first()
    if config and config.value:
        return config.value
    return DEFAULT_COOKIE


@sql_config_bp.route('/api/config/cookie', methods=['GET'])
def api_get_cookie():
    """获取 Cookie 状态（隐藏中间部分）"""
    cookie = get_cookie_from_db()
    
    if not cookie:
        return jsonify({'cookie': '', 'status': '未配置'})
    
    # 隐藏中间部分，只显示前5位和后5位
    if len(cookie) > 15:
        masked = cookie[:5] + '****' + cookie[-5:]
    else:
        masked = '****'
    
    return jsonify({'cookie': masked, 'status': '已配置'})


@sql_config_bp.route('/api/config/cookie', methods=['POST'])
def api_set_cookie():
    """保存 Cookie 到数据库"""
    data = request.get_json()
    cookie = data.get('cookie', '').strip()

    if not cookie:
        return jsonify({'success': False, 'message': 'Cookie 不能为空'}), 400

    # 查找或创建记录
    config = SqlConfig.query.filter_by(key='IDATA_COOKIE').first()
    if config:
        config.value = cookie
    else:
        config = SqlConfig(key='IDATA_COOKIE', value=cookie)
        db.session.add(config)

    db.session.commit()

    return jsonify({'success': True, 'message': 'Cookie 已保存'})


@sql_config_bp.route('/api/config/cookie-test', methods=['POST'])
def api_test_cookie():
    """测试 Cookie 是否有效"""
    data = request.get_json()
    cookie = data.get('cookie', '').strip()

    # 如果没有提供cookie，使用数据库中保存的
    if not cookie:
        cookie = get_cookie_from_db()

    if not cookie:
        return jsonify({'success': False, 'message': 'Cookie 未配置'}), 400

    # 尝试从 iData 获取数据（使用简单的 COUNT 查询）
    try:
        test_url = "https://idata.cai-inc.com/api/idas/inner/fetchData/getCache"
        test_params = {
            'sql': 'SELECT 1 as cnt LIMIT 1',
            'datasourceType': 'hive',
            'env': 'prod'
        }

        import requests
        headers = {
            'Cookie': cookie,
            'Content-Type': 'application/json'
        }

        response = requests.get(test_url, params=test_params, headers=headers, timeout=10)

        if response.status_code == 200:
            # 检查是否返回有效数据或认证成功
            return jsonify({'success': True, 'message': 'Cookie 有效，连接成功'})
        elif response.status_code == 401:
            return jsonify({'success': False, 'message': 'Cookie 已过期，请重新复制'})
        elif response.status_code == 403:
            return jsonify({'success': False, 'message': '无权限，请检查 Cookie 是否正确'})
        else:
            return jsonify({'success': False, 'message': '连接异常: ' + str(response.status_code)})

    except requests.exceptions.Timeout:
        return jsonify({'success': False, 'message': '连接超时，请检查网络'})
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': '连接失败: ' + str(e)}), 500


@sql_config_bp.route('/api/config/test-connection', methods=['POST'])
def api_test_connection():
    """测试 Cookie 连接"""
    data = request.get_json()
    cookie = data.get('cookie', '').strip()
    
    if not cookie:
        return jsonify({'success': False, 'message': 'Cookie 不能为空'}), 400
    
    # 临时使用提供的 Cookie 测试
    try:
        # 使用云环境的第一个实例测试
        test_env = '云环境'
        api_url = ENV_CONFIG[test_env]['query_api_url']
        test_instance = ENV_CONFIG[test_env]['instances'][0]
        
        headers = {
            'Content-Type': 'application/json',
            'Cookie': cookie
        }
        # 执行一个简单的测试查询
        test_sql = "SELECT 1 as test"
        payload = {
            'sql': test_sql,
            'instance': test_instance,
            'datasourceType': IDATA_DATASOURCE_TYPE
        }
        
        resp = requests.post(api_url, json=payload, headers=headers, timeout=30)
        result = resp.json()
        
        # 检查返回的业务状态
        if isinstance(result, dict) and result.get('success') is False:
            error_msg = result.get('error', result.get('message', '未知错误'))
            return jsonify({'success': False, 'message': f'连接失败: {error_msg}'})
        
        return jsonify({'success': True, 'message': 'Cookie 有效，连接成功'})
    
    except requests.exceptions.RequestException as e:
        return jsonify({'success': False, 'message': f'连接失败: {str(e)}'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'连接失败: {str(e)}'})


# ====== 通用配置接口（统一配置项管理）======

@sql_config_bp.route('/api/config/<key>', methods=['GET'])
@login_required
def api_config_get(key):
    """获取指定配置项

    返回格式：
    {
        "key": "IDATA_COOKIE",
        "value": "****************",
        "masked": true,
        "is_configured": true,
        "updated_at": "2026-05-12 10:30:00"
    }
    """
    config = SqlConfig.query.filter_by(key=key).first()

    if not config:
        return jsonify({
            'key': key,
            'value': '',
            'masked': key in SENSITIVE_KEYS,
            'is_configured': False,
            'updated_at': ''
        })

    raw_value = config.value or ''
    is_configured = bool(raw_value)
    display_value = mask_value(key, raw_value) if is_configured else ''

    return jsonify({
        'key': key,
        'value': display_value,
        'masked': key in SENSITIVE_KEYS and is_configured,
        'is_configured': is_configured,
        'updated_at': config.updated_at.strftime('%Y-%m-%d %H:%M:%S') if config.updated_at else ''
    })


@sql_config_bp.route('/api/config/<key>', methods=['PUT'])
@login_required
def api_config_put(key):
    """保存指定配置项

    接收 JSON：{ "value": "配置内容" }
    逻辑：存在则更新，不存在则新增
    """
    data = request.get_json()
    if data is None:
        value = ''
    else:
        value = data.get('value', '')

    # 支持空字符串（用于清空配置）
    if value is None:
        value = ''

    config = SqlConfig.query.filter_by(key=key).first()
    if config:
        config.value = value
        config.updated_at = datetime.utcnow()
    else:
        config = Config(key=key, value=value)
        db.session.add(config)

    db.session.commit()

    # 重新读取返回最新状态
    raw_value = config.value or ''
    is_configured = bool(raw_value)
    display_value = mask_value(key, raw_value) if is_configured else ''

    return jsonify({
        'success': True,
        'message': '配置已保存',
        'key': key,
        'value': display_value,
        'masked': key in SENSITIVE_KEYS and is_configured,
        'is_configured': is_configured,
        'updated_at': config.updated_at.strftime('%Y-%m-%d %H:%M:%S') if config.updated_at else ''
    })


# ====== 模型B 配置接口 ======

# ====== 模型B配置（已迁移到 model_review.py） ======
# 以下路由由 model_review_bp 统一处理，避免重复路由冲突


# ========== 实例规则关联配置 ==========

@sql_config_bp.route('/api/config/instance-rule-mapping', methods=['GET'])
@login_required
def api_get_instance_rule_mapping():
    """获取实例规则关联配置"""
    config = SqlConfig.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
    
    # 默认映射（兜底，不含 .md 后缀）
    default_mapping = {
        "ZJWC": "浙江网超审核规则",
        "HWCS": "浙江乐采网超审核规则",
        "YNLCY": "其他乐采网超审核规则",
        "GXLCY": "其他乐采网超审核规则",
        "HNLCWC": "其他乐采网超审核规则"
    }
    
    if config and config.value:
        try:
            mapping = json.loads(config.value)
        except:
            mapping = default_mapping
    else:
        mapping = default_mapping
    
    # 归一化：去掉 .md/.txt 后缀保证格式统一
    import re as _re
    normalized = {}
    for inst, rule in mapping.items():
        normalized[inst] = _re.sub(r'\.(md|txt)$', '', rule)
    
    # 实例中文名称（用于前端标签展示）
    instance_names = {
        "ZJWC": "浙江网超",
        "HWCS": "浙江乐采网超",
        "HNLCWC": "湖南乐采网超",
        "YNLCY": "云南乐采云",
        "GXLCY": "广西乐采云"
    }

    return jsonify({'mapping': normalized, 'instance_names': instance_names})


@sql_config_bp.route('/api/config/instance-rule-mapping', methods=['PUT'])
@login_required
def api_update_instance_rule_mapping():
    """更新实例规则关联配置"""
    data = request.get_json()
    if not data or 'mapping' not in data:
        return jsonify({'success': False, 'message': '无效的请求数据'}), 400
    
    mapping = data['mapping']
    
    # 验证 mapping 是有效 JSON 对象
    if not isinstance(mapping, dict):
        return jsonify({'success': False, 'message': 'mapping 必须是对象'}), 400
    
    # 归一化：去掉 .md/.txt 后缀
    import re as _re
    normalized = {}
    for inst, rule in mapping.items():
        normalized[inst] = _re.sub(r'\.(md|txt)$', '', rule)
    
    config = SqlConfig.query.filter_by(key='INSTANCE_RULE_MAPPING').first()
    if config:
        config.value = json.dumps(normalized, ensure_ascii=False)
        config.updated_at = datetime.utcnow()
    else:
        config = SqlConfig(
            key='INSTANCE_RULE_MAPPING',
            value=json.dumps(normalized, ensure_ascii=False)
        )
        db.session.add(config)
    
    db.session.commit()
    
    return jsonify({'success': True, 'message': '关联关系已更新'})


# ========== 用户偏好配置 ==========

@sql_config_bp.route('/api/config/user-preference/<key>', methods=['GET'])
@login_required
def api_get_user_preference(key):
    """获取用户偏好配置

    从 Config 表读取 key='USER_PREF_<username>_<key>' 的值
    支持通过URL参数指定环境：?env=云环境 -> key变为 last_sql_template_云环境
    """
    # 获取当前用户名
    username = current_user.username if current_user else 'anonymous'

    # 获取环境参数（可选）
    env = request.args.get('env', '')

    # 如果key包含last_sql_template且传入了env，将env合并到key中
    if env and key == 'last_sql_template':
        key = f'last_sql_template_{env}'

    # 构建完整的配置键
    full_key = f'USER_PREF_{username}_{key}'

    config = SqlConfig.query.filter_by(key=full_key).first()

    if config and config.value:
        try:
            value = json.loads(config.value)
        except:
            value = config.value
        return jsonify({'value': value})
    else:
        return jsonify({'value': None})


@sql_config_bp.route('/api/config/user-preference/<key>', methods=['POST'])
@login_required
def api_save_user_preference(key):
    """保存用户偏好配置

    存储到 Config 表：key='USER_PREF_<username>_<key>', value=json字符串
    支持通过请求体中的env字段指定环境：{"value": {...}, "env": "云环境"}
    当key为last_sql_template且传入env时，key变为 last_sql_template_云环境
    """
    data = request.get_json()
    if not data or 'value' not in data:
        return jsonify({'success': False, 'message': '无效请求'})

    # 获取当前用户名
    username = current_user.username if current_user else 'anonymous'

    # 获取环境参数（可选）
    env = data.get('env', '')

    # 如果key包含last_sql_template且传入了env，将env合并到key中
    if env and key == 'last_sql_template':
        key = f'last_sql_template_{env}'

    # 构建完整的配置键
    full_key = f'USER_PREF_{username}_{key}'

    # 序列化 value
    value = data.get('value')
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    else:
        value = str(value) if value is not None else ''

    # 查询或创建配置
    config = SqlConfig.query.filter_by(key=full_key).first()
    if config:
        config.value = value
        config.updated_at = datetime.utcnow()
    else:
        config = SqlConfig(key=full_key, value=value)
        db.session.add(config)

    db.session.commit()

    return jsonify({'success': True, 'message': '偏好已保存'})


# ========== 取数管道配置 API ==========

@sql_config_bp.route('/api/fetch-pipeline', methods=['GET'])
@login_required
def api_fetch_pipeline_list():
    """获取取数管道配置（按环境分组）

    返回格式：
    {
        "pipelines": {
            "云环境": [
                { "id": 1, "step_name": "COUNT总数", "sort_order": 1, "enabled": true,
                  "sql_template_id": 10, "category": "count", "sql_name": "取数-COUNT总数统计" },
                ...
            ],
            "乐采云环境": [...]
        }
    }
    """
    from models import FetchPipeline

    pipelines = FetchPipeline.query.order_by(
        FetchPipeline.env, FetchPipeline.sort_order
    ).all()

    result = {}
    for p in pipelines:
        env_label = p.env
        if env_label not in result:
            result[env_label] = []
        tpl = p.sql_template
        result[env_label].append({
            'id': p.id,
            'step_name': p.step_name,
            'sort_order': p.sort_order,
            'enabled': p.enabled,
            'sql_template_id': p.sql_template_id,
            'category': tpl.category if tpl else None,
            'sql_name': tpl.name if tpl else None,
        })

    return jsonify({'pipelines': result})


@sql_config_bp.route('/api/fetch-pipeline', methods=['POST'])
@login_required
def api_fetch_pipeline_create():
    """新增管道步骤

    请求体：{ "env": "云环境", "sql_template_id": 10, "step_name": "自定义步骤", "sort_order": 6 }
    """
    from models import FetchPipeline

    data = request.get_json() or {}
    env = data.get('env', '')
    sql_template_id = data.get('sql_template_id')
    step_name = data.get('step_name', '')
    sort_order = data.get('sort_order')

    if not env or not sql_template_id:
        return jsonify({'success': False, 'message': '环境 和 SQL模板 不能为空'}), 400

    # 自动设置为最大 sort_order + 1（如果未指定）
    if sort_order is None:
        max_order = db.session.query(db.func.max(FetchPipeline.sort_order))\
            .filter_by(env=env).scalar() or 0
        sort_order = max_order + 1

    pipe = FetchPipeline(
        env=env,
        sql_template_id=int(sql_template_id),
        step_name=step_name,
        sort_order=int(sort_order),
        enabled=True
    )
    db.session.add(pipe)
    db.session.commit()

    return jsonify({'success': True, 'id': pipe.id, 'message': '管道步骤已创建'})


@sql_config_bp.route('/api/fetch-pipeline/<int:pipeline_id>', methods=['PUT'])
@login_required
def api_fetch_pipeline_update(pipeline_id):
    """更新单个管道步骤（顺序/启用状态/名称）

    请求体：{ "sort_order": 1, "enabled": true, "step_name": "COUNT总数" }
    """
    from models import FetchPipeline

    data = request.get_json() or {}
    pipeline = FetchPipeline.query.get(pipeline_id)
    if not pipeline:
        return jsonify({'success': False, 'message': '管道步骤不存在'}), 404

    if 'sort_order' in data:
        pipeline.sort_order = int(data['sort_order'])
    if 'enabled' in data:
        pipeline.enabled = bool(data['enabled'])
    if 'step_name' in data:
        pipeline.step_name = data['step_name']

    pipeline.updated_at = datetime.utcnow()
    db.session.commit()

    return jsonify({'success': True, 'message': '管道步骤已更新'})


@sql_config_bp.route('/api/fetch-pipeline/batch', methods=['PUT'])
@login_required
def api_fetch_pipeline_batch_update():
    """批量更新管道配置

    请求体：{
        "updates": [
            { "id": 1, "sort_order": 1, "enabled": true },
            { "id": 2, "sort_order": 2, "enabled": false }
        ]
    }
    """
    from models import FetchPipeline

    data = request.get_json() or {}
    updates = data.get('updates', [])
    if not updates:
        return jsonify({'success': False, 'message': 'updates 列表不能为空'}), 400

    updated_count = 0
    for item in updates:
        pipeline_id = item.get('id')
        if not pipeline_id:
            continue
        pipeline = FetchPipeline.query.get(pipeline_id)
        if not pipeline:
            continue
        if 'sort_order' in item:
            pipeline.sort_order = int(item['sort_order'])
        if 'enabled' in item:
            pipeline.enabled = bool(item['enabled'])
        if 'step_name' in item:
            pipeline.step_name = item['step_name']
        pipeline.updated_at = datetime.utcnow()
        updated_count += 1

    db.session.commit()

    return jsonify({'success': True, 'message': f'已更新 {updated_count} 条管道配置'})
