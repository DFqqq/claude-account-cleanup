# Windows 与 macOS 入口适配

这些是定位线索，不是自动清理名单。检查实际启动入口和资料夹后，使用明确绝对路径。仅查已安装的软件；不为检查安装或启动客户端，不调用模型。

## 共用原则

CLI 默认主状态文件可能是用户目录的 `.claude.json`，配置目录通常是 `.claude`。`CLAUDE_CONFIG_DIR` 可能改变实际位置；VS Code 扩展、独立 CLI、自定义启动脚本可能使用不同目录。查询环境只输出配置目录及变量存在性，不打印完整环境、shell 配置或 `.env`。不要把 Codex 自身继承的环境直接当成其他进程环境。

从缓存、启动配置、用户已给事实交叉判断旧账号范围。两个目录可以共用凭据；macOS 还可能有 `CLAUDE_SECURESTORAGE_CONFIG_DIR` 覆盖。新的 Console profile 或第三方网关可能不使用旧式 `.credentials.json`。对不支持的认证存储只报告准确范围，不自行扩展为全局注销。

## Windows

- Claude Code 文件凭据通常位于实际配置目录的 `.credentials.json`；脚本只删除确认的 `claudeAiOauth` 子项。
- VS Code 用户目录常在 `%APPDATA%\Code`，也可能是 Insiders 或便携版。保留整个用户设置、扩展和工作区；不删除 VS Code globalStorage/state.vscdb。仅在当前版本扩展有明确独立退出入口时操作该入口。
- Claude 桌面版可能是传统安装或 MSIX；`%APPDATA%\Claude`、包的 LocalCache 等仅作线索。MSIX 数据可能重定向到其他磁盘。先通过已安装应用和进程对应关系确认真实目录，不递归扫描整个 WpSystem。
- 遇到 Junction、符号链接或其他 reparse point：记录、不跟随。确需处理应用的真实重定向目录，先核实应用归属、旧账号和权限，再把真实绝对路径作为新根；不能用“跟随全部链接”解决。
- 浏览器：Chrome、Edge、其他 Chromium 浏览器的 User Data 可能由 `--user-data-dir` 指定，不能只选 Default。只控制目标资料夹。
- 权限采用 Windows ACL；不得用 `chmod 600` 作为 Windows 隐私保证。文件脚本通过受限 ACL 创建临时/输出文件，以 ReplaceFileW 替换并比较源 DACL。只读、权限拒绝或文件占用均不强行夺权处理。

## macOS

- Claude Code 可能使用 `.claude.json` 和 Keychain；只清 JSON 不等于处理了钥匙串。
- 默认 service 曾使用 `Claude Code-credentials`，独立目录可能带不同后缀。先从当前安装版本的实现/官方说明和目标条目的元数据核实 service、account、keychain 三者，不广泛 dump 钥匙串。
- 必要时用系统凭据工具只查选定 service 的元数据，禁止打印密码内容。`keychain_guard.py inspect` 使用精确命名空间读取到内存，仅返回指纹、子项存在性和数量。`clean` 仅移除 Claude OAuth，保留 MCP 和 item 的访问控制。
- Keychain 权限拒绝不等于不存在。适配器不解锁钥匙串、不新增系统授权、不删除整项、不改变搜索列表。系统弹窗只能由用户在本机授权。
- 文件写入利用 macOS fcopyfile 保留 ACL、扩展属性和基础元数据，写入后校验；缺少能力时不降级为丢失权限的覆盖。
- Claude 桌面数据可能位于 `~/Library/Application Support/Claude`；Chrome 在 `~/Library/Application Support/Google/Chrome`，Edge、Firefox、Safari 有各自资料位置。这些位置不能整删。VS Code 同样不删除其用户目录或共用 secret storage。

## 浏览器定点处理

完整步骤、可执行工具和缺少控制连接时的处理见 [浏览器处理](browser.md)。只在原资料夹操作，由 Agent 保存草稿、核对范围、选择现有 UI/CDP 控制并核验。不要重启浏览器开调试端口、不复制资料夹、不直接修改数据库；Cookie 父域和 Console/SSO 共用登录须保护。

## Claude 桌面版（由 Agent 操作）

先保存工作，通过已运行客户端的明确账号退出入口处理旧账号；不强退。如果应用未运行、退出入口不可用或账号归属不明，不为确认封禁而自动打开账号页面。离线残留只有在当前版本存在明确、可验证的独立认证项时才定点处理；不得删除 Application Support、LocalCache、IndexedDB 或整份应用数据目录。

桌面版中涉及本地项目、会话、Cowork 等工作数据时一律保留。未能独立核验桌面登录时，明确标为未完成；Claude Code 退出不能替代桌面退出。
