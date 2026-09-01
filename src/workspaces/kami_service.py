"""Kami 管理员记忆安全域会话服务（Phase 5A）。

职责边界：
- 维护 kami_session_states 会话状态（active/expired/revoked/exited），
  状态按键 session_id + person_id 生效；
- 私聊 /kami 直接激活；群聊先 /kami 触发 30 秒内存确认挑战，
  只有 /kami confirm 才能激活（挑战不落库）；
- 默认 TTL 900 秒并 clamp 到 60..86400 秒；
- 每次 resolve_active 重新校验 boot、权限组、成员、上下文、能力与 TTL，
  失效时立即写 expired/revoked；
- 进程重启后，旧 boot 的 active 会话在首次使用时批量置为 expired；
- 控制审计只记录枚举 command/reason 与安全 metadata，绝不保存消息正文。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Optional
from uuid import uuid4

import json
import threading

from sqlalchemy import update
from sqlmodel import Session, col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import (
    BotControlAudit,
    BotProfile,
    KamiSessionState,
    MemoryAccessAudit,
)
from src.common.logger import get_logger

from .access_resolver import MemoryAccessDecision, access_resolver

logger = get_logger("workspace.kami")

# 每次进程启动生成唯一标识；重启后所有旧 boot 的 active 会话在首次使用时批量失效。
PROCESS_BOOT_ID = uuid4().hex

KAMI_BOT_PROFILE_ID = "bot-profile-kami"
KAMI_MEMORY_SPACE_ID = "memory-space-kami"

DEFAULT_KAMI_TTL_SECONDS = 900
MIN_KAMI_TTL_SECONDS = 60
MAX_KAMI_TTL_SECONDS = 86400
CONFIRM_WINDOW_SECONDS = 30

# kami_session_states.status 枚举
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"
STATUS_EXITED = "exited"

# bot_control_audit.result 枚举
RESULT_SUCCESS = "success"
RESULT_DENIED = "denied"
RESULT_FAILED = "failed"

# bot_control_audit.command 枚举（完整原始命令映射后的稳定枚举值）
COMMAND_KAMI = "kami"
COMMAND_KAMI_CONFIRM = "kami_confirm"
COMMAND_KAMI_OFF = "kami_off"
COMMAND_KAMI_STATUS = "kami_status"

_COMMAND_ALIASES = {
    "/kami": COMMAND_KAMI,
    "/kami confirm": COMMAND_KAMI_CONFIRM,
    "/kami off": COMMAND_KAMI_OFF,
    "/kami status": COMMAND_KAMI_STATUS,
}


@dataclass(frozen=True, slots=True)
class ActiveKamiSession:
    """一次已通过全部校验的 Kami 会话快照。"""

    session_id: str
    person_id: str
    kami_bot_profile_id: str
    activated_from_bot_profile_id: str
    permission_group_id: str
    activated_at: datetime
    expires_at: datetime
    last_used_at: datetime
    revision: int
    remaining_seconds: int = 0


@dataclass(frozen=True, slots=True)
class KamiCommandResult:
    """Kami 命令处理结果；只携带枚举/快照，不携带任何消息正文。"""

    command: str
    result: str
    reason: str = ""
    activated: bool = False
    needs_confirm: bool = False
    active: Optional[ActiveKamiSession] = None
    status: Optional["KamiStatus"] = None


@dataclass(frozen=True, slots=True)
class KamiStatus:
    """/kami status 的展示快照。"""

    session_id: str
    person_id: str
    active: bool
    status: str = ""
    kami_bot_profile_id: str = ""
    activated_from_bot_profile_id: str = ""
    permission_group_id: str = ""
    activated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    remaining_seconds: int = 0


@dataclass(frozen=True, slots=True)
class _PendingConfirmation:
    """群聊确认挑战，只保存在进程内存中，绝不落库。"""

    expires_at: datetime
    workspace_id: str
    activated_from_bot_profile_id: str


# 模块级并发控制：按键 RLock 序列化同一 session+person 的状态变更；
# 全局锁只保护 boot 批量失效与按键锁字典本身。
_key_locks: dict[tuple[str, str], threading.RLock] = {}
_key_locks_guard = threading.Lock()
_pending_confirmations: dict[tuple[str, str], _PendingConfirmation] = {}
_pending_guard = threading.Lock()
_boot_expired_for: Optional[str] = None
_boot_expired_guard = threading.Lock()


def _key_lock(session_id: str, person_id: str) -> threading.RLock:
    key = (session_id, person_id)
    with _key_locks_guard:
        lock = _key_locks.get(key)
        if lock is None:
            lock = threading.RLock()
            _key_locks[key] = lock
        return lock


class KamiService:
    """Kami 会话状态机、权限复验与控制审计。"""

    # ------------------------------------------------------------------
    # 对外命令入口
    # ------------------------------------------------------------------

    def handle_command(
        self,
        text: str,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        audience_type: str,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> KamiCommandResult:
        """处理完整原始 Kami 命令；句子/引用等非精确命令一律拒绝。

        只有四个精确命令会进入控制审计；其余输入返回 denied 且不产生审计。
        """
        normalized = str(text or "").strip()
        command = _COMMAND_ALIASES.get(normalized)
        if command is None:
            return KamiCommandResult(command="", result=RESULT_DENIED, reason="kami.invalid_command")
        if audience_type not in {"private", "group"}:
            return KamiCommandResult(command=command, result=RESULT_DENIED, reason="kami.invalid_audience")
        if command == COMMAND_KAMI:
            return self._enter(
                session_id=session_id,
                person_id=person_id,
                platform=platform,
                workspace_id=workspace_id,
                activated_from_bot_profile_id=activated_from_bot_profile_id,
                audience_type=audience_type,
                ttl_seconds=ttl_seconds,
                now=now,
            )
        if command == COMMAND_KAMI_CONFIRM:
            return self._confirm(
                session_id=session_id,
                person_id=person_id,
                platform=platform,
                workspace_id=workspace_id,
                activated_from_bot_profile_id=activated_from_bot_profile_id,
                audience_type=audience_type,
                ttl_seconds=ttl_seconds,
                now=now,
            )
        if command == COMMAND_KAMI_OFF:
            return self.off(session_id=session_id, person_id=person_id, platform=platform, now=now)
        return self._status_command(
            session_id=session_id,
            person_id=person_id,
            platform=platform,
            workspace_id=workspace_id,
            audience_type=audience_type,
            now=now,
        )

    def activate(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        audience_type: str,
        ttl_seconds: Optional[int] = None,
        now: Optional[datetime] = None,
        command: str = COMMAND_KAMI,
    ) -> KamiCommandResult:
        """事务内终止旧 active 并创建新 active，按键 session_id+person_id。"""
        if audience_type not in {"private", "group"}:
            raise ValueError("audience_type 只能是 private/group")
        with _key_lock(session_id, person_id):
            self._ensure_old_boot_expired()
            current_now = now or datetime.now()
            ttl = self._clamp_ttl(ttl_seconds)
            expires_at = current_now + timedelta(seconds=ttl)
            with get_db_session() as session:
                try:
                    decision = self._resolve_permission(
                        session,
                        person_id=person_id,
                        session_id=session_id,
                        workspace_id=workspace_id,
                        audience_type=audience_type,
                    )
                except PermissionError:
                    self._write_control_audit(
                        session,
                        session_id=session_id,
                        person_id=person_id,
                        platform=platform,
                        command=command,
                        before_bot_profile_id=activated_from_bot_profile_id,
                        after_bot_profile_id="",
                        permission_group_id="",
                        result=RESULT_DENIED,
                        reason="kami.denied",
                        metadata_json=json.dumps(
                            {"audience_type": audience_type, "ttl_seconds": ttl},
                            ensure_ascii=False,
                        ),
                    )
                    return KamiCommandResult(command=command, result=RESULT_DENIED, reason="kami.denied")
                except ValueError:
                    self._write_control_audit(
                        session,
                        session_id=session_id,
                        person_id=person_id,
                        platform=platform,
                        command=command,
                        before_bot_profile_id=activated_from_bot_profile_id,
                        after_bot_profile_id="",
                        permission_group_id="",
                        result=RESULT_FAILED,
                        reason="kami.config_conflict",
                        metadata_json=json.dumps(
                            {"audience_type": audience_type, "ttl_seconds": ttl},
                            ensure_ascii=False,
                        ),
                    )
                    return KamiCommandResult(command=command, result=RESULT_FAILED, reason="kami.config_conflict")

                # 事务内终止旧 active（同 session+person），再创建新 active
                for old in self._iter_active(session, session_id, person_id):
                    old.status = STATUS_EXITED
                    old.revision += 1
                    session.add(old)
                state = KamiSessionState(
                    id=uuid4().hex,
                    session_id=session_id,
                    person_id=person_id,
                    kami_bot_profile_id=KAMI_BOT_PROFILE_ID,
                    activated_from_bot_profile_id=activated_from_bot_profile_id,
                    permission_group_id=decision.permission_group_id,
                    status=STATUS_ACTIVE,
                    activated_at=current_now,
                    expires_at=expires_at,
                    last_used_at=current_now,
                    process_boot_id=PROCESS_BOOT_ID,
                    revision=1,
                )
                session.add(state)
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=command,
                    before_bot_profile_id=activated_from_bot_profile_id,
                    after_bot_profile_id=state.kami_bot_profile_id,
                    permission_group_id=decision.permission_group_id,
                    result=RESULT_SUCCESS,
                    reason="kami.activated",
                    metadata_json=json.dumps(
                        {"audience_type": audience_type, "ttl_seconds": ttl},
                        ensure_ascii=False,
                    ),
                )
                return KamiCommandResult(
                    command=command,
                    result=RESULT_SUCCESS,
                    reason="kami.activated",
                    activated=True,
                    active=self._snapshot(state, current_now),
                )

    def off(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        now: Optional[datetime] = None,
    ) -> KamiCommandResult:
        """/kami off：退出 Kami，恢复原 BotProfile 由调用方负责路由切换。"""
        with _key_lock(session_id, person_id):
            self._ensure_old_boot_expired()
            with get_db_session() as session:
                row = self._find_active(session, session_id, person_id)
                if row is None:
                    self._write_control_audit(
                        session,
                        session_id=session_id,
                        person_id=person_id,
                        platform=platform,
                        command=COMMAND_KAMI_OFF,
                        before_bot_profile_id="",
                        after_bot_profile_id="",
                        permission_group_id="",
                        result=RESULT_DENIED,
                        reason="kami.not_active",
                        metadata_json="{}",
                    )
                    return KamiCommandResult(command=COMMAND_KAMI_OFF, result=RESULT_DENIED, reason="kami.not_active")
                row.status = STATUS_EXITED
                row.revision += 1
                session.add(row)
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=COMMAND_KAMI_OFF,
                    before_bot_profile_id=row.kami_bot_profile_id,
                    after_bot_profile_id=row.activated_from_bot_profile_id,
                    permission_group_id=row.permission_group_id,
                    result=RESULT_SUCCESS,
                    reason="kami.exited",
                    metadata_json="{}",
                )
                return KamiCommandResult(command=COMMAND_KAMI_OFF, result=RESULT_SUCCESS, reason="kami.exited")

    def status(
        self,
        *,
        session_id: str,
        person_id: str,
        workspace_id: str,
        audience_type: str,
        platform: str,
        now: Optional[datetime] = None,
    ) -> KamiStatus:
        """/kami status：返回当前会话状态，同时触发一次完整复验。"""
        current_now = now or datetime.now()
        active = self.resolve_active(
            session_id=session_id,
            person_id=person_id,
            workspace_id=workspace_id,
            audience_type=audience_type,
            now=current_now,
        )
        with get_db_session() as session:
            self._write_control_audit(
                session,
                session_id=session_id,
                person_id=person_id,
                platform=platform,
                command=COMMAND_KAMI_STATUS,
                before_bot_profile_id=active.kami_bot_profile_id if active else "",
                after_bot_profile_id=active.kami_bot_profile_id if active else "",
                permission_group_id=active.permission_group_id if active else "",
                result=RESULT_SUCCESS,
                reason="kami.status",
                metadata_json=json.dumps({"audience_type": audience_type}, ensure_ascii=False),
            )
        if active is None:
            return KamiStatus(session_id=session_id, person_id=person_id, active=False)
        return KamiStatus(
            session_id=session_id,
            person_id=person_id,
            active=True,
            status=STATUS_ACTIVE,
            kami_bot_profile_id=active.kami_bot_profile_id,
            activated_from_bot_profile_id=active.activated_from_bot_profile_id,
            permission_group_id=active.permission_group_id,
            activated_at=active.activated_at,
            expires_at=active.expires_at,
            remaining_seconds=active.remaining_seconds,
        )

    def resolve_active(
        self,
        *,
        session_id: str,
        person_id: str,
        workspace_id: str,
        audience_type: str,
        now: Optional[datetime] = None,
    ) -> Optional[ActiveKamiSession]:
        """每次调用都完整复验会话；失效时立即写 expired/revoked。"""
        if audience_type not in {"private", "group"}:
            raise ValueError("audience_type 只能是 private/group")
        with _key_lock(session_id, person_id):
            self._ensure_old_boot_expired()
            current_now = now or datetime.now()
            with get_db_session() as session:
                row = self._find_active(session, session_id, person_id)
                if row is None:
                    return None
                if row.process_boot_id != PROCESS_BOOT_ID:
                    self._mark_status(session, row, STATUS_EXPIRED)
                    return None
                if row.expires_at <= current_now:
                    self._mark_status(session, row, STATUS_EXPIRED)
                    return None
                if not self._permission_still_valid(session, row, workspace_id, audience_type):
                    self._mark_status(session, row, STATUS_REVOKED)
                    return None
                row.last_used_at = current_now
                session.add(row)
                return self._snapshot(row, current_now)

    @staticmethod
    def record_memory_access_audit(
        *,
        trace_id: str = "",
        session_id: str = "",
        person_id: str = "",
        workspace_id: str = "",
        active_bot_profile_id: str = "",
        permission_group_id: str = "",
        access_mode: str = "normal",
        query: str = "",
        requested_scope: Optional[dict] = None,
        allowed_scope: Optional[dict] = None,
        denied_scope: Optional[dict] = None,
        result_count: int = 0,
        latency_ms: int = 0,
    ) -> None:
        """记录记忆访问审计；只保存不可逆 query_hash 与作用域 JSON。

        查询原文与记忆正文一律不进库。
        """
        query_hash = sha256(str(query or "").encode("utf-8")).hexdigest()
        with get_db_session() as session:
            session.add(
                MemoryAccessAudit(
                    trace_id=trace_id or "",
                    session_id=session_id or "",
                    person_id=person_id or "",
                    workspace_id=workspace_id or "",
                    active_bot_profile_id=active_bot_profile_id or "",
                    permission_group_id=permission_group_id or "",
                    access_mode=access_mode,
                    query_hash=query_hash,
                    requested_scope_json=json.dumps(requested_scope or {}, ensure_ascii=False),
                    allowed_scope_json=json.dumps(allowed_scope or {}, ensure_ascii=False),
                    denied_scope_json=json.dumps(denied_scope or {}, ensure_ascii=False),
                    result_count=int(result_count or 0),
                    latency_ms=int(latency_ms or 0),
                    created_at=datetime.now(),
                )
            )

    # ------------------------------------------------------------------
    # 内部流程
    # ------------------------------------------------------------------

    def _enter(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        audience_type: str,
        ttl_seconds: Optional[int],
        now: Optional[datetime],
    ) -> KamiCommandResult:
        with _key_lock(session_id, person_id):
            self._ensure_old_boot_expired()
            current_now = now or datetime.now()
            with get_db_session() as session:
                existing = self._find_active(session, session_id, person_id)
                if existing is not None:
                    self._write_control_audit(
                        session,
                        session_id=session_id,
                        person_id=person_id,
                        platform=platform,
                        command=COMMAND_KAMI,
                        before_bot_profile_id=existing.activated_from_bot_profile_id,
                        after_bot_profile_id=existing.kami_bot_profile_id,
                        permission_group_id=existing.permission_group_id,
                        result=RESULT_SUCCESS,
                        reason="kami.already_active",
                        metadata_json=json.dumps({"audience_type": audience_type}, ensure_ascii=False),
                    )
                    return KamiCommandResult(
                        command=COMMAND_KAMI,
                        result=RESULT_SUCCESS,
                        reason="kami.already_active",
                        active=self._snapshot(existing, current_now),
                    )
            if audience_type == "group":
                return self._group_enter_challenge(
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    workspace_id=workspace_id,
                    activated_from_bot_profile_id=activated_from_bot_profile_id,
                    now=current_now,
                )
            return self.activate(
                session_id=session_id,
                person_id=person_id,
                platform=platform,
                workspace_id=workspace_id,
                activated_from_bot_profile_id=activated_from_bot_profile_id,
                audience_type="private",
                ttl_seconds=ttl_seconds,
                now=current_now,
                command=COMMAND_KAMI,
            )

    def _group_enter_challenge(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        now: datetime,
    ) -> KamiCommandResult:
        """群聊首次 /kami：校验权限后只建立 30 秒内存确认挑战，不落库。"""
        with get_db_session() as session:
            try:
                self._resolve_permission(
                    session,
                    person_id=person_id,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    audience_type="group",
                )
            except PermissionError:
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=COMMAND_KAMI,
                    before_bot_profile_id=activated_from_bot_profile_id,
                    after_bot_profile_id="",
                    permission_group_id="",
                    result=RESULT_DENIED,
                    reason="kami.denied",
                    metadata_json=json.dumps({"audience_type": "group"}, ensure_ascii=False),
                )
                return KamiCommandResult(command=COMMAND_KAMI, result=RESULT_DENIED, reason="kami.denied")
            except ValueError:
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=COMMAND_KAMI,
                    before_bot_profile_id=activated_from_bot_profile_id,
                    after_bot_profile_id="",
                    permission_group_id="",
                    result=RESULT_FAILED,
                    reason="kami.config_conflict",
                    metadata_json=json.dumps({"audience_type": "group"}, ensure_ascii=False),
                )
                return KamiCommandResult(command=COMMAND_KAMI, result=RESULT_FAILED, reason="kami.config_conflict")
        self._set_pending(
            session_id=session_id,
            person_id=person_id,
            workspace_id=workspace_id,
            activated_from_bot_profile_id=activated_from_bot_profile_id,
            now=now,
        )
        with get_db_session() as session:
            self._write_control_audit(
                session,
                session_id=session_id,
                person_id=person_id,
                platform=platform,
                command=COMMAND_KAMI,
                before_bot_profile_id=activated_from_bot_profile_id,
                after_bot_profile_id="",
                permission_group_id="",
                result=RESULT_DENIED,
                reason="kami.group_confirm_required",
                metadata_json=json.dumps(
                    {"audience_type": "group", "confirm_window_seconds": CONFIRM_WINDOW_SECONDS},
                    ensure_ascii=False,
                ),
            )
        return KamiCommandResult(
            command=COMMAND_KAMI,
            result=RESULT_DENIED,
            reason="kami.group_confirm_required",
            needs_confirm=True,
        )

    def _confirm(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        audience_type: str,
        ttl_seconds: Optional[int],
        now: Optional[datetime],
    ) -> KamiCommandResult:
        current_now = now or datetime.now()
        pending = self._take_pending(session_id=session_id, person_id=person_id, now=current_now)
        if pending is None:
            with get_db_session() as session:
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=COMMAND_KAMI_CONFIRM,
                    before_bot_profile_id=activated_from_bot_profile_id,
                    after_bot_profile_id="",
                    permission_group_id="",
                    result=RESULT_DENIED,
                    reason="kami.confirm_expired",
                    metadata_json=json.dumps({"audience_type": audience_type}, ensure_ascii=False),
                )
            return KamiCommandResult(
                command=COMMAND_KAMI_CONFIRM,
                result=RESULT_DENIED,
                reason="kami.confirm_expired",
            )
        if (
            pending.workspace_id != workspace_id
            or pending.activated_from_bot_profile_id != activated_from_bot_profile_id
        ):
            with get_db_session() as session:
                self._write_control_audit(
                    session,
                    session_id=session_id,
                    person_id=person_id,
                    platform=platform,
                    command=COMMAND_KAMI_CONFIRM,
                    before_bot_profile_id=activated_from_bot_profile_id,
                    after_bot_profile_id="",
                    permission_group_id="",
                    result=RESULT_DENIED,
                    reason="kami.confirm_context_mismatch",
                    metadata_json=json.dumps({"audience_type": audience_type}, ensure_ascii=False),
                )
            return KamiCommandResult(
                command=COMMAND_KAMI_CONFIRM,
                result=RESULT_DENIED,
                reason="kami.confirm_context_mismatch",
            )
        return self.activate(
            session_id=session_id,
            person_id=person_id,
            platform=platform,
            workspace_id=workspace_id,
            activated_from_bot_profile_id=activated_from_bot_profile_id,
            audience_type=audience_type,
            ttl_seconds=ttl_seconds,
            now=current_now,
            command=COMMAND_KAMI_CONFIRM,
        )

    def _status_command(
        self,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        workspace_id: str,
        audience_type: str,
        now: Optional[datetime],
    ) -> KamiCommandResult:
        st = self.status(
            session_id=session_id,
            person_id=person_id,
            workspace_id=workspace_id,
            audience_type=audience_type,
            platform=platform,
            now=now,
        )
        return KamiCommandResult(command=COMMAND_KAMI_STATUS, result=RESULT_SUCCESS, reason="kami.status", status=st)

    # ------------------------------------------------------------------
    # 校验与查询
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_permission(
        session: Session,
        *,
        person_id: str,
        session_id: str,
        workspace_id: str,
        audience_type: str,
    ) -> MemoryAccessDecision:
        """复用 AccessResolver 完成权限组选择与 Kami 权限校验。"""
        decision = access_resolver.resolve(
            session,
            person_id=person_id,
            session_id=session_id,
            workspace_id=workspace_id,
            home_space_id=KAMI_MEMORY_SPACE_ID,
            bot_profile_id=KAMI_BOT_PROFILE_ID,
            bot_profile_type="kami",
            audience_type=audience_type,
        )
        profile = session.get(BotProfile, KAMI_BOT_PROFILE_ID)
        if profile is None or not profile.enabled or profile.profile_type != "kami":
            raise PermissionError("Kami BotProfile 不存在或已禁用")
        return decision

    def _permission_still_valid(
        self,
        session: Session,
        row: KamiSessionState,
        workspace_id: str,
        audience_type: str,
    ) -> bool:
        """复验激活时记录的权限组是否仍然生效；失效返回 False。"""
        profile = session.get(BotProfile, row.kami_bot_profile_id)
        if profile is None or not profile.enabled or profile.profile_type != "kami":
            return False
        try:
            decision = access_resolver.resolve(
                session,
                person_id=row.person_id,
                session_id=row.session_id,
                workspace_id=workspace_id,
                home_space_id=KAMI_MEMORY_SPACE_ID,
                bot_profile_id=row.kami_bot_profile_id,
                bot_profile_type="kami",
                audience_type=audience_type,
            )
        except (PermissionError, ValueError):
            return False
        return decision.permission_group_id == row.permission_group_id and decision.access_mode == "forced_kami"

    @staticmethod
    def _find_active(session: Session, session_id: str, person_id: str) -> Optional[KamiSessionState]:
        return session.exec(
            select(KamiSessionState)
            .where(
                KamiSessionState.session_id == session_id,
                KamiSessionState.person_id == person_id,
                KamiSessionState.status == STATUS_ACTIVE,
            )
            .order_by(col(KamiSessionState.activated_at).desc(), col(KamiSessionState.id).desc())
        ).first()

    @staticmethod
    def _iter_active(session: Session, session_id: str, person_id: str) -> list[KamiSessionState]:
        return list(
            session.exec(
                select(KamiSessionState).where(
                    KamiSessionState.session_id == session_id,
                    KamiSessionState.person_id == person_id,
                    KamiSessionState.status == STATUS_ACTIVE,
                )
            ).all()
        )

    @staticmethod
    def _mark_status(session: Session, row: KamiSessionState, status: str) -> None:
        row.status = status
        row.revision += 1
        session.add(row)

    @staticmethod
    def _snapshot(row: KamiSessionState, now: datetime) -> ActiveKamiSession:
        return ActiveKamiSession(
            session_id=row.session_id,
            person_id=row.person_id,
            kami_bot_profile_id=row.kami_bot_profile_id,
            activated_from_bot_profile_id=row.activated_from_bot_profile_id,
            permission_group_id=row.permission_group_id,
            activated_at=row.activated_at,
            expires_at=row.expires_at,
            last_used_at=row.last_used_at,
            revision=row.revision,
            remaining_seconds=max(0, int((row.expires_at - now).total_seconds())),
        )

    @staticmethod
    def _clamp_ttl(ttl_seconds: Optional[int]) -> int:
        """默认 900 秒，并把用户配置 clamp 到 60..86400 秒。"""
        if ttl_seconds is None:
            return DEFAULT_KAMI_TTL_SECONDS
        return max(MIN_KAMI_TTL_SECONDS, min(MAX_KAMI_TTL_SECONDS, int(ttl_seconds)))

    def _set_pending(
        self,
        *,
        session_id: str,
        person_id: str,
        workspace_id: str,
        activated_from_bot_profile_id: str,
        now: datetime,
    ) -> None:
        with _pending_guard:
            _pending_confirmations[(session_id, person_id)] = _PendingConfirmation(
                expires_at=now + timedelta(seconds=CONFIRM_WINDOW_SECONDS),
                workspace_id=workspace_id,
                activated_from_bot_profile_id=activated_from_bot_profile_id,
            )

    @staticmethod
    def _take_pending(
        *,
        session_id: str,
        person_id: str,
        now: datetime,
    ) -> Optional[_PendingConfirmation]:
        with _pending_guard:
            item = _pending_confirmations.pop((session_id, person_id), None)
            if item is None or item.expires_at <= now:
                return None
            return item

    def _ensure_old_boot_expired(self) -> None:
        """进程重启后首次使用时批量失效旧 boot 的 active 会话。"""
        global _boot_expired_for
        with _boot_expired_guard:
            if _boot_expired_for == PROCESS_BOOT_ID:
                return
            with get_db_session() as session:
                session.exec(
                    update(KamiSessionState)
                    .where(
                        KamiSessionState.status == STATUS_ACTIVE,
                        KamiSessionState.process_boot_id != PROCESS_BOOT_ID,
                    )
                    .values(status=STATUS_EXPIRED, revision=KamiSessionState.revision + 1)
                )
            _boot_expired_for = PROCESS_BOOT_ID

    @staticmethod
    def _write_control_audit(
        session: Session,
        *,
        session_id: str,
        person_id: str,
        platform: str,
        command: str,
        before_bot_profile_id: str,
        after_bot_profile_id: str,
        permission_group_id: str,
        result: str,
        reason: str,
        metadata_json: str,
    ) -> None:
        """写控制审计；只接受枚举 command/reason 与调用方构造的安全 metadata。"""
        session.add(
            BotControlAudit(
                session_id=session_id or "",
                person_id=person_id or "",
                platform=platform or "",
                command=command,
                before_bot_profile_id=before_bot_profile_id or "",
                after_bot_profile_id=after_bot_profile_id or "",
                permission_group_id=permission_group_id or "",
                result=result,
                reason=reason,
                metadata_json=metadata_json,
                created_at=datetime.now(),
            )
        )


kami_service = KamiService()
