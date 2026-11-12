"""反思说明字段必须能写入文本列。"""

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from techins.harness.reflection import normalize_reflect_output
from techins.models import Reflection


def test_list_strategy_is_stored_as_text():
    normalized = normalize_reflect_output(
        {
            "diagnosis": ["合规规则未通过", "成果转化费用损失险"],
            "root_causes": "责任条款中的罚款未被识别为例外",
            "strategy": ["补上罚款除外的明确表述", "复核 C14"],
            "risk_if_unfixed": ["运行会在写反思记录时失败"],
        }
    )
    assert normalized["strategy"] == "补上罚款除外的明确表述；复核 C14"
    assert normalized["diagnosis"] == "合规规则未通过；成果转化费用损失险"
    assert normalized["root_causes"] == ["责任条款中的罚款未被识别为例外"]

    engine = create_engine("sqlite://")
    Reflection.__table__.create(engine)
    with Session(engine) as session:
        session.add(
            Reflection(
                run_id=1136,
                round=2,
                trigger="final_eval",
                diagnosis=normalized["diagnosis"],
                categories=["格式与契约错误"],
                strategy=normalized["strategy"],
                plan=[{"content": "合规复核校验", "tool": "check_compliance", "args": {}}],
                score_before=59.0,
            )
        )
        session.commit()
        stored = session.query(Reflection).one()
        assert stored.strategy == "补上罚款除外的明确表述；复核 C14"
        assert isinstance(stored.strategy, str)
