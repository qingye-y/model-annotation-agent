# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, '.')
from app import app

with app.app_context():
    with app.test_client() as client:
        response = client.get('/api/data-fetch/download/BAT-20260519161001?type=raw')
        print(f'状态码: {response.status_code}')
        print(f'Content-Type: {response.content_type}')
        print(f'Content-Length: {response.content_length}')
        data = response.data
        print(f'数据长度: {len(data)} bytes')
        if len(data) > 0:
            lines = data.decode('utf-8-sig').split('\n')
            print(f'行数: {len([l for l in lines if l.strip()])}')
            print(f'第一行: {lines[0][:200]}')
