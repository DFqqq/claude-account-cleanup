# 完整清理与续跑

本页供 Codex 执行，不交给员工填写。员工一句“使用该 Skill 完整清理被封旧号”已授权必要的本地定位、旧登录处理及已确认旧标识清理；仅要求退出时不额外重置标识。

## 完成顺序

1. **先发现入口和控制能力**：按平台说明确认浏览器原资料夹、桌面版、独立 CLI、VS Code、实际配置目录与共用凭据。不存在的入口记为不适用。已有新号、混用或归属不明的入口及共用凭据先保护。
2. **由 Codex 生成完整 inventory**，把文件、旧标识、Keychain、浏览器和桌面核验列为独立任务，使用相同的 shared_scope 标记共用认证。相同文件不要重复清理。
3. **执行并处理剩余 Agent 任务**：文件验证完成后，继续钥匙串、浏览器、桌面和旧标识，不提前结束。缺少标识依据由 Codex 核查代码；UI 缺少 CDP 时检查原窗口的系统 UI 能力。系统授权必须本人操作的，集中提示一次，其他明确部分继续。
4. **重新核对并续跑**：已清理字段不存在则记已完成，不重放旧 plan。不自动恢复完整旧配置。系统权限拒绝、界面操作超时、发生部分写入时先核查当前状态。
5. **逐入口收尾**：只在 completion 的适用旧号任务完成、无剩余 Agent 任务及系统动作时宣布完成。新号保护、未安装、没有数据与权限不足分别记录。

## inventory

用本地私有工作目录保存 inventory、各步骤计划及回执，放在所有源资料夹之外。不要写入令牌、完整凭据或会话正文。Agent 自行补齐绝对路径，以下仅为结构例子：

```json
{
  "schema": 1,
  "inventory_complete": true,
  "entries": [
    {"id":"cli-files","kind":"files","scope":"old-only","shared_scope":"default-claude",
     "spec":"/private/file-spec.json","identity_state":"/verified/.claude.json","old_account_uuid":"confirmed-old-uuid"},
    {"id":"cli-identifiers","kind":"identifiers","scope":"old-only","shared_scope":"default-claude",
     "spec":"/private/identifier-spec.json","identity_state":"/verified/.claude.json","old_account_uuid":"confirmed-old-uuid"},
    {"id":"browser-main","kind":"browser","scope":"old-only","shared_scope":"chrome-profile",
     "profile_path":"/verified/browser/Profile 1","spec":"/private/browser-spec.json","node":"/verified/node"},
    {"id":"desktop","kind":"desktop","scope":"not-installed","shared_scope":"desktop"}
  ]
}
```

macOS 的 keychain entry 另给精确的 keychain、service、account，按实际认证版本定位，不猜条目、不全量 dump。同一默认配置对应的 Keychain 与 CLI/VS Code 使用相同 shared_scope。文件 spec 仅移除旧认证；identifier spec 的 remove_fields 为空并包含 identifier_reset，避免未知标识阻断其他清理。

```text
python -B -X utf8 scripts/completion.py --inventory /private/inventory.json --out /private/new-run --execute
```

退出码 2 及 partial 代表仍有工作。读取 agent_tasks 继续执行，不直接转述给员工；只有 user_actions 中确需系统授权或解锁的事项交给本人。没有现成控制工具时，尝试下述原生桥接；仍缺少系统能力，准确说明一次。

## 标识核查

```text
python -B -X utf8 scripts/identifier_probe.py --binary /verified/actual-program --version actual-version
```

该脚本只读显式程序文件，核对摘要，不运行客户端、不读账号。verified-build 可直接交给标识策略；needs-code-review 的偏移只是检索线索，Codex 必须核实随机生成、配置读写和使用位置，才能生成 local-code-reviewed 依据。Windows 构建结论不能当作 Mac 构建依据。

## 原生界面桥接

优先使用已经可控制原窗口的工具。缺少这类工具时，scripts/native_ui.py 提供 Windows UIAutomation 与 macOS System Events 后备，不安装插件、不修改浏览器调试设置、不扫描全部窗口。

由 Codex 确认实际 PID、原窗口及旧号范围，在私有目录生成 native-ui-spec.json：

```json
{"schema":1,"scope":"old-only","profile_confirmed":true,"drafts_saved":true,
 "entry_kind":"desktop","pid":123,"window_handle":456,"action":"inspect"}
```

```text
python -B -X utf8 scripts/native_ui.py --spec /private/native-ui-spec.json
```

Windows 先读取 inspect 返回的 runtime_id 和精确名称，再以 open-menu、logout、confirm-logout 执行相应已确认控件。macOS 使用精确 PID、唯一窗口和唯一已知名称；系统 Accessibility 或 Automation 授权只能由本人给予。不会读取编辑框值、密码、剪贴板或全局 UI，不强退。

浏览器的 remove-site 限 Windows 已确认的 Claude 原站点容器，control_name 必须是明确删除按钮，且 site_container_runtime_id 对应精确 claude.ai 站点行、目标按钮在该行内。不支持全局清缓存、删除账号或重置应用。Mac 浏览器和复杂确认框由现有 UI 工具处理，不能用笼统名称盲点。

action-invoked 仅表示控件调用完成。Codex 必须独立核验原入口已经退出或站点数据已清，以及项目、会话和 MCP 保留。生成 ui_evidence（entry_id、profile_confirmed、old_logout_verified、work_preserved、mcp_preserved），供对应 UI entry 记录结果。发现新号或改变的资料夹立即保护，不重复调用旧退出控件。

确认旧号独占、工作已保存且具有可靠控制时，可为完成已授权退出而正常打开原入口。不得为探测封禁登录、发送消息或主动打开申诉链接；不得通过新建/复制资料夹、修改数据库或绕过客户端调试限制实现“完成”。

## 浏览器暂态重试

completion 通过浏览器 helper 的 --retry-transient 开启最多两次写入前刷新。仅当 Cookie、标签页、sessionStorage、localStorage 摘要和数据库/缓存名称等逻辑状态完全一致，只有 quota 用量统计变化时重建计划。新登录、逻辑数据变化、控制失效和任何部分写入都不能自动重放。

## 保留与验证

保留项目、未提交改动、会话、memory、MCP 授权、网关、公司 VPN/代理/路由/DNS 及现有遥测隐私设置。钥匙串与桌面版的真实系统授权有平台条件；虚构接口通过不等于官方应用真实登录退出已验收。
