# A-Memorix Phase 4A 正式同步沿革

本记录说明 Phase 4A 的 A-Memorix 内核改动来源和正式同步状态。A-Memorix 的唯一权威远程为用户仓库，不向 `A-Dawn/A_memorix` 创建、恢复或推送 PR。

- 权威仓库：`https://github.com/YANGFENG0001/A_memorix.git`
- 权威分支：`MaiBot_branch`
- Phase 4A 初始基准：`e54bf256d`
- 分区隔离内核提交：`7e1ee0335d6032bb92066454465fd197e95909ca`
- MaiBot 内嵌运行时基线对齐提交：`4e22f14e84468ccc9b254e42974ba2e0e7da9c5d`
- 权威远程政策提交：`405c97bb5`
- LPMM 显式配置修复提交：`b00584b3c`
- MaiBot 最新正式 subtree split：`b00584b3c`
- MaiBot 临时定向移植提交：`6f9baf9e1f7f704d2281553fe0cc6a27f91c1083`

## 正式同步规则

`src/A_memorix` 必须从 `YANGFENG0001/A_memorix:MaiBot_branch` 通过 `git subtree` 同步。临时定向移植只作为历史实施记录；正式 subtree 基线建立后，后续更新只能从该权威分支拉取，并通过 tree hash 和 `git-subtree-split` 追踪来源。

## 阶段状态

截至 2026-09-01，Phase 4A 已完成正式闭环：

1. `YANGFENG0001/A_memorix:MaiBot_branch` 包含分区隔离内核和完整 MaiBot 内嵌运行时；
2. MaiBot 已建立正式 subtree 来源记录，并从权威分支拉取到 `b00584b3c`；
3. `src/A_memorix` tree hash 与权威分支 tree hash 完全一致；
4. A-Memorix 全量回归为 `722 passed, 3 skipped`；
5. Workspace/MemoryScope/BotRequestContext 相关回归为 `18 passed`；
6. Ruff、compileall、`git diff --check` 均通过。

Phase 4B 已有实现可以恢复验收，但仍不得在验收完成前合并 `main`、发布镜像或部署服务器。
