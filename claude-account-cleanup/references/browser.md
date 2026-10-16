# 浏览器定点清理

先确认实际浏览器资料夹、旧账号范围，保存目标页面草稿。新账号、Anthropic Console 或共用身份无法分离时保护对应入口。不要直接改 Cookie/SQLite/LevelDB，不清全部浏览数据，不改代理、VPN、DNS、启动策略或公司扩展。

## 自动选择可用方式

1. 有原资料夹的现成浏览器/桌面控制工具：由 Codex 进入该浏览器的网站数据管理，逐个删除确认的 Claude 站点。Chrome 为 `chrome://settings/content/all`，Edge 为 `edge://settings/content/all`，其他浏览器按当前界面。先核对目标域名及资料夹，再操作删除，不按模糊搜索结果批量删。
2. 有原浏览器现成且授权的本地 CDP 连接：使用下面的配套工具执行和核验。仅使用已确认的连接，不扫描端口，不重启浏览器开调试端口，不复制或新建用户资料夹冒充清理。
3. 两者都没有：优先发现当前 Codex 可用的浏览器/桌面控制能力；若需要用户授权，集中请求一次连接原浏览器。未获得控制前保留该入口、继续其他已授权部分。不要把未完成说成清理成功，也不要要求同事修改浏览器数据库。

## CDP 工具（Chrome / Edge / Chromium）

需要现有 Node.js 22+，不需要 npm 包。macOS 和 Windows 共用同一工具；缺少 Node 时使用上面的界面方式，不自动安装软件。

Agent 核对浏览器进程、原资料夹和 CDP context 对应关系。`anchor_target_id` 是同一资料夹中现有的中性页面，必要时通过控制工具开空白标签页，不访问 Claude。`browser_context_id` 来自目标元数据；持久默认 context 也可能返回非空 ID，照实际元数据填写。工具不根据资料夹字符串猜测真实连接。

在源资料夹外的用户私有工作目录写入 `browser-spec.json`（不含令牌）：

```json
{
  "schema": 1,
  "endpoint": "ws://127.0.0.1:12345/devtools/browser/actual-browser-id",
  "profile_path": "/verified/browser/Profile 1",
  "profile_confirmed": true,
  "browser_context_id": "",
  "anchor_target_id": "ACTUAL_TARGET_ID",
  "account_state": "old-only",
  "drafts_saved": true,
  "shared_login_excluded": true,
  "close_target_tabs": true,
  "origins": ["https://claude.ai"],
  "protected_origins": ["https://console.anthropic.com", "https://accounts.google.com"]
}
```

以上是 Agent 生成文件的格式，不交给员工填。按实际存储补充明确的 Claude 子域，不使用通配符。此工具仅允许 `claude.ai` 及其子域；Google/Apple/企业 SSO、Anthropic 父域 Cookie 均保留。其他认证域需要另行核对界面，不能擅自加入删除。

```text
node scripts/browser_cleanup.mjs plan --spec browser-spec.json --out /private/browser-plan.json
node scripts/browser_cleanup.mjs apply --plan /private/browser-plan.json --out /private/browser-receipt.json --execute
```

`--out` 使用绝对路径。未加 `--execute` 只核对计划。工具只连接明确的回环 CDP 地址，不读取浏览器数据库、打开网站或发送模型请求。

工具会检查计划后 Cookie 和已选存储有无变化，临时禁用选中旧标签页的脚本，清除 sessionStorage、Cookie、localStorage、IndexedDB、CacheStorage 和 Service Worker 等存储并核验；然后关闭已保存草稿的这些标签页，二次清理持久存储，防止关页写回。失败时恢复仍打开页面的脚本执行。其他 Cookie 及明确列出的保护站点另行前后核对。带分区键的 Cookie 按原分区精确删除；无法定位的 opaque 分区 Cookie 交给界面方式。

`cleared-selected-origins` 表示选中范围已处理：关闭前独立读取核对网站存储，关闭后再次清理并核验 Cookie、配额及保护站点。关闭后协议通常无法独立读取 localStorage / IndexedDB / CacheStorage，回执明确这一限制；需要最终存储界面证据时由 Agent 使用原浏览器站点管理界面核对。不能代替整个浏览器验收：历史关闭标签页的恢复记录、全局 HTTP 缓存和未列出的 origin 不在覆盖范围。不要清全局缓存牺牲其他网站。`partial` 时检查剩余范围，不重放原计划。关闭标签页与清数据是有副作用的操作，不能用于只读排查。

界面方式也应核对原资料夹的目标站点清空、其他网站仍保留，并明确实际覆盖。无法取得界面或协议证据时报告未完成。

协议依据：[Chrome Storage](https://chromedevtools.github.io/devtools-protocol/tot/Storage/)、[Network.deleteCookies](https://chromedevtools.github.io/devtools-protocol/tot/Network/#method-deleteCookies)、[DOMStorage](https://chromedevtools.github.io/devtools-protocol/tot/DOMStorage/)。
