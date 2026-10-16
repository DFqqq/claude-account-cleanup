# 精确操作与验证

运行需要 Python 3.10+，无第三方依赖。Windows 使用本机有效的 Python，macOS 优先已有 python3；不要静默安装依赖。下面命令及 JSON 由 Agent 按实际路径生成，员工无需填写。

## 文件计划

`spec.json` 示例中的路径均为示意；实际写入时必须替换为已核实的本机绝对路径。

```json
{
  "schema": 1,
  "account_state": "old-only",
  "config_dirs": ["/verified/config"],
  "state_files": ["/verified/.claude.json"],
  "preserve_paths": ["/verified/company-network-settings.json"],
  "operations": [
    {"kind": "state-json", "path": "/verified/.claude.json", "remove_fields": ["oauthAccount", "userID"], "old_account_evidence": "confirmed"},
    {"kind": "credential-json", "path": "/verified/config/.credentials.json", "remove_fields": ["claudeAiOauth"], "old_account_evidence": "confirmed"}
  ]
}
```

`old-only` 和 `confirmed` 是调用方核实后的声明，脚本不能从字符串判断真实用户授权或账号是否被封。若发现新号、归属不明、仍有写入者，不生成相应变更。

- `config_dirs` 包括目标旧号实际配置目录；其内部非目标文件全部记录并比较。`state_files` 允许 `.claude.json` 及 `.claude.json.backup...`，不自动发现或删除备份。
- `preserve_paths` 可加入相关应用配置、MCP 或公司网络配置。只读取本次相关、已授权文件；不扫描整个磁盘。保留路径不会变成操作目标。
- 可删除字段仅限经本机版本核实的 `oauthAccount/userID/machineID/deviceId`，凭据仅 `claudeAiOauth`。完整旧号清理时按 [标识策略](identifiers.md) 在操作中加入 `identifier_reset`，由脚本补齐已确认的旧随机标识；未知构建由 Agent 核查代码，不让员工决定字段。其他缓存暂不自动处理，不能绕过白名单用临时脚本扩大范围。
- `plan` 为保留验证读取文件计算 SHA-256，但不解析历史正文，不输出内容或令牌。大目录会增加耗时；不因提速跳过用户要求保留的工作数据。
- 快照记录目录链接本身，不跟随其目标。作为根或目标祖先的链接会拒绝，须先确认真实根。

```text
python -B scripts/cleanup_guard.py plan --spec spec.json --out plan.json
python -B scripts/cleanup_guard.py apply --plan plan.json --out dry-run.json
python -B scripts/cleanup_guard.py apply --plan plan.json --out receipt.json --execute
python -B scripts/cleanup_guard.py verify --plan plan.json
```

每次输出选择源目录外的新文件。`apply` 不加 `--execute` 只验证、不修改。已经明确授权清理的用户不必再逐文件确认；执行前用简短进度说明具体范围即可。只读请求不得加 `--execute`。

计划后任一保护文件变化，或目标出现硬链接/重解析点，拒绝过期计划。执行前后会多次快照；清理期间正常退出目标写入程序是必要前提。脚本不是对恶意本地进程的隔离器，也不是多文件事务。

发生 `partial`：不要原样重跑，也不要重建全量配置。按目标文件逐项检查已完成项，保护新的变化，针对仍属于旧号的剩余项重新计划。发生 `blocked`：不得解释成已清理。输出文件不含原始凭据备份，重新登录是认证移除后的恢复路径；项目和会话无需迁移。

回执文件保持预留的私有句柄到写入结束，拒绝已替换或新增硬链接的回执。已执行但回执保存失败时返回 `partial`，附带已获得的操作结果；不能按“没有回执”重跑。Windows 深路径由脚本规范化，不要求员工移动账号目录。旧版计划不能交给新版脚本执行，需核对现状后重新生成。

## macOS Keychain

`keychain_guard.py` 用 Security.framework 在本机内存读取、比较、写回一个明确条目。不通过命令行参数传递秘密，不输出原始凭据，不保存整包备份。

```text
python3 -B scripts/keychain_guard.py inspect --keychain /verified/login.keychain-db --service "Claude Code-credentials" --account actual-account
python3 -B scripts/keychain_guard.py clean --keychain /verified/login.keychain-db --service "Claude Code-credentials" --account actual-account --expected-sha256 observed-fingerprint
python3 -B scripts/keychain_guard.py clean --keychain /verified/login.keychain-db --service "Claude Code-credentials" --account actual-account --expected-sha256 observed-fingerprint --execute
```

service 必须是实际核实的 `Claude Code-credentials` 或当前版本相关后缀；上面的默认 service 仅在本机核实后使用。缺少 Claude OAuth 时不修改。操作只保留原 item 修改 JSON 子项，不变更条目 ACL。写入前再次比较原指纹；写入后比较所有保留子项。

无跨进程原子比较交换能力，必须先使目标 Claude 会话停止写入。检查返回 absent 只表示精确条目不存在；unverified 可能是权限拒绝、格式不支持或写后验证失败，不可视作不存在。

## 浏览器、桌面版及系统效果

浏览器可执行工具与 schema 见 [浏览器处理](browser.md)。文件脚本的 verified 仅证明计划范围内文件结果。Keychain、浏览器、桌面退出分别取证，不互相代替。浏览器、系统凭据控制权限受限时，只保护并报告未完成入口，不把技术操作转成要求同事自行修改 JSON 的步骤。

## 验证依据

- [Claude Code 认证](https://code.claude.com/docs/en/authentication)
- [Claude Code 本地数据](https://code.claude.com/docs/en/claude-directory)
- [Windows ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew)
- [Apple Security Framework](https://developer.apple.com/documentation/security)

本技能不会执行网络测试、改遥测开关或时区。macOS fcopyfile、空/非空 ACL 与扩展属性保留已通过 macOS CI 虚构文件验证；真实 Keychain、权限弹窗与用户实际环境仍需实机验证，模拟凭据接口不能代替系统授权验证。

Windows 文件短暂占用时仅在旧内容及权限未变的情况下短暂重试；不强退占用程序。回执出现 recovery_file 表示原子替换中断后保留了受限权限的候选文件，其中保留工作配置；先核对原目标与候选内容，再决定接续，不直接删除或盲目覆盖后来出现的新账号。
