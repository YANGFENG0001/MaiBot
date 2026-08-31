from contextlib import contextmanager
import importlib

from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, create_engine, select

from src.common.database.database_model import MemoryObjectPartition, MemoryObjectSpace, MemorySpace
from src.workspaces.partition_service import PartitionService
from src.workspaces.service import WorkspaceService


def _services(monkeypatch):
    engine=create_engine("sqlite://",connect_args={"check_same_thread":False})
    SQLModel.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,class_=Session,expire_on_commit=False)
    @contextmanager
    def db(auto_commit=True):
        session=factory()
        try:
            yield session
            if auto_commit:
                session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    partition_module=importlib.import_module("src.workspaces.partition_service")
    workspace_module=importlib.import_module("src.workspaces.service")
    monkeypatch.setattr(partition_module,"get_db_session",db)
    monkeypatch.setattr(workspace_module,"get_db_session",db)
    return PartitionService(db),WorkspaceService(),db


def test_partition_service_isolates_spaces_and_dual_writes(monkeypatch) -> None:
    partitions,workspaces,db=_services(monkeypatch)
    with db() as session:
        session.add_all([MemorySpace(id="space-a",name="A"),MemorySpace(id="space-b",name="B"),MemorySpace(id="memory-space-kami",name="Kami",space_type="kami")])
    person_a=partitions.ensure_person_partition("space-a","person-1")
    person_b=partitions.ensure_person_partition("space-b","person-1")
    assert person_a.id != person_b.id
    assert partitions.ensure_person_partition("space-a","person-1").id == person_a.id
    assert partitions.ensure_shared_partition("memory-space-kami").security_domain == "kami"

    assert workspaces.register_memory_objects(object_type="memory",object_ids=["summary"],memory_space_id="space-a",source_session_id="chat-a",partition_type="conversation") == 1
    assert workspaces.register_memory_objects(object_type="memory",object_ids=["import"],memory_space_id="space-a",source_session_id="chat-a",partition_type="shared") == 1
    assert workspaces.register_memory_objects(object_type="person_profile",object_ids=["person-1"],memory_space_id="space-a",source_session_id="chat-a",partition_type="person") == 1
    assert workspaces.register_memory_objects(object_type="memory",object_ids=["import"],memory_space_id="space-a",source_session_id="chat-a",partition_type="shared") == 0
    with db() as session:
        memberships=session.exec(select(MemoryObjectPartition)).all()
        legacy=session.exec(select(MemoryObjectSpace)).all()
    assert len(memberships)==3
    assert len(legacy)==3
    types={m.object_id: m.partition_id for m in memberships}
    assert types["summary"] == partitions.ensure_conversation_partition("space-a","chat-a").id
    assert types["import"] == partitions.ensure_shared_partition("space-a").id
    assert types["person-1"] == person_a.id
    assert partitions.resolve_partition_ids("memory",["summary","import"]) == {"summary":{types["summary"]},"import":{types["import"]}}
