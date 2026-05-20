/**
 * 全局配置系统 - 标注标签管理
 * 用于在页面上动态管理标注备注标签
 */
(function() {
  // 实例名称映射（固定5个实例）
  const INSTANCE_NAMES = {
    'ZJWC': '浙江网超',
    'HWCS': '浙江乐采网超',
    'HNLCWC': '湖南乐采网超',
    'YNLCY': '云南乐采云',
    'GXLCY': '广西乐采云'
  };

  // 默认标签列表（来源：提示词V24 + 50,000条违规探查结果，2026-05-15更新）
  const DEFAULT_LABELS = [
    '特殊资质缺失',
    '水印',
    '马赛克',
    '盗图',
    '类目错放',
    '图文不一致',
    '销售属性错误',
    'SKU图不一致',
    '关键属性不一致',
    '站外引流',
    '无关信息',
    '多主体',
    '商品清单',
    '品类词堆砌',
    '禁售商品',
    '标题无关词',
    'AI生成',
    '书籍版权页',
    '其他'
  ];

  // 默认提示词规则
  const DEFAULT_PROMPT_RULES = [
    { id: 'rule_001', name: '通用商品审核规则', description: '适用于一般商品的审核规则' },
    { id: 'rule_002', name: '食品类审核规则', description: '适用于食品类商品的审核规则' },
    { id: 'rule_003', name: '服装类审核规则', description: '适用于服装类商品的审核规则' },
    { id: 'rule_004', name: '美妆类审核规则', description: '适用于美妆类商品的审核规则' },
    { id: 'rule_005', name: '3C数码审核规则', description: '适用于3C数码类商品的审核规则' }
  ];

  // 默认实例列表
  const DEFAULT_INSTANCES = [
    { id: 'instance_001', name: '实例01', description: '生产环境-主站' },
    { id: 'instance_002', name: '实例02', description: '生产环境-国际站' },
    { id: 'instance_003', name: '实例03', description: '预发环境' },
    { id: 'instance_004', name: '实例04', description: '测试环境' },
    { id: 'instance_005', name: '实例05', description: '沙箱环境' }
  ];

  // 规则与实例的绑定关系
  const DEFAULT_RULE_INSTANCE_BINDINGS = [
    { ruleId: 'rule_001', instanceIds: ['instance_001', 'instance_002', 'instance_003'] },
    { ruleId: 'rule_002', instanceIds: ['instance_001', 'instance_002'] },
    { ruleId: 'rule_003', instanceIds: ['instance_003', 'instance_004'] },
    { ruleId: 'rule_004', instanceIds: ['instance_001', 'instance_005'] },
    { ruleId: 'rule_005', instanceIds: ['instance_002', 'instance_003', 'instance_004'] }
  ];

  const STORAGE_KEY = 'app_label_tags';
  const PROMPT_RULES_KEY = 'app_prompt_rules';
  const INSTANCES_KEY = 'app_instances';
  const RULE_INSTANCE_BINDINGS_KEY = 'app_rule_instance_bindings';
  const USERS_KEY = 'app_users';

  // ========== 标注员用户管理 ==========
  const DEFAULT_USERS = [
    { username: 'ann_zhangsan', password: 'pass123', name: '张三', rules: ['rule_001'], dailyQuota: 200, usedQuota: 0, status: 'active', createdAt: '2026-05-01' },
    { username: 'ann_lisi', password: 'pass123', name: '李四', rules: ['rule_002', 'rule_004'], dailyQuota: 200, usedQuota: 0, status: 'active', createdAt: '2026-05-02' },
    { username: 'ann_wangwu', password: 'pass123', name: '王五', rules: ['rule_003'], dailyQuota: 150, usedQuota: 0, status: 'active', createdAt: '2026-05-03' },
    { username: 'ann_zhaoliu', password: 'pass123', name: '赵六', rules: ['rule_005'], dailyQuota: 200, usedQuota: 0, status: 'disabled', createdAt: '2026-05-04' },
    { username: 'ann_sunqi', password: 'pass123', name: '孙七', rules: ['rule_001', 'rule_002'], dailyQuota: 200, usedQuota: 0, status: 'active', createdAt: '2026-05-05' },
    { username: 'ann_zhouba', password: 'pass123', name: '周八', rules: ['rule_004'], dailyQuota: 180, usedQuota: 0, status: 'active', createdAt: '2026-05-06' }
  ];

  // 获取用户列表
  function getUsers() {
    const stored = localStorage.getItem(USERS_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (e) {
        return [...DEFAULT_USERS];
      }
    }
    return [...DEFAULT_USERS];
  }

  // 保存用户列表
  function saveUsers(users) {
    localStorage.setItem(USERS_KEY, JSON.stringify(users));
  }

  // 添加用户
  function addUser(user) {
    const users = getUsers();
    const exists = users.find(u => u.username === user.username);
    if (exists) {
      return { success: false, message: '账号名已存在' };
    }
    users.push({
      username: user.username,
      password: user.password,
      name: user.name,
      rules: user.rules,
      dailyQuota: user.dailyQuota || 200,
      usedQuota: 0,
      status: 'active',
      createdAt: new Date().toISOString().split('T')[0]
    });
    saveUsers(users);
    return { success: true, message: '添加成功' };
  }

  // 更新用户
  function updateUser(username, updates) {
    const users = getUsers();
    const idx = users.findIndex(u => u.username === username);
    if (idx === -1) {
      return { success: false, message: '用户不存在' };
    }
    if (updates.name) users[idx].name = updates.name;
    if (updates.rules) users[idx].rules = updates.rules;
    if (updates.dailyQuota) users[idx].dailyQuota = updates.dailyQuota;
    if (updates.usedQuota !== undefined) users[idx].usedQuota = updates.usedQuota;
    saveUsers(users);
    return { success: true, message: '更新成功' };
  }

  // 删除用户
  function deleteUser(username) {
    const users = getUsers();
    const idx = users.findIndex(u => u.username === username);
    if (idx === -1) {
      return { success: false, message: '用户不存在' };
    }
    users.splice(idx, 1);
    saveUsers(users);
    return { success: true, message: '删除成功' };
  }

  // 切换用户状态
  function toggleUserStatus(username) {
    const users = getUsers();
    const user = users.find(u => u.username === username);
    if (!user) {
      return { success: false, message: '用户不存在' };
    }
    user.status = user.status === 'active' ? 'disabled' : 'active';
    saveUsers(users);
    return { success: true, message: user.status === 'active' ? '账号已启用' : '账号已停用' };
  }

  // 重置密码
  function resetPassword(username, newPassword) {
    const users = getUsers();
    const user = users.find(u => u.username === username);
    if (!user) {
      return { success: false, message: '用户不存在' };
    }
    user.password = newPassword;
    saveUsers(users);
    return { success: true, message: '密码已重置' };
  }

  // 获取用户绑定的规则名称
  function getUserRuleNames(username) {
    const users = getUsers();
    const user = users.find(u => u.username === username);
    if (!user) return [];
    const rules = getPromptRules();
    return user.rules.map(rid => {
      const rule = rules.find(r => r.id === rid);
      return rule ? rule.name : rid;
    });
  }

  // 根据规则获取可用用户
  function getUsersByRule(ruleId) {
    const users = getUsers();
    return users.filter(u => u.status === 'active' && u.rules.includes(ruleId));
  }

  // 获取标签列表
  function getLabels() {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (e) {
        return [...DEFAULT_LABELS];
      }
    }
    return [...DEFAULT_LABELS];
  }

  // 保存标签列表到本地存储
  function saveLabels(labels) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
  }

  // 获取提示词规则列表
  function getPromptRules() {
    const stored = localStorage.getItem(PROMPT_RULES_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (e) {
        return [...DEFAULT_PROMPT_RULES];
      }
    }
    return [...DEFAULT_PROMPT_RULES];
  }

  // 保存提示词规则列表
  function savePromptRules(rules) {
    localStorage.setItem(PROMPT_RULES_KEY, JSON.stringify(rules));
  }

  // 获取实例列表
  function getInstances() {
    const stored = localStorage.getItem(INSTANCES_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (e) {
        return [...DEFAULT_INSTANCES];
      }
    }
    return [...DEFAULT_INSTANCES];
  }

  // 保存实例列表
  function saveInstances(instances) {
    localStorage.setItem(INSTANCES_KEY, JSON.stringify(instances));
  }

  // 获取规则与实例绑定关系
  function getRuleInstanceBindings() {
    const stored = localStorage.getItem(RULE_INSTANCE_BINDINGS_KEY);
    if (stored) {
      try {
        return JSON.parse(stored);
      } catch (e) {
        return [...DEFAULT_RULE_INSTANCE_BINDINGS];
      }
    }
    return [...DEFAULT_RULE_INSTANCE_BINDINGS];
  }

  // 保存规则与实例绑定关系
  function saveRuleInstanceBindings(bindings) {
    localStorage.setItem(RULE_INSTANCE_BINDINGS_KEY, JSON.stringify(bindings));
  }

  // 更新规则绑定的实例
  function updateRuleInstances(ruleId, instanceIds) {
    const bindings = getRuleInstanceBindings();
    const idx = bindings.findIndex(b => b.ruleId === ruleId);
    if (idx !== -1) {
      bindings[idx].instanceIds = instanceIds;
    } else {
      bindings.push({ ruleId, instanceIds });
    }
    saveRuleInstanceBindings(bindings);
    return { success: true };
  }

  // 获取规则绑定的实例
  function getRuleBoundInstances(ruleId) {
    const bindings = getRuleInstanceBindings();
    const binding = bindings.find(b => b.ruleId === ruleId);
    return binding ? binding.instanceIds : [];
  }

  // 添加标签
  function addLabel(name) {
    const labels = getLabels();
    const trimmedName = name.trim();
    if (!trimmedName) {
      return { success: false, message: '标签名称不能为空' };
    }
    if (labels.includes(trimmedName)) {
      return { success: false, message: '标签已存在' };
    }
    labels.push(trimmedName);
    saveLabels(labels);
    return { success: true, message: '添加成功' };
  }

  // 删除标签
  function removeLabel(name) {
    const labels = getLabels();
    const index = labels.indexOf(name);
    if (index === -1) {
      return { success: false, message: '标签不存在' };
    }
    labels.splice(index, 1);
    saveLabels(labels);
    return { success: true, message: '删除成功' };
  }

  // 修改标签
  function updateLabel(oldName, newName) {
    const labels = getLabels();
    const trimmedNewName = newName.trim();
    if (!trimmedNewName) {
      return { success: false, message: '标签名称不能为空' };
    }
    const index = labels.indexOf(oldName);
    if (index === -1) {
      return { success: false, message: '原标签不存在' };
    }
    if (labels.includes(trimmedNewName) && trimmedNewName !== oldName) {
      return { success: false, message: '新标签名称已存在' };
    }
    labels[index] = trimmedNewName;
    saveLabels(labels);
    return { success: true, message: '修改成功' };
  }

  // 重置为默认标签
  function resetLabels() {
    saveLabels([...DEFAULT_LABELS]);
    return { success: true, message: '已重置为默认标签' };
  }

  // 暴露全局对象
  window.APP_CONFIG = {
    // 实例名称映射
    INSTANCE_NAMES: INSTANCE_NAMES,
    getInstanceName: function(code) {
      return INSTANCE_NAMES[code] || code;
    },
    getAllInstanceCodes: function() {
      return Object.keys(INSTANCE_NAMES);
    },
    getLabels: getLabels,
    addLabel: addLabel,
    removeLabel: removeLabel,
    updateLabel: updateLabel,
    resetLabels: resetLabels,
    DEFAULT_LABELS: DEFAULT_LABELS,
    getPromptRules: getPromptRules,
    savePromptRules: savePromptRules,
    getInstances: getInstances,
    saveInstances: saveInstances,
    getRuleInstanceBindings: getRuleInstanceBindings,
    saveRuleInstanceBindings: saveRuleInstanceBindings,
    updateRuleInstances: updateRuleInstances,
    getRuleBoundInstances: getRuleBoundInstances,
    DEFAULT_PROMPT_RULES: DEFAULT_PROMPT_RULES,
    DEFAULT_INSTANCES: DEFAULT_INSTANCES,
    // 标注员用户管理
    getUsers: getUsers,
    addUser: addUser,
    updateUser: updateUser,
    deleteUser: deleteUser,
    toggleUserStatus: toggleUserStatus,
    resetPassword: resetPassword,
    getUserRuleNames: getUserRuleNames,
    getUsersByRule: getUsersByRule
  };
})();
