import pytest

from bioharbor.harbor import Harbor


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def harbor(tmp_path):
    h = Harbor(home=tmp_path / "home", workers=2)
    yield h
    h.close()
