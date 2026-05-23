/**
 * common.js — 全局共享工具函数
 * 在所有 HTML 模板之前加载（<script src="/static/common.js">）
 */

/**
 * 安全 fetch：自动处理登录重定向和 JSON 解析错误
 * - 401 / 非JSON响应 → 跳转登录页
 * - 403 → toast 权限不足
 * - 网络错误 / JSON解析错误 → 抛出异常（由调用方 catch）
 * @param {string} url
 * @param {RequestInit} options
 * @returns {Promise<any>} parsed JSON
 */
function safeFetch(url, options) {
  options = options || {};
  var headers = Object.assign(
    { 'Accept': 'application/json' },
    options.headers || {}
  );
  return fetch(url, Object.assign({ credentials: 'same-origin' }, options, { headers: headers }))
    .then(function(r) {
      var ct = (r.headers.get('content-type') || '').toLowerCase();
      var isJson = ct.indexOf('application/json') !== -1 || ct.indexOf('text/json') !== -1;

      if (r.status === 401) {
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
        return Promise.reject('LOGIN_REQUIRED');
      }
      if (r.status === 403) {
        if (window.showToast) window.showToast('权限不足', true);
        else alert('权限不足');
        return Promise.reject('FORBIDDEN');
      }
      if (r.status === 204) return null;

      if (!r.ok) {
        return r.text().then(function(text) {
          var errMsg = '请求失败 (' + r.status + ')';
          try { var j = JSON.parse(text); errMsg = j.error || j.message || errMsg; } catch (_) {}
          return Promise.reject(errMsg);
        });
      }

      // 防止 HTML 登录页被当 JSON 解析
      if (!isJson) {
        return r.text().then(function(text) {
          try { return JSON.parse(text); }
          catch {
            // 仍是 HTML → 未登录
            window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
            return Promise.reject('LOGIN_REQUIRED');
          }
        });
      }
      return r.json();
    });
}

/**
 * GET 包装
 * @param {string} url
 * @returns {Promise<any>}
 */
function apiGet(url) {
  return safeFetch(url, { method: 'GET' });
}

/**
 * POST 包装（JSON body）
 * @param {string} url
 * @param {object} data
 * @returns {Promise<any>}
 */
function apiPost(url, data) {
  return safeFetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
}

/**
 * PUT 包装（JSON body）
 * @param {string} url
 * @param {object} data
 * @returns {Promise<any>}
 */
function apiPut(url, data) {
  return safeFetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
}

/**
 * DELETE 包装
 * @param {string} url
 * @returns {Promise<any>}
 */
function apiDelete(url) {
  return safeFetch(url, { method: 'DELETE' });
}
