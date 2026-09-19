import pytest

from readmission_risk.pipeline.spark_session import make_spark_session


@pytest.fixture(scope="session")
def spark():
    session = make_spark_session("test")
    yield session
    session.stop()
