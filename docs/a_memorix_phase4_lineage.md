# A-Memorix Phase 4A 正式同步沿革

本记录说明 Phase 4A 的 A-Memorix 内核改动来源和正式同步状态。A-Memorix 的唯一权威远程为用户仓库，不向 `A-Dawn/A_memorix` 创建、恢复或推送 PR。

- 权威仓库：`https://github.com/YANGFENG0001/A_memorix.git`
- 权威分支：`MaiBot_branch`
- Phase 4A 初始基准：`e54bf256d`
- 分区隔离内核提交：`7e1ee0335d6032bb92066454465fd197e95909ca`
- MaiBot 内嵌运行时基线对齐提交：`4e22f14e84468ccc9b254e42974ba2e0e7da9c5d`
- 权威远程政策提交：`405c97bb5`
- MaiBot 临时定向移植提交：`6f9baf9e1f7f704d2281553fe0cc6a27f91c1083`

## 正式同步规则

`src/A_memorix` 必须从 `YANGFENG0001/A_memorix:MaiBot_branch` 通过 `git subtree` 同步。临时定向移植只作为历史实施记录；正式 subtree 基线建立后，后续更新只能从该权威分支拉取，并通过 tree hash 和 `git-subtree-split` 追踪来源。

## 阶段状态

Phase 4A 只有在以下条件全部成立后才可标记完成：

1. 权威分支包含内核提交和与 MaiBot 内嵌运行时一致的完整树；
2. MaiBot 已建立正式 subtree 来源记录并完成 pull；
3. 临时移植与权威树没有内容差异；
4. A-Memorix 与 MaiBot Phase 4A 回归、Ruff、编译和 diff 检查全部通过。
