from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.config import Settings
from app.service import ApplicationService

JD = """
Senior Platform Engineer
We need a platform engineer with strong Google Cloud Platform, Kubernetes, Terraform,
Jenkins, CI/CD, Linux, and Python experience. Experience with IAM and cloud security helps.
"""


@pytest.mark.asyncio
async def test_analyze_and_generate_end_to_end(tmp_path: Path, master_text: str) -> None:
    master = tmp_path / "MASTER_RESUME.md"
    master.write_text(master_text, encoding="utf-8")
    settings = Settings(
        llm_provider="none",
        master_resume_path=master,
        portfolio_base_url="http://127.0.0.1:9",
        data_dir=tmp_path / "data",
        ollama_timeout_seconds=1,
    )
    service = ApplicationService(settings)
    record = service.start_analysis(text=JD, company="Acme", role="Senior Platform Engineer")
    status = await _wait(service, record.id)
    assert status == "analyzed"
    analyzed = service.get(record.id)
    assert analyzed is not None and analyzed.score > 0

    service.start_generation(record.id)
    status = await _wait(service, record.id, {"generated", "error"})
    assert status == "generated"
    generated = service.get(record.id)
    assert generated is not None
    kinds = {artifact.kind for artifact in generated.artifacts}
    assert {"markdown", "html", "docx"} <= kinds
    payload = service.analysis_payload(record.id)
    assert "metadata" in payload
    assert payload["metadata"]["method"] == "deterministic"


async def _wait(
    service: ApplicationService, application_id: str, terminal: set[str] | None = None
) -> str:
    terminal = terminal or {"analyzed", "error", "needs_paste"}
    for _ in range(240):
        record = service.get(application_id)
        assert record is not None
        if record.status in terminal:
            return record.status
        await asyncio.sleep(0.05)
    raise AssertionError("Timed out waiting for background task.")
