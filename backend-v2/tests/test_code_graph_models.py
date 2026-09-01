from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models.code_graph import CallEdge, CodeEntity, CodeMetric


def test_code_graph_models_roundtrip():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        caller = CodeEntity(repo="mini", entity_type="method", class_name="CommitLog",
                            method_name="putMessage", module="broker",
                            file_path="com/example/broker/CommitLog.java",
                            start_line=20, end_line=32, signature="boolean putMessage(...)")
        callee = CodeEntity(repo="mini", entity_type="method", class_name="FlushService",
                            method_name="flush", module="broker",
                            file_path="com/example/broker/FlushService.java",
                            start_line=8, end_line=11, signature="boolean flush(...)")
        s.add_all([caller, callee])
        s.flush()
        s.add(CallEdge(caller_id=caller.id, callee_id=callee.id, call_type="direct",
                       call_site_file="com/example/broker/CommitLog.java", call_site_line=20))
        s.add(CodeMetric(entity_id=caller.id, complexity=3, fan_in=1, fan_out=1, loc=13))
        s.commit()
        edge = s.query(CallEdge).one()
        assert s.get(CodeEntity, edge.caller_id).class_name == "CommitLog"
        assert s.query(CodeMetric).one().fan_out == 1
