#!/usr/bin/env python3
"""Task #16 验证：dispatch_center.html filter_batch URL 参数支持"""
import sys
import re

def verify():
    path = "templates/dispatch_center.html"
    with open(path, encoding="utf-8") as f:
        content = f.read()

    checks = []
    errors = []

    # 1. fetchAssignHistory 已纳入 Promise.all
    checks.append(("Promise.all 包含 fetchAssignHistory",
                   "fetchAssignHistory()" in content and
                   "Promise.all([\n        fetchTaskPool()," in content))

    # 2. URL 参数提前读取
    checks.append(("init() 提前读取 filter_batch 参数",
                   "var filterBatchNo = urlParams.get('filter_batch')" in content))

    # 3. filter_batch 处理分支存在
    checks.append(("filter_batch 处理分支存在",
                   "if (filterBatchNo) {" in content))

    # 4. 自动展开历史记录区域
    checks.append(("自动展开 historyContent",
                   "historyContent" in content and "classList.add('show')" in content))

    # 5. 自动添加 expanded 箭头样式
    checks.append(("展开箭头添加 expanded 样式",
                   "historyArrow" in content and "classList.add('expanded')" in content))

    # 6. data-batch-no 属性查询（定位目标行）
    checks.append(("通过 data-batch-no 属性定位批次行",
                   'data-batch-no="' in content and "filterBatchNo" in content))

    # 7. 浅黄色高亮样式
    checks.append(("浅黄色高亮样式（#fff8e1）",
                   "#fff8e1" in content))

    # 8. scrollIntoView 滚动定位
    checks.append(("scrollIntoView 滚动到目标行",
                   "scrollIntoView" in content))

    # 9. 2秒后自动移除高亮
    checks.append(("2秒后自动移除高亮（setTimeout 2000）",
                   "setTimeout(function() {\n                    targetRow.style.background = '';" in content or
                   "setTimeout(function(){targetRow.style.background=''" in content))

    # 10. 恢复原始筛选状态
    checks.append(("恢复原始筛选状态（调用 onHistoryFilterChange）",
                   "onHistoryFilterChange();" in content and
                   content.count("onHistoryFilterChange();") >= 2))

    # 11. URL 参数清除（replaceState）
    checks.append(("URL 参数清除（replaceState）",
                   "window.history.replaceState({}, '', cleanUrl);" in content))

    # 12. model_task.html viewGeneratedTasks 跳转 URL 已修正
    path2 = "templates/model_task.html"
    with open(path2, encoding="utf-8") as f2:
        content2 = f2.read()
    checks.append(("viewGeneratedTasks 跳转到 /dispatch_center.html",
                   "/dispatch_center.html?filter_batch=" in content2))
    checks.append(("viewGeneratedTasks 不含错误 URL /dispatch-center",
                   "/dispatch-center?filter_batch=" not in content2))

    print("=" * 56)
    print("Task #16 验证：dispatch_center.html filter_batch 参数支持")
    print("=" * 56)

    all_pass = True
    for name, passed in checks:
        status = "✅" if passed else "❌"
        print(f"  {status} {name}")
        if not passed:
            all_pass = False
            errors.append(name)

    print()
    if all_pass:
        print("🎉 全部通过！Task #16 已完成。")
        return 0
    else:
        print(f"❌ 共 {len(errors)} 项失败：")
        for e in errors:
            print(f"   - {e}")
        return 1

if __name__ == "__main__":
    sys.exit(verify())
