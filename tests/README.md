# Test Suite Documentation

This directory contains unit and integration tests for the project using the **pytest** framework.

---

## Test Organization

To maintain clean code and readability, tests are **grouped into test classes (`Test Classes`)** based on the module, method, or feature under test (e.g., `Test<FeatureName>`).

### Guidelines & Best Practices:

1. **Class Naming:** Test classes must start with `Test` using PascalCase (e.g., `TestValidateProjectConfig`).
2. **Docstrings:** Include a brief docstring for every test class describing the purpose of the contained tests.
3. **Fixtures & Mocking:** Isolate tests from external I/O (network, database, file system) using `pytest.fixture` and `unittest.mock`.
4. **Test Naming:** Test methods must start with `test_` and clearly describe the scenario being verified.

---

## Standard Test File Structure

Every test module follows a consistent structure divided by clear section headers:

1. **Setup & Fixtures:** Global fixtures, mocks, and helper functions.
2. **Test Classes:** Isolated classes for each target method or component.

### Example Pattern:

```python
import pytest
from unittest.mock import MagicMock, AsyncMock

# ---------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------
@pytest.fixture
def updater(tmp_path, monkeypatch):
    """Create a configured `ReportUpdater` and mocked repository for testing."""
    repo = MagicMock()
    # ... mock setup ...
    return updater_instance, repo


# ---------------------------------------------------------------
# 1. Tests for validate_project_config
# ---------------------------------------------------------------
class TestValidateProjectConfig:
    """Tests for the `validate_project_config` method of the `ReportUpdater` class."""

    def test_validate_project_config_valid(self, updater):
        u, repo = updater
        repo.does_title_exist.return_value = True
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is True

    def test_validate_project_config_rejects_missing_project_page(self, updater):
        u, repo = updater
        repo.does_title_exist.return_value = False
        assert u.validate_project_config("Wikipedia:WikiProject Foo", _project()) is False


# ---------------------------------------------------------------
# 2. Tests for process_project (Async)
# ---------------------------------------------------------------
class TestProcessProject:
    """Tests for the `process_project` method of the `ReportUpdater` class."""

    @pytest.mark.asyncio
    async def test_process_project_renders_report_from_cache(self, updater):
        u, repo = updater
        # ... test execution ...
        assert "Expected Content" in written_text


# ---------------------------------------------------------------
# 3. Pipeline & Index Tests
# ---------------------------------------------------------------
class TestUpdateReports:
    """Tests for the `update_reports` method of the `ReportUpdater` class."""
    ...

class TestUpdateIndex:
    """Tests for the `update_index` method of the `ReportUpdater` class."""
    ...
```
