# 子系统 · Bot 身份 · 记忆权限与 Kami 安全域

本文面向部署与运维管理员，说明 Workspace（子系统）体系从「聊天归属」扩展到「Bot 身份 + 记忆空间 + 分区 + 权限组 + Kami 安全域 + 可审计转移」后的管理方式、接口变化与安全边界。

- 当前 schema 版本：`47`（见 `src/common/database/migrations/builtin.py` 的 `LATEST_SCHEMA_VERSION`）
- 管理入口：WebUI `/workspaces`
- 相关矩阵测试：`docs/phase_5b_test_matrix.md`、`pytests/workspace_scope_test_support.py`

---

## 1. 概念总览

| 概念 | 作用 | 关键标识 |
| --- | --- | --- |
| 子系统 Workspace | 聊天主归属单位，绑定默认 BotProfile 与默认记忆空间 | `workspace_id` |
| BotProfile | 运行期 Bot 身份，决定工具/插件/记忆策略 | `bot-profile-public` / `bot-profile-group-*` / `bot-profile-kami` |
| 记忆空间 MemorySpace | 记忆的逻辑隔离单元 | `space_type` = `private` / `public` |
| 记忆分区 MemoryPartition | 空间内的细分隔离单元 | `partition_type` = `shared` / `person` / `conversation` |
| 安全域 security_domain | 普通域与 Kami 域的硬隔离标签 | `normal` / `kami` |
| 权限组 MemoryPermissionGroup | 按用户与场景扩展或收窄记忆范围 | `group_id` |
| AccessResolver | 运行期唯一的范围裁决者 | `MemoryAccessDecision` |
| 记忆转移 Transfer | 可审计的 link / copy / promote 工作流 | `transfer job` |

**核心原则**：所有范围判定只有一个来源——运行期 `AccessResolver.resolve()`。WebUI、权限模拟器与 API 都不得复制权限算法，只允许调用它并展示结果。

---

## 2. 数据模型与迁移

数据库版本按以下顺序推进，每个迁移都可独立回滚到上一个版本：

| 迁移 | 内容 |
| --- | --- |
| `v42_to_v43` | public / group / kami BotProfile、Profile 工具与插件策略、会话级 Bot 路由状态 |
| `v43_to_v44` | 逻辑记忆分区（shared / person / conversation）与旧对象回填 |
| `v44_to_v45` | 用户记忆权限组、成员、上下文、能力、记忆规则、权限组 Bot 规则 |
| `v45_to_v46` | Kami 安全域：Kami 会话状态、Bot 控制审计、记忆访问审计 |
| `v46_to_v47` | 可审计、可恢复的记忆转移工作流 |

分区 ID 由 `build_partition_id(space, type, key, domain)` 生成，形如 `memory-partition-<sha256[:32]>`，即分区 ID 本身已包含空间、类型、键与安全域信息，不可跨空间复用。

---

## 3. Bot 身份与请求级路由

- BotProfile 分为三类：`public`（公共）、`group`（分组，继承 public）、`kami`（完全独立，不继承任何普通 Profile）。
- 每个请求在进入业务逻辑前解析出 `BotRequestContext`，其中携带当次生效的 BotProfile、权限组、记忆范围与 trace id。该上下文是**不可变**的，同一请求内不得被改写。
- 默认子系统绑定唯一公共 BotProfile；非默认子系统自动建立继承公共 Bot 的分组 Profile。
- 普通会话按会话级路由状态选择 Bot；Kami 会话走强制 Kami 路由（见第 6 节）。

---

## 4. 记忆空间与分区

- 默认范围（`memory_scope_mode = "inherit"`）为：本空间 `shared` + 本人 `person` + 当前 `conversation`。
- `memory_scope_mode = "override"` 时，范围**完全由规则决定**，默认范围被替换而不是叠加。这是刻意设计：避免出现「默认范围 + 扩展范围」的危险并集。
- 跨空间读取需要**双向握手**：出向 `BotProfileMemoryRule.can_read` 与入向 `MemorySpaceBotRule.can_read` 必须同时成立；`strict_isolation` 的归属空间同样需要握手。任一方向撤销后立即失效。
- 知识库支持按记忆空间与分区筛选。分区筛选只允许在**已授权可读分区内收窄**：后端收到 `partition_ids` 后会校验其是否落在本次解析出的可读分区集合中，越界直接返回权限错误，不会静默忽略。

---

## 5. 权限组与 AccessResolver

### 5.1 命中规则

权限组只有在存在**已启用的上下文行**（`MemoryPermissionGroupContext`）时才会被命中。只有成员、没有上下文的权限组**永远不会生效**——这是最常见的「配了没反应」原因。

同一人命中多个上下文时，按作用域特异度与会话优先级选择。

### 5.2 能力白名单

只有以下能力会被运行期采纳（`src/workspaces/access_resolver.py` 的 `NORMAL_CAPABILITIES`）：

```text
memory.read.other_person          memory.read.other_conversation
memory.read.cross_space           memory.read.force_all
bot.switch.public                 bot.switch.group
bot.switch.kami                   kami.manage_permissions
kami.use_in_group
memory.transfer.import            memory.transfer.publish
memory.transfer.read_source       memory.transfer.write_target
memory.transfer.link              memory.transfer.copy
memory.transfer.approve           memory.transfer.auto_safe
memory.transfer.auto_approve_safe memory.transfer.retry
memory.transfer.cancel            memory.transfer.cross_space_write
memory.transfer.cross_domain_copy memory.transfer.cross_domain
memory.transfer.kami_link         memory.transfer.kami_copy
```

白名单之外的能力会被忽略，不会因为写进数据库就生效。

### 5.3 管理者预设

勾选「管理者权限模式」时只授予两项能力：

```text
bot.switch.kami + memory.read.force_all
```

预设**不会**自动授予 `kami.manage_permissions`，避免一键造出可自我扩权的账号。

### 5.4 冲突与并发

- 同一层级、同一优先级下同时出现 `allow` 与 `deny` 会被拒绝保存（结果不确定即拒绝，而不是取其一）。
- 所有子表写入都会在同一事务内递增父级 `policy_revision`。
- 写接口接受 `expected_revision`；与服务端不一致时返回 **409**，前端必须提示「配置已被其他管理员更新」并提供刷新入口，**不得静默覆盖**。

---

## 6. Kami 安全域

Kami 是管理员记忆模式，用于临时以全量视角排查记忆问题。

- 固定标识：`bot-profile-kami` 与 `memory-space-kami`。
- 生效条件（全部满足）：处于管理者模式、具备 `bot.switch.kami`、具备 `memory.read.force_all`；若在群聊中触发还需 `kami.use_in_group`。
- 能力边界：可强制读取**全部普通记忆**，但**不会**因此获得系统、文件或令牌权限；写入只落在 Kami 安全域（`security_domain = "kami"`）内。普通 Bot 即使在 `force_all` 下也无法读取 Kami 分区。
- 会话命令（`src/workspaces/kami_service.py`）：

  | 命令 | 作用 |
  | --- | --- |
  | `/kami` | 私聊直接激活；群聊先触发 30 秒内存确认挑战（挑战不落库） |
  | `/kami confirm` | 群聊中确认激活 |
  | `/kami off` | 关闭 Kami 状态 |
  | `/kami status` | 查看当前状态 |

  命令在普通消息注册之前被拦截，**不会进入 AI 历史**，对模型完全不可见。
- TTL：默认 `900` 秒，取值被 clamp 到 `60..86400` 秒；危险操作确认窗口 `30` 秒。
- 失效条件：TTL 到期、进程重启（boot id 变化）、权限被撤销、执行 `/kami off`。
- 审计：`bot_control_audit` 记录枚举化命令与原因，`memory_access_audit` 只记录不可逆摘要与范围元数据。

---

## 7. 记忆转移

- 三种模式：`link`（建立引用）、`copy`（复制）、`promote`（提升）。
- 计划以**不可变快照**保存，审批必须匹配计划哈希与策略快照，否则拒绝执行。
- 支持冲突策略、幂等键、逐项结果、来源链与内容指纹。
- 执行时会做权限复核；支持租约、可重试/不可重试错误分类、崩溃恢复与执行前循环检测。
- 跨安全域（normal ↔ kami）的写入会被降级禁止。

---

## 8. WebUI 管理控制台

### 8.1 路径

- 管理控制台：`/workspaces`
- 记忆控制台（含空间/分区筛选）：`/resource/knowledge-base`
- 运行中心（NapCat / 适配器状态）：`/operations`

### 8.2 七个分区

`/workspaces` 分为以下七个页签（i18n 键位于 `workspaceAdmin.tabs`）：

| 页签 | 中文 | 职责 |
| --- | --- | --- |
| `chatGroups` | 聊天分组 | 子系统建立、公共/独立记忆空间选择、群聊与私聊分配 |
| `botProfiles` | Bot 配置 | BotProfile 策略、工具与插件允许/拒绝、记忆规则 |
| `memorySpaces` | 记忆库 | 记忆空间与分区、双向 ACL |
| `permissionGroups` | 权限组 | 权限组 CRUD、成员、上下文、能力、记忆规则、Bot 规则 |
| `kami` | Kami 管理 | Kami Profile 开关、活动会话列表与强制撤销 |
| `transfers` | 同步与导入 | 记忆转移任务的创建、审批与执行 |
| `audit` | 审计与模拟 | 记忆访问审计、Bot 控制审计、权限模拟器 |

### 8.3 交互约定

- 聊天流一律显示实际名称（群名称或「xxx 的私聊」），不显示 `session_id`。
- 下拉弹层挂载到顶层并使用不透明背景，支持键盘访问。
- 审计页面**默认遮蔽敏感字段**：普通管理员看不到查询哈希与 metadata 原文，需要显式开启才展示。
- 权限模拟器直接调用运行期真实 `AccessResolver`，返回命中原因、读写范围、`security_domain`、`access_mode` 与 `trace_id`。

---

## 9. API 变化

所有接口都要求认证（`require_auth`），写接口额外叠加限流。

| 前缀 | 说明 |
| --- | --- |
| `/api/webui/bot-profiles` | BotProfile 列表/详情/更新，工具与插件策略，记忆规则 |
| `/api/webui/memory-permission-groups` | 权限组 CRUD、成员、上下文、能力、规则、Bot 规则、模拟器 |
| `/api/webui/memory-transfers` | 转移任务的创建、审批、执行与查询 |
| `/api/webui/kami` | Kami Profile（`/profile`）与活动会话（`/sessions`、`/sessions/{state_id}/revoke`） |
| `/api/webui/memory-access-audit` | 记忆访问审计查询 |
| `/api/webui/bot-control-audit` | Bot 控制审计查询 |
| `/api/webui/memory` | 新增 `partition_ids` 查询参数（空间/分区筛选） |

`/api/webui/memory` 系列的 `memory_space_id` 与 `partition_ids` 由前端控制台统一拼接，`partition_ids` 为逗号分隔的分区 ID 列表。

---

## 10. 安全边界

1. 范围裁决唯一化：任何模块都不得自行计算可读范围，必须消费 `AccessResolver` 的结果。
2. 默认拒绝：未命中的范围一律不可读、不可写；不提供「读不到就放行」的兜底。
3. 无危险并集：`override` 替换默认范围，不做叠加。
4. 双向握手：跨空间读取必须双向许可，任一方向撤销即失效。
5. 安全域硬隔离：`normal` 与 `kami` 互不可见，写入不跨域。
6. 群聊受众安全：即使范围包含他人记忆，群聊受众还必须额外满足上下文的 `allow_group_disclosure`，否则不得在群内披露。
7. 审计最小化：审计只保留不可逆摘要与范围元数据，绝不写入查询正文、记忆正文或令牌。
8. 并发保护：子表写入递增父级 `policy_revision`，冲突返回 409，前端必须显式刷新。
9. Kami 不可自我扩权：管理者预设不含 `kami.manage_permissions`。

---

## 11. 升级与回滚

### 11.1 部署布局

按仓库自带编排，core 服务的关键映射如下（`docker-compose.yml`）：

| 项 | 值 |
| --- | --- |
| 容器名 | `maim-bot-core` |
| 镜像（prod） | `sengokucola/maibot:latest` |
| 镜像（GHCR 覆盖文件） | `ghcr.io/yangfeng0001/maibot:latest` |
| 数据目录 | 容器 `/MaiMBot/data` ← 宿主机 `./data/MaiMBot` |
| 配置目录 | 容器 `/MaiMBot/config` ← 宿主机 `./docker-config/mmc` |
| 插件数据 | 容器 `/MaiMBot/data/plugins` ← 宿主机 `./data/MaiMBot-plugin-data` |
| WebUI 端口 | `18001:8001` |

`docker-compose.ghcr.yml` 只覆盖 `core` 的 `image`，必须与主编排文件叠加使用。

### 11.2 升级

升级会在启动时按 `v42 → v47` 顺序自动执行迁移。升级前必须完成完整备份，并记录当前 core 镜像 digest。

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.ghcr.yml"

# 1) 备份宿主机数据与配置目录（不要进容器里备份，宿主目录才是持久化位置）
tar -czf backup_$(date +%Y%m%d%H%M%S).tar.gz ./data/MaiMBot ./data/MaiMBot-plugin-data ./docker-config

# 2) 记录当前镜像 digest（回滚时需要固定到该 digest）
docker image inspect ghcr.io/yangfeng0001/maibot:latest --format '{{index .RepoDigests 0}}'

# 3) 拉取新镜像并重建 core
$COMPOSE pull core
$COMPOSE up -d core

# 4) 观察启动日志中的迁移版本
docker logs maim-bot-core --tail 100 | grep -E "数据库版本|写入版本"
```

迁移成功后访问 `http://<宿主机>:18001/workspaces` 确认管理控制台可用。

### 11.3 回滚

**回滚必须先把失败后的数据保存到独立目录，再恢复部署前快照**，不得直接在原地覆盖：

```bash
COMPOSE="docker compose -f docker-compose.yml -f docker-compose.ghcr.yml"
STAMP=$(date +%Y%m%d%H%M%S)

# 1) 保存失败后的现场数据（便于事后分析）
mkdir -p rollback_$STAMP
tar -czf rollback_$STAMP/data_after_failure.tar.gz ./data/MaiMBot ./docker-config

# 2) 停止 core 并恢复部署前快照
$COMPOSE down core
# 按部署前记录的备份文件恢复，不要盲目复制示例路径

# 3) 把 core 镜像固定为升级前记录的 PREVIOUS_IMAGE_DIGEST 后启动
$COMPOSE up -d core
```

注意：新版 schema（47）与旧版代码不兼容。若要回滚到旧镜像，必须先恢复旧版 schema 快照，再启动旧容器。

注意：新版 schema（47）与旧版代码不兼容。若要回滚到旧镜像，必须先恢复旧版 schema 快照，再启动旧容器。

---

## 12. A-Memorix 同步说明

- A-Memorix 的唯一权威远程为用户仓库，不向 `A-Dawn/A_memorix` 创建、恢复或推送 PR。
- 检索内核的空间与分区过滤先在权威仓库形成正式提交并 push，再从该分支执行正式 subtree 同步；禁止以 MaiBot 本地临时补丁替代正式链路。
- 详细约定见 `docs/a_memorix_sync.md` 与 `docs/a_memorix_phase4_lineage.md`。
- 涉及 `src/A_memorix` 的改动请先阅读 `src/A_memorix/MODIFICATION_POLICY.md`。

---

## 13. 常用运维命令

```bash
# 后端静态检查
uv run --no-sync ruff check src/ scripts/ pytests/

# 后端测试（必须用 -m pytest，直接 pytest pytests 会因 src 不在路径而失败）
uv run --no-sync python -m pytest pytests -q

# 阶段矩阵校验（期望输出 127/127 exact collected nodes）
uv run --no-sync python scripts/verify_phase6_matrix.py

# Dashboard 四项检查
cd dashboard
npm run typecheck
npm run lint
npm run test:run
npm run build
```

---

## 14. 常见问题

**配了权限组但不生效？**
检查该权限组是否存在**已启用的上下文行**。只有成员、没有上下文的权限组不会被命中。

**设置了记忆范围但看到的内容变少了？**
`memory_scope_mode` 为 `override` 时范围由规则完全决定，默认范围被替换。若希望保留默认范围，请使用 `inherit` 并只添加扩展规则。

**跨空间读不到对端记忆？**
检查双向握手：出向 `BotProfileMemoryRule.can_read` 与入向 `MemorySpaceBotRule.can_read` 必须同时为真。

**保存时返回 409？**
说明配置已被其他管理员更新。刷新页面后重试，不要绕过 `expected_revision`。

**Kami 会话突然失效？**
TTL 到期、进程重启、权限撤销或执行 `/kami off` 都会终止状态。可在「Kami 管理」页签查看活动会话与状态。
