from __future__ import annotations

import pytest

from app.models import CandidateProfile
from app.profile.master_resume import parse_master_resume_text

MASTER = """# Test Candidate
## Senior Platform Engineer
- test@example.com | +1 555 123 4567
- https://www.linkedin.com/in/test-candidate | https://github.com/test

# Professional Summary

Platform engineer with experience building secure cloud platforms and reliable delivery pipelines.

# Core Skills

## Cloud
- Google Cloud Platform
- Kubernetes
- Terraform

## DevOps
- CI/CD
- Jenkins
- Python
- Linux

# Professional Experience

## Example Corp

### Senior Platform Engineer
Jan 2020 – Present | Remote
- Built GCP landing zones with Terraform and Kubernetes.
- Automated CI/CD pipelines with Jenkins and Python.
- Improved platform reliability for internal engineering teams.

## Earlier Co

### Systems Engineer
Jan 2018 – Dec 2019 | Remote
- Managed Linux servers and cloud networking.

# Education

## B.Tech in Computer Science
Example University

# Certifications

- Google Cloud Professional Cloud Architect
"""


@pytest.fixture
def profile() -> CandidateProfile:
    return parse_master_resume_text(MASTER, source="tests/conftest.py")


@pytest.fixture
def master_text() -> str:
    return MASTER
