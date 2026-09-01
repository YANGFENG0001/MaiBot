"""MemoryPartition 数据访问与旧 MemoryObjectSpace 兼容双写。"""

from typing import Iterable, Optional

from sqlmodel import col, select

from src.common.database.database import get_db_session
from src.common.database.database_model import MemoryObjectPartition, MemoryObjectSpace, MemoryPartition, MemorySpace
from src.common.database.migrations.v43_to_v44 import build_partition_id


class PartitionService:
    def __init__(self, session_factory=get_db_session) -> None:
        self._session_factory = session_factory

    @staticmethod
    def _domain(space: MemorySpace, security_domain: str = "") -> str:
        requested = security_domain.strip()
        if requested:
            if requested not in {"normal", "kami"}:
                raise ValueError("security_domain 只能是 normal/kami")
            return requested
        return "kami" if space.space_type == "kami" or space.id == "memory-space-kami" else "normal"

    def _ensure(self, memory_space_id: str, partition_type: str, partition_key: str, display_name: str, security_domain: str = "") -> MemoryPartition:
        with self._session_factory() as session:
            space = session.get(MemorySpace, memory_space_id)
            if space is None or not space.enabled:
                raise LookupError("记忆空间不存在或已禁用")
            domain = self._domain(space, security_domain)
            partition_id = build_partition_id(memory_space_id, partition_type, partition_key, domain)
            partition = session.get(MemoryPartition, partition_id)
            if partition is None:
                partition = MemoryPartition(id=partition_id, memory_space_id=memory_space_id, partition_type=partition_type, partition_key=partition_key, security_domain=domain, display_name=display_name)
                session.add(partition)
                session.flush()
            session.expunge(partition)
            return partition

    def ensure_shared_partition(self, memory_space_id: str, security_domain: str = "") -> MemoryPartition:
        return self._ensure(memory_space_id, "shared", "shared", "共享记忆", security_domain)

    def ensure_person_partition(self, memory_space_id: str, person_id: str, security_domain: str = "") -> MemoryPartition:
        key = person_id.strip()
        if not key:
            raise ValueError("person_id 不能为空")
        return self._ensure(memory_space_id, "person", key, f"人物 {key}", security_domain)

    def ensure_conversation_partition(self, memory_space_id: str, session_id: str, security_domain: str = "") -> MemoryPartition:
        key = session_id.strip()
        if not key:
            raise ValueError("session_id 不能为空")
        return self._ensure(memory_space_id, "conversation", key, f"会话 {key}", security_domain)

    def register_object_partition(self, *, object_type: str, object_id: str, partition_id: str, source_session_id: str = "", origin_space_id: Optional[str] = None, origin_partition_id: Optional[str] = None, transfer_job_id: Optional[str] = None) -> bool:
        with self._session_factory() as session:
            partition = session.get(MemoryPartition, partition_id)
            if partition is None or not partition.enabled:
                raise LookupError("记忆分区不存在或已禁用")
            existing = session.exec(select(MemoryObjectPartition).where(MemoryObjectPartition.object_type == object_type, MemoryObjectPartition.object_id == object_id, MemoryObjectPartition.partition_id == partition_id)).first()
            created = existing is None
            if created:
                session.add(MemoryObjectPartition(object_type=object_type, object_id=object_id, partition_id=partition_id, source_session_id=source_session_id, origin_space_id=origin_space_id, origin_partition_id=origin_partition_id, transfer_job_id=transfer_job_id))
            legacy = session.exec(select(MemoryObjectSpace).where(MemoryObjectSpace.object_type == object_type, MemoryObjectSpace.object_id == object_id, MemoryObjectSpace.memory_space_id == partition.memory_space_id)).first()
            if legacy is None:
                session.add(MemoryObjectSpace(object_type=object_type, object_id=object_id, memory_space_id=partition.memory_space_id, source_session_id=source_session_id, origin_space_id=origin_space_id))
            return created

    def resolve_partition_ids(self, object_type: str, object_ids: Iterable[str]) -> dict[str, set[str]]:
        ids = tuple(dict.fromkeys(str(item).strip() for item in object_ids if str(item).strip()))
        if not ids:
            return {}
        with self._session_factory() as session:
            rows = session.exec(select(MemoryObjectPartition).where(MemoryObjectPartition.object_type == object_type, col(MemoryObjectPartition.object_id).in_(ids))).all()
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row.object_id, set()).add(row.partition_id)
        return result


partition_service = PartitionService()
