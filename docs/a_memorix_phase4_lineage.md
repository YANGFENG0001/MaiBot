# A-Memorix Phase 4A lineage and upstream status

本记录说明 Phase 4A 的 A-Memorix 内核改动来源与当前同步状态，避免将本地实施分支误认为已经完成正式 upstream subtree 同步。

- A-Memorix upstream base: `e54bf25` (`docs: update MaiBot branch README`)
- Local upstream implementation commit: `7e1ee03` (`feat: enforce memory spaces and partitions inside retrieval kernel`)
- MaiBot integration commit: `6f9baf9e1f7f704d2281553fe0cc6a27f91c1083` (`feat: enforce partition isolation in A-Memorix kernel (upstream 7e1ee03)`)
- A-Dawn/A_memorix push: `403 Permission denied`
- `YANGFENG0001/A_memorix` fork: repository not found

## 当前状态

Phase 4A 功能状态为 `functional-complete / upstream-trace-pending`。本地 MaiBot 实施分支已包含定向移植并通过已有专项回归；A-Memorix 独立仓库的正式 upstream 推送仍需具备 `A-Dawn/A_memorix` 写权限或可用 fork 后才能完成。

## 维护要求

后续涉及 `src/A_memorix/core/**` 的行为改动，仍应先提交到 A-Memorix 的 `MaiBot_branch`，再通过 subtree/定向同步进入 MaiBot。不得把本地复制、patch 文件或 MaiBot 集成提交描述为已经完成正式 upstream sync。
