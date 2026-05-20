# -*- coding: utf-8 -*-
"""
提示词规则管理蓝图 - 基于文件系统
用于管理 prompt_rules/ 目录下的提示词规则文件
支持 Markdown 文件格式
"""
import os
from datetime import datetime
from flask import Blueprint, request, jsonify, send_from_directory
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename, safe_join

prompt_rules_bp = Blueprint('prompt_rules', __name__, url_prefix='/api/prompt-rules')

# 提示词规则存储目录
PROMPT_RULES_DIR = 'prompt_rules'

# 默认扩展名（从 .txt 升级为 .md）
DEFAULT_EXTENSION = '.md'

# 允许的文件扩展名
ALLOWED_EXTENSIONS = {'.txt', '.md'}


def get_rules_directory():
    """获取规则目录的绝对路径"""
    import app as flask_app
    base_dir = os.path.dirname(os.path.abspath(flask_app.__file__))
    rules_dir = os.path.join(base_dir, PROMPT_RULES_DIR)
    if not os.path.exists(rules_dir):
        os.makedirs(rules_dir)
    return rules_dir


def is_allowed_file(filename):
    """检查文件扩展名是否允许"""
    ext = os.path.splitext(filename)[1].lower()
    return ext in ALLOWED_EXTENSIONS


def get_file_info(filepath, show_ext=True):
    """获取文件信息"""
    stat = os.stat(filepath)
    mtime = datetime.fromtimestamp(stat.st_mtime)
    basename = os.path.basename(filepath)

    if show_ext:
        name = basename
    else:
        name = os.path.splitext(basename)[0]

    return {
        'name': name,
        'file': basename,
        'size': stat.st_size,
        'modified': mtime.strftime('%Y-%m-%d %H:%M:%S')
    }


def migrate_txt_to_md():
    """将现有的 .txt 文件迁移为 .md 文件"""
    rules_dir = get_rules_directory()
    migrated = []

    for filename in os.listdir(rules_dir):
        if filename.endswith('.txt'):
            txt_path = os.path.join(rules_dir, filename)
            md_filename = filename[:-4] + '.md'
            md_path = os.path.join(rules_dir, md_filename)

            # 如果 .md 文件不存在，则迁移
            if os.path.isfile(txt_path) and not os.path.exists(md_path):
                try:
                    # 读取 .txt 内容
                    with open(txt_path, 'r', encoding='utf-8') as f:
                        content = f.read()

                    # 写入 .md 文件
                    with open(md_path, 'w', encoding='utf-8') as f:
                        f.write(content)

                    # 删除旧的 .txt 文件
                    os.remove(txt_path)

                    migrated.append(md_filename)
                    print(f'[PromptRules] 已迁移: {filename} -> {md_filename}')
                except Exception as e:
                    print(f'[PromptRules] 迁移失败: {filename}, {e}')

    return migrated


def find_rule_file_by_name(rule_name):
    """根据规则名称查找文件

    优先级：
    1. <规则名>.md
    2. <规则名>.txt（兼容旧数据）
    3. <规则名>（无扩展名，尝试 .md 和 .txt）
    """
    rules_dir = get_rules_directory()

    # 清理文件名
    clean_name = secure_filename(rule_name)
    if clean_name.endswith('.md'):
        clean_name = clean_name[:-3]
    elif clean_name.endswith('.txt'):
        clean_name = clean_name[:-4]

    # 尝试查找
    search_names = [
        clean_name + '.md',
        clean_name + '.txt',
        clean_name
    ]

    for search_name in search_names:
        filepath = os.path.join(rules_dir, search_name)
        if os.path.isfile(filepath):
            return filepath

    return None


def read_rule_content(rule_name):
    """读取规则内容

    根据规则名称读取对应的 .md 或 .txt 文件内容
    """
    filepath = find_rule_file_by_name(rule_name)

    if not filepath:
        return None

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print(f'[PromptRules] 读取规则失败: {rule_name}, {e}')
        return None


# ========== API 接口 ==========

@prompt_rules_bp.route('', methods=['GET'])
@login_required
def list_rules():
    """获取规则列表"""
    rules_dir = get_rules_directory()
    rules = []

    for filename in os.listdir(rules_dir):
        filepath = os.path.join(rules_dir, filename)
        if os.path.isfile(filepath) and is_allowed_file(filename):
            rules.append(get_file_info(filepath, show_ext=True))

    # 按修改时间倒序排列
    rules.sort(key=lambda x: x['modified'], reverse=True)

    return jsonify(rules)


@prompt_rules_bp.route('/', methods=['GET'])
@login_required
def get_rule_content(filename):
    """获取规则内容

    支持 .md 和 .txt 文件
    如果传入不带扩展名的名称，优先查找 .md
    """
    # 防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400

    rules_dir = get_rules_directory()
    safe_filename = secure_filename(filename)

    # 确定文件路径
    filepath = None

    if safe_filename:
        # 如果文件名已包含扩展名
        if '.' in safe_filename:
            candidate = os.path.join(rules_dir, safe_filename)
            if os.path.isfile(candidate):
                filepath = candidate
        else:
            # 尝试 .md（优先）
            md_candidate = os.path.join(rules_dir, safe_filename + '.md')
            if os.path.isfile(md_candidate):
                filepath = md_candidate
            else:
                # 尝试 .txt（兼容）
                txt_candidate = os.path.join(rules_dir, safe_filename + '.txt')
                if os.path.isfile(txt_candidate):
                    filepath = txt_candidate

    if not filepath or not os.path.exists(filepath):
        return jsonify({'error': '文件不存在'}), 404

    if not os.path.isfile(filepath):
        return jsonify({'error': '无效的文件'}), 400

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return jsonify({'error': f'读取文件失败: {str(e)}'}), 500

    basename = os.path.basename(filepath)
    name_without_ext = os.path.splitext(basename)[0]

    return jsonify({
        'name': name_without_ext,
        'file': basename,
        'content': content
    })


@prompt_rules_bp.route('', methods=['POST'])
@login_required
def create_rule():
    """创建新规则

    默认创建 .md 文件
    """
    data = request.get_json()
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400

    name = data.get('name', '').strip()
    if not name:
        return jsonify({'error': '规则名称不能为空'}), 400

    content = data.get('content', '')

    # 安全处理文件名
    safe_name = secure_filename(name)

    # 添加 .md 扩展名（如果不存在）
    if not os.path.splitext(safe_name)[1]:
        safe_filename = safe_name + DEFAULT_EXTENSION
    else:
        safe_filename = safe_name

    rules_dir = get_rules_directory()
    filepath = os.path.join(rules_dir, safe_filename)

    # 检查文件是否已存在
    if os.path.exists(filepath):
        return jsonify({'error': '规则已存在'}), 409

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return jsonify({'error': f'创建文件失败: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'file': safe_filename,
        'name': os.path.splitext(safe_filename)[0]
    })


@prompt_rules_bp.route('/', methods=['PUT'])
@login_required
def update_rule(filename):
    """更新规则内容"""
    # 防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400

    content = data.get('content', '')
    safe_filename = secure_filename(filename)

    if not safe_filename:
        return jsonify({'error': '无效的文件名'}), 400

    # 添加扩展名（如果不存在，优先使用 .md）
    if '.' not in safe_filename:
        safe_filename += DEFAULT_EXTENSION

    rules_dir = get_rules_directory()
    filepath = os.path.join(rules_dir, safe_filename)

    if not os.path.exists(filepath):
        # 尝试查找对应的文件
        alt_filepath = find_rule_file_by_name(safe_filename)
        if alt_filepath:
            filepath = alt_filepath
        else:
            return jsonify({'error': '文件不存在'}), 404

    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    except Exception as e:
        return jsonify({'error': f'更新文件失败: {str(e)}'}), 500

    return jsonify({'success': True})


@prompt_rules_bp.route('/', methods=['DELETE'])
@login_required
def delete_rule(filename):
    """删除规则"""
    # 防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400

    safe_filename = secure_filename(filename)

    if not safe_filename:
        return jsonify({'error': '无效的文件名'}), 400

    # 添加扩展名（如果不存在，优先使用 .md）
    if '.' not in safe_filename:
        safe_filename += DEFAULT_EXTENSION

    rules_dir = get_rules_directory()
    filepath = os.path.join(rules_dir, safe_filename)

    if not os.path.exists(filepath):
        # 尝试查找对应的文件
        alt_filepath = find_rule_file_by_name(safe_filename)
        if alt_filepath:
            filepath = alt_filepath
        else:
            return jsonify({'error': '文件不存在'}), 404

    try:
        os.remove(filepath)
    except Exception as e:
        return jsonify({'error': f'删除文件失败: {str(e)}'}), 500

    return jsonify({'success': True})


@prompt_rules_bp.route('//rename', methods=['PUT'])
@login_required
def rename_rule(filename):
    """重命名规则"""
    # 防止路径穿越
    if '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'error': '无效的文件名'}), 400

    data = request.get_json()
    if not data:
        return jsonify({'error': '无效的请求数据'}), 400

    new_name = data.get('new_name', '').strip()
    if not new_name:
        return jsonify({'error': '新名称不能为空'}), 400

    # 安全处理文件名
    safe_old_filename = secure_filename(filename)
    safe_new_name = secure_filename(new_name)

    # 处理扩展名
    if not os.path.splitext(safe_old_filename)[1]:
        old_ext = DEFAULT_EXTENSION
    else:
        old_ext = os.path.splitext(safe_old_filename)[1]

    if not os.path.splitext(safe_new_filename)[1]:
        safe_new_filename = safe_new_name + old_ext
    else:
        safe_new_filename = safe_new_name

    rules_dir = get_rules_directory()
    old_filepath = find_rule_file_by_name(safe_old_filename)

    if not old_filepath:
        return jsonify({'error': '原��件不存在'}), 404

    new_filepath = os.path.join(rules_dir, safe_new_filename)

    if os.path.exists(new_filepath):
        return jsonify({'error': '新文件名已存在'}), 409

    try:
        os.rename(old_filepath, new_filepath)
    except Exception as e:
        return jsonify({'error': f'重命名失败: {str(e)}'}), 500

    return jsonify({
        'success': True,
        'file': safe_new_filename,
        'name': os.path.splitext(safe_new_filename)[0]
    })


# ========== 初始化默认规则（Markdown格式） ==========
def init_default_rules():
    """初始化默认规则文件（Markdown格式）"""
    rules_dir = get_rules_directory()

    # 先执行迁移
    migrate_txt_to_md()

    # 默认规则内容（Markdown格式）
    default_rules = {
        '浙江网超审核规则.md': '''# 浙江网超审核规则

## 合规标准

1. **商品信息完整**：商品名称、描述、图片等信息完整准确
2. **图片合规**：
   - 主图清晰，无水印、无马赛克
   - 无违规内容（色情、暴力、政治敏感等）
3. **价格合规**：价格标示清晰，无虚假原价
4. **无违规发布**：不属于平台禁售商品

## 常见违规项

- 描述不符
- 缺少必要资质
- 图片质量问题
- 价格违规
''',
        '浙江乐采网超审核规则.md': '''# 浙江乐采网超审核规则

## 合规标准

1. 商品信息完整准确
2. 图片清晰合规
3. 价格标示清晰
4. 符合平台要求

## 常见违规项

- 描述不符
- 图片问题
- 价格违规
''',
        '其他乐采网超审核规则.md': '''# 其他乐采网超审核规则

## 合规标准

1. 商品信息完整
2. 图片合规
3. 符合基本要求

## 审核要点

根据商品类型进行差异化审核
'''
    }

    for rule_file, rule_content in default_rules.items():
        filepath = os.path.join(rules_dir, rule_file)
        if not os.path.exists(filepath):
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(rule_content)
                print(f'已创建默认规则文件: {rule_file}')
            except Exception as e:
                print(f'创建默认规则文件失败: {rule_file}, {e}')