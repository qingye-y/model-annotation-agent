# 模型B 带图互检 — 实施方案

> **版本**：v1.1 | 日期：2026-05-21 | 编写：小B  
> 状态：**待确认**（排查完成，方案待青也审批）

---

## 版本记录

| 版本 | 日期 | 变更摘要 |
|------|------|---------|
| v1.1 | 2026-05-21 | 按青也：全量图（主图+详情+SKU）、去SPU、不限数量 |
| v1.0 | 2026-05-21 | 排查现状 + 初始方案（主图≤3、详情≤2、含SPU） |

---

## 一、排查结果

### 1.1 build_prompt 函数 → 确认：不含图片

**位置**：`blueprints/model_review.py` 行47-74

```python
def build_prompt(product_data, prompt_template, ai_result):
    ai_reject_reason = product_data.get('ai_reject_reason', '')
    focus_point = extract_error_reason(ai_reject_reason) or ai_reject_reason[:20]

    if ai_result == '违规':
        prompt = f"""请审核以下商品的主要违规点。
商品名称：{product_data.get('product_name', '')}
类目：{product_data.get('category', '')}
AI审核结果：{ai_result}
主要违规点：{focus_point}

请返回严格的JSON格式：..."""
    else:
        prompt = f"""请审核以下商品是否合规。
商品名称：{product_data.get('product_name', '')}
类目：{product_data.get('category', '')}
AI原审核结果：{ai_result}

请返回严格的JSON格式：..."""
    return prompt
```

**结论**：只传了 `product_name` / `category` / `ai_result` / `focus_point`，**无任何图片信息**。

### 1.2 call_modelb 函数 → 确认：messages 纯文本

**位置**：`blueprints/model_review.py` 行77-109

```python
messages = [
    {"role": "system", "content": "你是一个商品审核专家..."},
    {"role": "user", "content": prompt}    # ← 纯文本，无 image 数组
]
payload = {
    "model": api_config.get('model_name', 'gpt-4'),
    "messages": messages,
    "temperature": 0.1
}
```

**结论**：API 请求只有 text content，没有 `image_url` 或 `image_base64`。

### 1.3 product_data 组装 → 只带了 main_image

**位置**：`blueprints/model_review.py` 行416-425

```python
product_data = {
    'product_name': record.product_name or '',
    'category': record.category or '',
    'shop_name': record.shop_name or '',
    'main_image': record.main_image or '',      # ← 只有主图
    'ai_result': record.ai_result or '合规',
    'ai_reject_reason': record.ai_reject_reason or '',
    'ai_explain': record.ai_explain or '',
    'instance_code': record.instance_code or ''
}
```

**结论**：`main_image` 已在 product_data 中但未被 prompt 使用。`detail_image` / `sku_image` / `spu_image` **未传入**。

### 1.4 图片字段格式（RawData 实测）

| 字段 | 格式 | 示例 | 数量 |
|------|------|------|------|
| `main_image` | 逗号分隔 URL | `url1,url2` | 1-N 张 |
| `detail_image` | 逗号分隔 URL | `url1,url2,url3,...` | 0-N 张 |
| `sku_image` | URL 或空 | 空 | 0-1 张 |
| `spu_image` | URL | `url1` | 1 张 |

> **都是 HTTP URL**，不存在 Base64。URL 域名：`itemcdn.zcycdn.com` / `sitecdn.zcycdn.com`。

---

## 二、设计方案

### 2.1 改造思路

模型B 用视觉模型时，OpenAI 兼容 API 的消息格式支持图文混合：

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "审核提示词..."},
    {"type": "image_url", "image_url": {"url": "https://..."}}
  ]
}
```

改造点：
1. `product_data` 补全 `detail_image` / `spu_image` 字段
2. `build_prompt` 返回的提示词改为图片数量提示（图片本身通过 API 的 image_url 传）
3. `call_modelb` 的 messages 从纯文本改为 `content: [text_obj, image_objs...]`

### 2.2 图片数量控制

| 图片类型 | 策略 |
|---------|------|
| 主图 (main_image) | **全量传入** |
| 详情图 (detail_image) | **全量传入** |
| SKU 图 (sku_image) | **全量传入** |
| SPU 图 (spu_image) | **不传** |

> 单次请求图片 = 所有主图 + 所有详情图 + 所有 SKU 图。⚠️ 如果某条记录图片过多（如详情图 20+ 张），可能超出模型上下文限制，需要在实际测试后确认是否需要上限。

### 2.3 改造代码（伪码）

#### 2.3.1 product_data 补全字段

```python
# run_modelb_review() 中，行416-425 改为：
product_data = {
    'product_name': record.product_name or '',
    'category': record.category or '',
    'shop_name': record.shop_name or '',
    'main_image': record.main_image or '',          # 已存在
    'detail_image': record.detail_image or '',      # 新增
    'sku_image': record.sku_image or '',            # 新增
    'ai_result': record.ai_result or '合规',
    'ai_reject_reason': record.ai_reject_reason or '',
    'ai_explain': record.ai_explain or '',
    'instance_code': record.instance_code or ''
}
```

#### 2.3.2 build_prompt 增加图片数量提示

```python
def build_prompt(product_data, prompt_template, ai_result):
    # ... 现有逻辑不变 ...
    # 末尾追加图片信息
    main_count = len([u for u in product_data.get('main_image','').split(',') if u.strip()]) if product_data.get('main_image') else 0
    detail_count = len([u for u in product_data.get('detail_image','').split(',') if u.strip()]) if product_data.get('detail_image') else 0
    sku_count = len([u for u in product_data.get('sku_image','').split(',') if u.strip()]) if product_data.get('sku_image') else 0
    img_count = main_count + detail_count + sku_count
    prompt += f"\n\n请结合以上{img_count}张商品图片（主图{main_count}张 + 详情图{detail_count}张 + SKU图{sku_count}张）进行判断。"
    return prompt
```

#### 2.3.3 call_modelb 构建图文混合 messages

```python
def call_modelb(product_data, prompt_template, api_config):
    prompt = build_prompt(product_data, prompt_template, ai_result)
    
    # 收集所有图片 URL（主图 + 详情图 + SKU图，spu图不传）
    images = []
    for field in ['main_image', 'detail_image', 'sku_image']:
        val = product_data.get(field, '')
        if not val:
            continue
        for u in val.split(','):
            u = u.strip()
            if u and u.startswith('http'):
                images.append(u)
    
    # 构建 content 数组
    content = [{"type": "text", "text": prompt}]
    for img_url in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": img_url}
        })
    
    messages = [
        {"role": "system", "content": "你是一个商品审核专家，负责判断商品是否合规。请结合所有图片仔细检查。只需返回JSON，不要输出其他内容。"},
        {"role": "user", "content": content}   # ← 图文混合
    ]
    
    payload = {
        "model": api_config.get('model_name', 'gpt-4'),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 500  # 输出限制
    }
    # ... 其余不变 ...
```

> ⚠️ 注意：`model_name` 必须换为支持视觉的模型（如 `gpt-4-vision-preview` / `gpt-4o` / `MiniMax-M2.7-vision` 等），普通文本模型不支持 image 格式。

### 2.4 并发控制

| 模式 | 当前并发 | 建议 |
|------|---------|------|
| Mock（无真实 API） | 不限 | 保持不变 |
| 真实 API（文本） | 1（逐条） | 保持不变 |
| 真实 API（带图） | 1（逐条） | **保持 1，不改** |

> 图片传输耗时会增加（每张 50KB-500KB，模型需下载 → 推理），估计单条约 3-8 秒。并发=1 已经足够保守。

---

## 三、风险与前置条件

| 风险 | 级别 | 缓解 |
|------|------|------|
| 模型B 不支持 vision 模式 | 🔴 高 | **先测试**：修改一条记录的 build_prompt + call_modelb，发送单次带图请求看返回格式 |
| 图片 URL 模型B 无法访问 | 🟡 中 | zcycdn 是公共 CDN，大概率可访问；若不可，需要在请求时用 `detail: "auto"` 或提前下载转 Base64 |
| 图片过多超出上下文 | 🟡 中 | 全量传图，极端情况（详情图 20+ 张）可能超限制；实测后再决定是否加硬上限 |
| 请求超时 | 🟡 中 | max_tokens=500 + timeout=120s（比现状 60s 翻倍） |
| 兼容旧逻辑 | 🟢 低 | `call_modelb` 失败有 Mock 降级，不影响现有数据流 |

---

## 四、验收标准

### 4.1 功能验收

| # | 验证项 | 预期 |
|---|--------|------|
| AC-1 | 模型B 配置了视觉模型名称 | sql_config → 模型B 配置 → model_name 为 vision 模型 |
| AC-2 | 带图互检不报错 | 触发互检 → 日志无 image 格式错误 |
| AC-3 | 互检返回结果含 modelb_result | 可取值 "合规" 或 "违规" |
| AC-4 | Mock 模式仍正常 | 未配置真实 API 时，互检走 Mock 不传图 |
| AC-5 | 图片全部传入（主图+详情+SKU） | 单次请求包含所有可用图片 URL，spu图不传 |

### 4.2 回归验收

| # | 验证项 | 预期 |
|---|--------|------|
| REG-1 | 纯文本互检（旧逻辑）仍可用 | 若模型名称不是 vision 模型，回退到旧的纯文本 messages |
| REG-2 | 中止/进度查询正常 | abort + status API 不受影响 |
| REG-3 | 导出功能不变 | 导出 CSV 中 modelb 相关字段正常 |

---

## 五、实施步骤

| 步骤 | 操作 |
|------|------|
| 1 | **模型名称配置**：在 sql_config → 模型B 配置中填入 vision 模型名 |
| 2 | 修改 `build_prompt`：增加图片数量提示文本 |
| 3 | 修改 `call_modelb`：构建图文混合 messages + max_tokens |
| 4 | 修改 `run_modelb_review` 的 product_data：补 detail_image / spu_image |
| 5 | **单条测试**：手工构造一次带图请求，验证模型返回格式 |
| 6 | 全量互检测试一批次（10条数据），观察耗时和成功率 |
| 7 | 如成功 → 推广到正式互检流程 |
