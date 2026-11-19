import pytest

import factory


@pytest.mark.smoke
def test_package_imports():
    assert factory is not None
