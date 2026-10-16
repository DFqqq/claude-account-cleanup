# 旧标识重置

“退出登录”和“完整清理旧号、重置旧标识”不同。用户明确要求后者且配置仅供已停用旧号使用时，把已核实的随机客户端标识纳入精确清理；不要因为它不是 OAuth 凭据就自动保留。仅要求退出登录时不额外重置标识。

## 已核实构建

依据另一台测试机的静态代码核查；以下是构建文件摘要，不是用户身份。使用时核对实际二进制 SHA-256，不只比较版本号。

| 版本 / SHA-256 | 可重置字段 | 定位依据 |
| --- | --- | --- |
| 2.1.178 / `8e82516a82511cb321f3aabe696e5fa6a318c43c2a74d657b867d70adf5e699b` | `userID` | `wQ()`，字节偏移 230880541 |
| 2.1.223 / `a708ba811c4cc46907df358e22f2aa6da3dbc28192747e4d3c4a0869752fe722` | `userID`、`machineID` | `Gae()`、`IZr()`，字节偏移 264505653、264505847 |

这些构建用随机字节生成并持久化标识。`userID` 用于请求元数据中的 `device_id` 等；2.1.223 的 `machineID` 用于初始化及错误报告。它们不是 Windows MachineGuid、网卡地址或硬件序列号。不要修改 `remoteControlMachineId`、系统标识或其他未核实字段。

未知构建：由 Codex 只读检查实际可执行文件中的生成、保存和使用位置，记录版本、二进制摘要及代码位置；只把已证明是配置内随机客户端标识的字段加入证据。无需员工提供字段名。不能查实的字段保留，独立的凭据清理继续。

## 生成计划

在相应 `state-json` 操作加入 `identifier_reset`，由脚本自动补齐当前实际存在的字段。例如：

```json
{
  "kind": "state-json",
  "path": "/verified/.claude.json",
  "remove_fields": ["oauthAccount"],
  "old_account_evidence": "confirmed",
  "identifier_reset": {
    "reset_requested": true,
    "config_scope": "old-only",
    "version": "2.1.223",
    "binary_sha256": "a708ba811c4cc46907df358e22f2aa6da3dbc28192747e4d3c4a0869752fe722"
  }
}
```

如果 `oauthAccount` 已经不存在，则使用空的 `remove_fields`，让标识选择器补齐；没有任何待处理字段时不生成该操作。旧备份逐个确认归属后使用同一依据，不按文件名清空全部备份。

对于另外核实的构建，证据还需包含 `basis: "local-code-reviewed"`、`verified_random_id_fields` 和 `code_locations`。这些是 Agent 的核查记录，不是脚本自行证明的账号归属。已登录新号、同一配置服务新旧号或归属不明时，不重置该配置。

正常退出使用该配置的旧客户端，再处理主配置与匹配备份，避免进程缓存写回。核验选中字段已移除、项目/会话/MCP/其余 JSON 字段和权限保留。后续正常启动由客户端生成新值，不自动登录新号；“已移除”和“已生成”分开报告。
