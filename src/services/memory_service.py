from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from src.A_memorix.host_service import a_memorix_host_service
from src.common.database.migrations.v43_to_v44 import build_partition_id
from src.common.logger import get_logger
from src.workspaces import PUBLIC_MEMORY_SPACE_ID, MemoryScope, get_current_request_context, workspace_service


logger = get_logger("memory_service")


@dataclass
class MemoryHit:
    content: str
    score: float = 0.0
    hit_type: str = ""
    source: str = ""
    hash_value: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    episode_id: str = ""
    title: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content": self.content,
            "score": self.score,
            "type": self.hit_type,
            "source": self.source,
            "hash": self.hash_value,
            "metadata": self.metadata,
            "episode_id": self.episode_id,
            "title": self.title,
        }


@dataclass
class MemorySearchResult:
    summary: str = ""
    hits: List[MemoryHit] = field(default_factory=list)
    filtered: bool = False
    success: bool = True
    error: str = ""

    def to_text(self, limit: int = 5, *, truncate_content: bool = True, max_content_chars: int = 160) -> str:
        if not self.hits:
            return ""
        lines = []
        for index, item in enumerate(self.hits[: max(1, int(limit))], start=1):
            content = item.content.strip().replace("\n", " ")
            if truncate_content and len(content) > max_content_chars:
                content = content[:max_content_chars] + "..."
            lines.append(f"{index}. {content}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "error": self.error,
            "summary": self.summary,
            "hits": [item.to_dict() for item in self.hits],
            "filtered": self.filtered,
        }


@dataclass
class MemoryWriteResult:
    success: bool
    stored_ids: List[str] = field(default_factory=list)
    skipped_ids: List[str] = field(default_factory=list)
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "stored_ids": self.stored_ids,
            "skipped_ids": self.skipped_ids,
            "detail": self.detail,
        }


@dataclass
class PersonProfileResult:
    summary: str = ""
    traits: List[str] = field(default_factory=list)
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"summary": self.summary, "traits": self.traits, "evidence": self.evidence}


class MemoryService:
    async def _invoke(
        self,
        component_name: str,
        args: Optional[Dict[str, Any]] = None,
        *,
        timeout_ms: Optional[int] = None,
    ) -> Any:
        if timeout_ms is None:
            response = await a_memorix_host_service.invoke(component_name, args or {})
        else:
            response = await a_memorix_host_service.invoke(component_name, args or {}, timeout_ms=timeout_ms)
        if isinstance(response, dict):
            return response
        payload = getattr(response, "payload", None)
        if isinstance(payload, dict):
            if isinstance(payload.get("result"), dict):
                return payload["result"]
            return payload
        model_dump = getattr(response, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                inner_payload = dumped.get("payload")
                if isinstance(inner_payload, dict):
                    if isinstance(inner_payload.get("result"), dict):
                        return inner_payload["result"]
                    return inner_payload
        return response

    async def _invoke_admin(
        self,
        component_name: str,
        *,
        action: str,
        timeout_ms: Optional[int] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        if timeout_ms is None:
            payload = await self._invoke(component_name, {"action": action, **kwargs})
        else:
            payload = await self._invoke(component_name, {"action": action, **kwargs}, timeout_ms=timeout_ms)
        return payload if isinstance(payload, dict) else {"success": False, "error": "invalid_payload"}

    @staticmethod
    def _coerce_write_result(payload: Any) -> MemoryWriteResult:
        if not isinstance(payload, dict):
            return MemoryWriteResult(success=False, detail="invalid_payload")
        stored_ids = [str(item) for item in (payload.get("stored_ids") or []) if str(item).strip()]
        skipped_ids = [str(item) for item in (payload.get("skipped_ids") or []) if str(item).strip()]
        detail = str(payload.get("detail") or payload.get("reason") or "")
        if stored_ids or skipped_ids:
            success = True
        elif "success" in payload:
            success = bool(payload.get("success"))
        else:
            success = not bool(detail)
        return MemoryWriteResult(
            success=success,
            stored_ids=stored_ids,
            skipped_ids=skipped_ids,
            detail=detail,
        )

    @staticmethod
    def _coerce_search_result(payload: Any) -> MemorySearchResult:
        if not isinstance(payload, dict):
            return MemorySearchResult(success=False, error="invalid_payload")
        hits: List[MemoryHit] = []
        for item in payload.get("hits", []) or []:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {}) or {}
            if not isinstance(metadata, dict):
                metadata = {}
            if "source_branches" in item and "source_branches" not in metadata:
                metadata["source_branches"] = item.get("source_branches") or []
            if "rank" in item and "rank" not in metadata:
                metadata["rank"] = item.get("rank")
            hits.append(
                MemoryHit(
                    content=str(item.get("content", "") or ""),
                    score=float(item.get("score", 0.0) or 0.0),
                    hit_type=str(item.get("type", "") or ""),
                    source=str(item.get("source", "") or ""),
                    hash_value=str(item.get("hash", "") or ""),
                    metadata=metadata,
                    episode_id=str(item.get("episode_id", "") or ""),
                    title=str(item.get("title", "") or ""),
                )
            )
        success_raw = payload.get("success")
        error = str(payload.get("error", "") or "")
        success = (not bool(error)) if success_raw is None else bool(success_raw)
        return MemorySearchResult(
            summary=str(payload.get("summary", "") or ""),
            hits=hits,
            filtered=bool(payload.get("filtered", False)),
            success=success,
            error=error,
        )

    @staticmethod
    def _coerce_profile_result(payload: Any) -> PersonProfileResult:
        if not isinstance(payload, dict):
            return PersonProfileResult()
        return PersonProfileResult(
            summary=str(payload.get("summary", "") or ""),
            traits=[str(item) for item in (payload.get("traits") or []) if str(item).strip()],
            evidence=[item for item in (payload.get("evidence") or []) if isinstance(item, dict)],
        )

    @staticmethod
    def _resolve_scope(chat_id: str, memory_space_id: str = "") -> MemoryScope:
        request_context = get_current_request_context()
        requested_space_id = str(memory_space_id or "").strip()
        if requested_space_id:
            if request_context is None or request_context.session_id != str(chat_id or ""):
                raise PermissionError("显式记忆空间访问必须绑定 BotRequestContext")
            if requested_space_id not in request_context.readable_space_ids:
                raise PermissionError("当前请求无权访问指定记忆空间")
            return MemoryScope(
                workspace_id=request_context.workspace_id,
                primary_space_id=requested_space_id,
                readable_space_ids=request_context.readable_space_ids,
                writable_space_ids=(request_context.home_memory_space_id,),
                shared_session_ids=(request_context.session_id,),
                readable_partition_ids=request_context.readable_partition_ids,
                writable_partition_ids=request_context.writable_partition_ids
                if requested_space_id == request_context.home_memory_space_id
                else (),
                access_mode=request_context.access_mode,
                security_domain=request_context.security_domain,
                trace_id=request_context.trace_id,
            )
        if request_context is not None and request_context.session_id == str(chat_id or ""):
            return MemoryScope(
                workspace_id=request_context.workspace_id,
                primary_space_id=request_context.home_memory_space_id,
                readable_space_ids=request_context.readable_space_ids,
                writable_space_ids=(request_context.home_memory_space_id,),
                shared_session_ids=(request_context.session_id,),
                readable_partition_ids=request_context.readable_partition_ids,
                writable_partition_ids=request_context.writable_partition_ids,
                access_mode=request_context.access_mode,
                security_domain=request_context.security_domain,
                trace_id=request_context.trace_id,
            )
        return workspace_service.resolve_memory_scope(chat_id, memory_space_id)

    @staticmethod
    def _writable_partition(
        scope: MemoryScope,
        *,
        partition_type: str,
        partition_key: str,
    ) -> str:
        partition_id = build_partition_id(
            scope.primary_space_id,
            partition_type,
            partition_key,
            scope.security_domain,
        )
        if partition_id in scope.writable_partition_ids:
            return partition_id
        if scope.trace_id:
            raise PermissionError(f"当前请求无权写入 {partition_type} 记忆分区")
        return partition_id

    @staticmethod
    def _audit_scope(scope: MemoryScope, *, action: str, result_count: int, success: bool) -> None:
        if not scope.trace_id:
            return
        workspace_service.record_memory_access_audit(
            workspace_id=scope.workspace_id,
            trace_id=scope.trace_id,
            action=action,
            access_mode=scope.access_mode,
            security_domain=scope.security_domain,
            readable_space_count=len(scope.readable_space_ids),
            readable_partition_count=len(scope.readable_partition_ids),
            result_count=result_count,
            success=success,
        )

    @staticmethod
    def _memory_space_from_hit(hit: MemoryHit) -> str:
        return str(hit.metadata.get("memory_space_id", "") or "").strip() or PUBLIC_MEMORY_SPACE_ID

    @classmethod
    def _filter_hits_for_scope(cls, hits: List[MemoryHit], scope: MemoryScope, limit: int) -> List[MemoryHit]:
        allowed_spaces = set(scope.readable_space_ids)
        allowed_partitions = set(scope.readable_partition_ids)
        visible = []
        for hit in hits:
            partition_id = str(hit.metadata.get("partition_id", "") or "").strip()
            if scope.trace_id and allowed_partitions and not partition_id:
                logger.warning("丢弃缺少分区来源、无法完成请求级权限校验的记忆检索结果")
                continue
            if partition_id and allowed_partitions and partition_id not in allowed_partitions:
                logger.warning("丢弃超出当前请求分区范围的记忆检索结果")
                continue
            if cls._memory_space_from_hit(hit) not in allowed_spaces:
                logger.warning("丢弃超出当前请求记忆空间范围的检索结果")
                continue
            visible.append(hit)
            if len(visible) >= limit:
                break
        return visible

    async def search(
        self,
        query: str,
        *,
        limit: int = 5,
        mode: str = "search",
        chat_id: str = "",
        person_id: str = "",
        time_start: str | float | None = None,
        time_end: str | float | None = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
        memory_space_id: str = "",
    ) -> MemorySearchResult:
        clean_query = str(query or "").strip()
        normalized_time_start = None if time_start in {None, ""} else time_start
        normalized_time_end = None if time_end in {None, ""} else time_end
        if not clean_query and normalized_time_start is None and normalized_time_end is None:
            return MemorySearchResult()
        try:
            scope = self._resolve_scope(chat_id, memory_space_id)
            backend_limit = max(1, int(limit))
            search_args = {
                "query": clean_query,
                "limit": backend_limit,
                "mode": mode,
                "chat_id": chat_id,
                "shared_chat_ids": list(scope.shared_session_ids) if len(scope.shared_session_ids) > 1 else [],
                "person_id": person_id,
                "time_start": normalized_time_start,
                "time_end": normalized_time_end,
                "respect_filter": bool(respect_filter),
                "user_id": str(user_id or "").strip(),
                "group_id": str(group_id or "").strip(),
                "allowed_memory_space_ids": list(scope.readable_space_ids),
                "allowed_partition_ids": list(scope.readable_partition_ids),
                "access_trace_id": scope.trace_id,
            }
            read_domains = ("normal", "kami") if scope.access_mode == "forced_kami" else (scope.security_domain,)
            domain_results = []
            for read_domain in read_domains:
                payload = await self._invoke(
                    "search_memory",
                    {**search_args, "security_domain": read_domain},
                )
                domain_results.append(self._coerce_search_result(payload))
            result = MemorySearchResult(
                summary="\n".join(item.summary for item in domain_results if item.summary),
                hits=sorted(
                    (hit for item in domain_results for hit in item.hits),
                    key=lambda hit: hit.score,
                    reverse=True,
                ),
                filtered=any(item.filtered for item in domain_results),
                success=all(item.success for item in domain_results),
                error="; ".join(item.error for item in domain_results if item.error),
            )
            result.hits = self._filter_hits_for_scope(result.hits, scope, max(1, int(limit)))
            self._audit_scope(scope, action="search", result_count=len(result.hits), success=result.success)
            return result
        except Exception as exc:
            logger.warning(f"长期记忆搜索失败: {exc}")
            return MemorySearchResult(success=False, error=str(exc))

    async def enqueue_feedback_task(
        self,
        *,
        query_tool_id: str,
        session_id: str,
        query_timestamp: Any = None,
        structured_content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            payload = await self._invoke(
                "enqueue_feedback_task",
                {
                    "query_tool_id": str(query_tool_id or "").strip(),
                    "session_id": str(session_id or "").strip(),
                    "query_timestamp": query_timestamp,
                    "structured_content": structured_content if isinstance(structured_content, dict) else {},
                },
                timeout_ms=10000,
            )
        except Exception as exc:
            logger.warning(f"反馈纠错任务入队失败: {exc}")
            return {"success": False, "queued": False, "reason": str(exc)}
        return (
            payload if isinstance(payload, dict) else {"success": False, "queued": False, "reason": "invalid_payload"}
        )

    async def ingest_summary(
        self,
        *,
        external_id: str,
        chat_id: str,
        text: str,
        participants: Optional[List[str]] = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
        memory_space_id: str = "",
    ) -> MemoryWriteResult:
        try:
            scope = self._resolve_scope(chat_id, memory_space_id)
            scoped_metadata = dict(metadata or {})
            scoped_metadata["memory_space_id"] = scope.primary_space_id
            scoped_metadata["workspace_id"] = scope.workspace_id
            payload = await self._invoke(
                "ingest_summary",
                {
                    "external_id": external_id,
                    "chat_id": chat_id,
                    "text": text,
                    "participants": participants or [],
                    "time_start": time_start,
                    "time_end": time_end,
                    "tags": tags or [],
                    "metadata": scoped_metadata,
                    "respect_filter": bool(respect_filter),
                    "user_id": str(user_id or "").strip(),
                    "group_id": str(group_id or "").strip(),
                    "memory_space_id": scope.primary_space_id,
                    "partition_id": self._writable_partition(
                        scope,
                        partition_type="conversation" if chat_id else "shared",
                        partition_key=chat_id or "shared",
                    ),
                    "security_domain": scope.security_domain,
                    "source_session_id": chat_id,
                    "workspace_id": scope.workspace_id,
                },
            )
            result = self._coerce_write_result(payload)
            self._audit_scope(scope, action="ingest_summary", result_count=len(result.stored_ids), success=result.success)
            if result.success:
                workspace_service.register_memory_objects(
                    object_type="memory",
                    object_ids=result.stored_ids,
                    memory_space_id=scope.primary_space_id,
                    source_session_id=chat_id,
                    partition_type="conversation",
                    partition_key=chat_id,
                )
            return result
        except Exception as exc:
            logger.warning(f"长期记忆写入摘要失败: {exc}")
            return MemoryWriteResult(success=False, detail=str(exc))

    async def ingest_text(
        self,
        *,
        external_id: str,
        source_type: str,
        text: str,
        chat_id: str = "",
        person_ids: Optional[List[str]] = None,
        participants: Optional[List[str]] = None,
        timestamp: float | None = None,
        time_start: float | None = None,
        time_end: float | None = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        entities: Optional[List[str]] = None,
        relations: Optional[List[Dict[str, Any]]] = None,
        respect_filter: bool = True,
        user_id: str = "",
        group_id: str = "",
        memory_space_id: str = "",
    ) -> MemoryWriteResult:
        try:
            scope = self._resolve_scope(chat_id, memory_space_id)
            scoped_metadata = dict(metadata or {})
            scoped_metadata["memory_space_id"] = scope.primary_space_id
            scoped_metadata["workspace_id"] = scope.workspace_id
            if source_type == "person_fact" and person_ids:
                partition_type, partition_key = "person", str(person_ids[0]).strip()
            elif chat_id:
                partition_type, partition_key = "conversation", chat_id
            else:
                partition_type, partition_key = "shared", "shared"
            payload = await self._invoke(
                "ingest_text",
                {
                    "external_id": external_id,
                    "source_type": source_type,
                    "text": text,
                    "chat_id": chat_id,
                    "person_ids": person_ids or [],
                    "participants": participants or [],
                    "timestamp": timestamp,
                    "time_start": time_start,
                    "time_end": time_end,
                    "tags": tags or [],
                    "metadata": scoped_metadata,
                    "entities": entities or [],
                    "relations": relations or [],
                    "respect_filter": bool(respect_filter),
                    "user_id": str(user_id or "").strip(),
                    "group_id": str(group_id or "").strip(),
                    "memory_space_id": scope.primary_space_id,
                    "partition_id": self._writable_partition(
                        scope,
                        partition_type=partition_type,
                        partition_key=partition_key,
                    ),
                    "security_domain": scope.security_domain,
                    "source_session_id": chat_id,
                    "workspace_id": scope.workspace_id,
                },
            )
            result = self._coerce_write_result(payload)
            self._audit_scope(scope, action="ingest_text", result_count=len(result.stored_ids), success=result.success)
            if result.success:
                workspace_service.register_memory_objects(
                    object_type="memory",
                    object_ids=result.stored_ids,
                    memory_space_id=scope.primary_space_id,
                    source_session_id=chat_id,
                    partition_type=partition_type,
                    partition_key=partition_key,
                )
                workspace_service.register_memory_objects(
                    object_type="person_profile",
                    object_ids=person_ids or [],
                    memory_space_id=scope.primary_space_id,
                    source_session_id=chat_id,
                    partition_type="person",
                )
            return result
        except Exception as exc:
            logger.warning(f"长期记忆写入文本失败: {exc}")
            return MemoryWriteResult(success=False, detail=str(exc))

    async def get_person_profile(
        self,
        person_id: str,
        *,
        chat_id: str = "",
        limit: int = 10,
        memory_space_id: str = "",
    ) -> PersonProfileResult:
        clean_person_id = str(person_id or "").strip()
        if not clean_person_id:
            return PersonProfileResult()
        try:
            scope = self._resolve_scope(chat_id, memory_space_id)
            memberships = workspace_service.memory_object_space_ids("person_profile", [clean_person_id])
            profile_spaces = memberships.get(clean_person_id, {PUBLIC_MEMORY_SPACE_ID})
            if not profile_spaces.intersection(scope.readable_space_ids):
                return PersonProfileResult()
            payload = await self._invoke(
                "get_person_profile",
                {"person_id": clean_person_id, "chat_id": chat_id, "limit": max(1, int(limit))},
            )
            return self._coerce_profile_result(payload)
        except Exception as exc:
            logger.warning(f"获取人物画像失败: {exc}")
            return PersonProfileResult()

    async def maintain_memory(
        self,
        *,
        action: str,
        target: str = "",
        hours: float | None = None,
        reason: str = "",
        limit: int = 50,
    ) -> MemoryWriteResult:
        try:
            payload = await self._invoke(
                "maintain_memory",
                {"action": action, "target": target, "hours": hours, "reason": reason, "limit": limit},
            )
            if not isinstance(payload, dict):
                return MemoryWriteResult(success=False, detail="invalid_payload")
            return MemoryWriteResult(success=bool(payload.get("success")), detail=str(payload.get("detail", "") or ""))
        except Exception as exc:
            logger.warning(f"记忆维护失败: {exc}")
            return MemoryWriteResult(success=False, detail=str(exc))

    async def memory_stats(self) -> Dict[str, Any]:
        try:
            payload = await self._invoke("memory_stats", {})
            return payload if isinstance(payload, dict) else {}
        except Exception as exc:
            logger.warning(f"获取记忆统计失败: {exc}")
            return {}

    async def graph_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_graph_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"图谱管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def source_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_source_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"来源管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def episode_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_episode_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"Episode 管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def profile_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            chat_id = str(kwargs.pop("chat_id", "") or "").strip()
            memory_space_id = str(kwargs.pop("memory_space_id", "") or "").strip()
            scope = self._resolve_scope(chat_id, memory_space_id)
            payload = await self._invoke_admin("memory_profile_admin", action=action, **kwargs)
            if action not in {"query", "list", "evidence"} or not isinstance(payload, dict):
                return payload

            def is_visible(person_id: str) -> bool:
                memberships = workspace_service.memory_object_space_ids("person_profile", [person_id])
                spaces = memberships.get(person_id, {PUBLIC_MEMORY_SPACE_ID})
                return bool(spaces.intersection(scope.readable_space_ids))

            items = payload.get("items")
            if isinstance(items, list):
                payload = dict(payload)
                payload["items"] = [
                    item
                    for item in items
                    if not isinstance(item, dict)
                    or is_visible(str(item.get("person_id", "") or "").strip())
                ]
                payload["count"] = len(payload["items"])
                return payload
            person_id = str(payload.get("person_id", "") or kwargs.get("person_id", "") or "").strip()
            if person_id and not is_visible(person_id):
                return {"success": False, "error": "该人物画像不在当前记忆空间的可读范围内"}
            return payload
        except Exception as exc:
            logger.warning(f"画像管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def feedback_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_feedback_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"反馈纠错管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def runtime_admin(self, *, action: str, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_runtime_admin", action=action, **kwargs)
        except Exception as exc:
            logger.warning(f"运行时管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def import_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            if action.startswith("create_") or action == "retry_failed":
                chat_id = str(kwargs.get("chat_id", "") or "").strip()
                requested_space_id = str(kwargs.pop("memory_space_id", "") or "").strip()
                scope = self._resolve_scope(chat_id, requested_space_id)
                kwargs["memory_space_id"] = scope.primary_space_id
                kwargs["workspace_id"] = scope.workspace_id
            return await self._invoke_admin("memory_import_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"导入管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def tuning_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_tuning_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"调优管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def v5_admin(self, *, action: str, timeout_ms: Optional[int] = None, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_v5_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"V5 记忆管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def delete_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_delete_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"删除管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def memory_correction_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        try:
            return await self._invoke_admin("memory_correction_admin", action=action, timeout_ms=timeout_ms, **kwargs)
        except Exception as exc:
            logger.warning(f"记忆修正管理调用失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def fuzzy_modify_admin(self, *, action: str, timeout_ms: int = 120000, **kwargs) -> Dict[str, Any]:
        return await self.memory_correction_admin(action=action, timeout_ms=timeout_ms, **kwargs)

    async def get_recycle_bin(self, *, limit: int = 50) -> Dict[str, Any]:
        try:
            payload = await self._invoke(
                "maintain_memory", {"action": "recycle_bin", "limit": max(1, int(limit or 50))}
            )
            return payload if isinstance(payload, dict) else {"success": False, "error": "invalid_payload"}
        except Exception as exc:
            logger.warning(f"获取回收站失败: {exc}")
            return {"success": False, "error": str(exc)}

    async def restore_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="restore", target=target)

    async def reinforce_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="reinforce", target=target)

    async def freeze_memory(self, *, target: str) -> MemoryWriteResult:
        return await self.maintain_memory(action="freeze", target=target)

    async def protect_memory(self, *, target: str, hours: float | None = None) -> MemoryWriteResult:
        return await self.maintain_memory(action="protect", target=target, hours=hours)


memory_service = MemoryService()
