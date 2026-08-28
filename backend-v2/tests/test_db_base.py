from app.db.base import Base


def test_naming_convention_applied():
    assert Base.metadata.naming_convention["ix"] == "ix_%(column_0_label)s"
    assert Base.metadata.naming_convention["uq"] == "uk_%(table_name)s_%(column_0_N_name)s"
    assert Base.metadata.naming_convention["fk"].startswith("fk_%(table_name)s")
    assert Base.metadata.naming_convention["pk"] == "pk_%(table_name)s"
