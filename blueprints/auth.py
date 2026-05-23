from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import check_password_hash, generate_password_hash
from models import db, User
from datetime import datetime, timedelta
import jwt
from config import SECRET_KEY

auth_bp = Blueprint('auth', __name__, url_prefix='')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username or not password:
            return render_template('login.html', error='用户名和密码不能为空')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password) and user.is_active:
            login_user(user)
            # 跳转到主页
            next_page = request.args.get('next')
            if next_page:
                return redirect(next_page)
            return redirect(url_for('auth.index'))
        else:
            return render_template('login.html', error='账号或密码错误')

    return render_template('login.html', error=None)


@auth_bp.route('/logout')
@login_required
def logout():
    """退出登录"""
    logout_user()
    return redirect(url_for('auth.login'))


# ========== API 接口 ==========

@auth_bp.route('/api/auth/login', methods=['POST'])
def api_login():
    """API 登录接口（返回 JSON）"""
    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')

    if not username or not password:
        return jsonify({'success': False, 'message': '用户名和密码不能为空'}), 400

    user = User.query.filter_by(username=username).first()

    if not user or not check_password_hash(user.password_hash, password):
        return jsonify({'success': False, 'message': '用户名或密码错误'}), 401

    if not user.is_active:
        return jsonify({'success': False, 'message': '账号已被停用'}), 403

    login_user(user)

    # 生成 token
    payload = {
        'user_id': user.id,
        'username': user.username,
        'role': user.role,
        'exp': datetime.utcnow() + timedelta(days=7)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm='HS256')

    return jsonify({
        'success': True,
        'message': '登录成功',
        'data': {
            'token': token,
            'user': {
                'id': user.id,
                'username': user.username,
                'name': user.name,
                'role': user.role,
                'daily_quota': user.daily_quota
            }
        }
    })


@auth_bp.route('/api/auth/logout', methods=['POST'])
@login_required
def api_logout():
    """API 登出接口"""
    logout_user()
    return jsonify({'success': True, 'message': '登出成功'})


@auth_bp.route('/api/auth/me', methods=['GET'])
@login_required
def api_current_user():
    """获取当前用户信息"""
    return jsonify({
        'success': True,
        'data': {
            'id': current_user.id,
            'username': current_user.username,
            'name': current_user.name,
            'role': current_user.role,
            'daily_quota': current_user.daily_quota
        }
    })


# ========== 用户管理 API ==========

@auth_bp.route('/api/auth/users', methods=['GET'])
@login_required
def list_users():
    """获取用户列表"""
    users = User.query.filter_by(is_active=True).all()
    return jsonify({
        'success': True,
        'data': [{
            'id': u.id,
            'username': u.username,
            'name': u.name,
            'role': u.role,
            'daily_quota': u.daily_quota
        } for u in users]
    })


@auth_bp.route('/api/auth/users', methods=['POST'])
@login_required
def add_user():
    """添加用户"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': '权限不足'}), 403

    data = request.get_json() or {}
    username = data.get('username', '').strip()
    password = data.get('password', '')
    name = data.get('name', '').strip()
    role = data.get('role', 'annotator')
    daily_quota = data.get('daily_quota', 200)

    if not username or not password or not name:
        return jsonify({'success': False, 'message': '参数不完整'}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': '用户名已存在'}), 400

    user = User(
        username=username,
        password_hash=generate_password_hash(password),
        name=name,
        role=role,
        daily_quota=daily_quota,
        is_active=True
    )
    db.session.add(user)
    db.session.commit()

    return jsonify({'success': True, 'message': '用户添加成功'})


@auth_bp.route('/api/auth/users/<int:user_id>', methods=['PUT'])
@login_required
def update_user(user_id):
    """更新用户信息"""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    if current_user.role != 'admin' and current_user.id != user_id:
        return jsonify({'success': False, 'message': '权限不足'}), 403

    data = request.get_json() or {}

    if 'name' in data and current_user.role == 'admin':
        user.name = data['name']
    if 'daily_quota' in data and current_user.role == 'admin':
        user.daily_quota = data['daily_quota']
    if 'role' in data and current_user.role == 'admin':
        user.role = data['role']

    db.session.commit()
    return jsonify({'success': True, 'message': '用户更新成功'})


@auth_bp.route('/api/auth/users/<int:user_id>', methods=['DELETE'])
@login_required
def delete_user(user_id):
    """删除用户（停用）"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': '权限不足'}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    user.is_active = False
    db.session.commit()

    return jsonify({'success': True, 'message': '用户已停用'})


@auth_bp.route('/api/auth/users/<int:user_id>/reset-password', methods=['POST'])
@login_required
def reset_password(user_id):
    """重置密码"""
    if current_user.role != 'admin':
        return jsonify({'success': False, 'message': '权限不足'}), 403

    user = User.query.get(user_id)
    if not user:
        return jsonify({'success': False, 'message': '用户不存在'}), 404

    data = request.get_json() or {}
    new_password = data.get('password', '')

    if not new_password:
        return jsonify({'success': False, 'message': '新密码不能为空'}), 400

    user.password_hash = generate_password_hash(new_password)
    db.session.commit()

    return jsonify({'success': True, 'message': '密码重置成功'})


# ========== 主页面路由 ==========

@auth_bp.route('/')
@login_required
def index():
    """主页面"""
    return render_template('index.html')