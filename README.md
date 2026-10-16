# Claude Account Cleanup

A Codex skill for scoped cleanup of retired Claude logins on Windows and macOS, while preserving projects, sessions, MCP authorizations, and network settings.

处理已停用的旧 Claude 登录、浏览器站点数据和经当前版本核实的旧随机客户端标识。由 Codex 定位、执行并核验，保留项目、会话、MCP 授权和网络配置。

**当前清理实现：v1.2.0 · 许可证：MIT**

## 安装

把下面这段发给 Codex：

```text
请从 https://github.com/DFqqq/claude-account-cleanup 安装 claude-account-cleanup Skill 到当前客户端的个人技能目录，不要装到当前项目。本次仅安装，不检查或清理任何账号。
```

也可在 [Releases](https://github.com/DFqqq/claude-account-cleanup/releases/latest) 下载 Skill ZIP，交给 Codex 安装。

安装器需要已有 Python 3.10+，无第三方 Python 依赖。下载并解压后，Codex 可使用 `claude-account-cleanup/scripts/install_skill.py --dest-root 实际个人技能目录`；已授权更新时加 `--upgrade`，保留旧 Skill 副本。仅安装不会访问账号数据。

## 调用

确认电脑上的目标旧号已停用、尚未登录新号，再发给 Codex：

```text
使用 $claude-account-cleanup 完整清理旧 Claude 登录并重置已确认的旧标识，保留项目、会话、MCP 授权和网络配置；本机尚未登录新号。
```

默认保护正常新账号及归属不明的入口。不要为了测试对正常账号运行真实清理。

## 清理与保留范围

| 处理对象 | 行为 |
| --- | --- |
| Claude Code / VS Code | 核对实际配置与共用授权，只处理确认的旧账号状态 |
| 旧随机客户端标识 | 按当前版本与代码依据选择字段，不套用固定删除名单，不改系统硬件标识 |
| 浏览器 | 通过原资料夹的已有界面控制或授权 CDP，定点清理 Claude 站点数据，保留其他网站 |
| macOS 钥匙串 | 精确处理旧 Claude OAuth，保留同一条目中的 MCP 授权 |
| 工作与网络 | 保留项目、未提交改动、会话、历史、Skills、插件、MCP、网关及 VPN、代理、路由、DNS 配置 |

不整删目录或凭据容器，不输出或备份原始令牌，不自动登录新号，不发模型消息。不同入口分别核验，未完成项明确列出。本工具处理本地状态，不判断封禁原因、不承诺免封或清除服务端记录。

## 平台与验证状态

- **Windows：**文件处理、权限保留、安装更新和隔离浏览器测试已验证。
- **macOS：**提供文件和 Keychain 适配；已有模拟测试，原生钥匙串、权限弹窗和完整实际使用流程尚待 Mac 实机验证。
- **浏览器：**需要原资料夹的可用界面控制或既有授权 CDP。协议工具支持 Chrome / Edge / Chromium，需要已有 Node.js 22+；不需要 npm 依赖。Safari 无专用协议脚本。
- **桌面版：**依赖宿主 Agent 可用的界面控制工具，独立于文件清理核验。

## 虚构数据测试

在仓库根目录运行：

```sh
python -B -X utf8 run_validation.py
```

测试只访问自动创建的虚构账号与工作目录，并从虚构用户目录安装后的副本执行。不会安装到真实个人 Skill 目录，也不会清理真实账号。

原 v1.2.0 验证包括：74 项 Python 测试（核心 35、安装更新 12、旧标识 8、扩展异常 19），14 项浏览器协议测试和 11 项真实 Chromium 隔离检查。原始范围见 [验证说明](docs/validation-summary.txt) 与 [验证数据](docs/validation-results.json)；模拟结果不能代替 Mac 实机验证。

有 Node.js 22+ 时默认增加协议测试；如已有 Playwright / Chromium，可指定现有模块并运行隔离浏览器测试：

```sh
python -B -X utf8 run_validation.py --browser
```

可通过 `PLAYWRIGHT_MODULE` 指定已有模块，通过 `--node` 指定 Node 路径。浏览器测试启动全新的无头虚构资料夹，页面由本地拦截提供；不使用现有浏览器登录。

## 本地构建

```sh
python -B -X utf8 build_release.py
```

产物包括 Skill ZIP、隔离测试包和 SHA-256 清单，均位于 `dist/`。安装包中的 MIT 许可证随技能保留。

## 贡献与来源

欢迎提交 Issues / Pull Requests，说明系统版本、客户端版本和经过脱敏的错误信息。请勿上传账号、Cookie、令牌、完整凭据、个人会话或未脱敏的本机路径。

设计参考 [cc-clean](https://github.com/luwill/cc-clean) 的精确范围、共用凭据保护与前后核验思路；本仓库脚本为独立实现。清理字段与客户端机制以实际版本及官方文档为准。

[MIT License](LICENSE) · [技能入口](claude-account-cleanup/SKILL.md) · [浏览器处理](claude-account-cleanup/references/browser.md) · [旧标识依据](claude-account-cleanup/references/identifiers.md)

