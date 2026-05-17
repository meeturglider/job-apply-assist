# JD Analyzer – Foundation Architecture

## Goal

Build an AI-assisted Job Description (JD) Analyzer that:

* Parses job descriptions
* Extracts ATS keywords
* Identifies role category
* Detects missing skills
* Suggests resume optimizations
* Generates tailored resume variants later

---

# Recommended Project Structure

```text
job-apply-assist/
│
├── MASTER_RESUME.md
├── requirements.txt
├── README.md
│
├── docs/
│
├── prompts/
│   ├── jd_analysis_prompt.txt
│   └── resume_tailor_prompt.txt
│
├── job_descriptions/
│   └── sample_jd.txt
│
├── outputs/
│   └── analysis_output.json
│
├── scripts/
│   ├── jd_analyzer.py
│   ├── keyword_extractor.py
│   ├── resume_tailor.py
│   └── pdf_export.py
│
└── variants/
    ├── cloud_architect.md
    ├── platform_engineer.md
    ├── devops_engineer.md
    └── ai_infra_engineer.md
```

---

# Phase 1 – JD Analyzer

## Input

* Raw job description text

## Output

* Extracted keywords
* Role category
* ATS optimization suggestions
* Missing skills analysis
* Resume alignment suggestions

---

# Suggested AI Workflow

```text
Job Description
       ↓
Keyword Extraction
       ↓
Role Classification
       ↓
Resume Gap Analysis
       ↓
Optimization Suggestions
```

---

# requirements.txt

```text
requests
rich
python-dotenv
```

---

# Initial Local LLM Integration

We will use:

```text
Ollama
```

with:

```text
qwen2.5-coder:7b
```

---

# Ollama API Endpoint

```text
http://localhost:11434/api/generate
```

---

# scripts/jd_analyzer.py

```python
import json
import requests
from pathlib import Path
from rich.console import Console
from rich.panel import Panel

console = Console()

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5-coder:7b"


def load_jd(file_path: str):
    return Path(file_path).read_text()


def build_prompt(job_description: str):
    return f"""
You are an ATS and cloud engineering recruiter assistant.

Analyze the following job description and return:

1. Role Category
2. Top Technical Skills
3. ATS Keywords
4. Required Certifications
5. Missing Skill Recommendations
6. Resume Optimization Suggestions

Return response in clean markdown.

JOB DESCRIPTION:

{job_description}
"""


def query_ollama(prompt: str):
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False
    }

    response = requests.post(OLLAMA_URL, json=payload)
    response.raise_for_status()

    return response.json()["response"]


def save_output(output: str):
    output_path = Path("outputs/analysis_output.md")
    output_path.write_text(output)
    return output_path


def main():
    console.print(Panel.fit("JD Analyzer Starting", style="bold cyan"))

    jd_path = "job_descriptions/sample_jd.txt"

    job_description = load_jd(jd_path)

    console.print("[yellow]Building AI prompt...[/yellow]")
    prompt = build_prompt(job_description)

    console.print("[yellow]Querying Ollama model...[/yellow]")
    result = query_ollama(prompt)

    output_file = save_output(result)

    console.print(Panel(result, title="ATS Analysis", style="green"))

    console.print(f"\n[bold green]Saved output:[/bold green] {output_file}")


if __name__ == "__main__":
    main()
```

---

# Example Usage

## Start Ollama

```bash
ollama ps
```

---

## Run Analyzer

```bash
python3 scripts/jd_analyzer.py
```

---

# Example Future Enhancements

## Planned Features

### Resume Tailoring

```text
JD → AI → Optimized Resume Variant
```

---

### ATS Match Scoring

Example:

```text
Resume Match Score: 82%
```

---

### LinkedIn Job Parser

Automatically:

* parse LinkedIn alerts
* classify jobs
* rank relevance
* notify via Telegram

---

### OpenClaw Integration

Future workflow:

```text
Email → OpenClaw → Telegram Reminder
```

---

# Recommended Workflow Going Forward

## Daily Process

```text
1. Paste JD
2. Run analyzer
3. Review ATS suggestions
4. Generate optimized resume variant
5. Export PDF
6. Apply
7. Track response
```

---

# Strategic Goal

Build a reusable:

```text
AI-Powered Career Engineering Platform
```

that continuously improves:

* ATS visibility
* recruiter alignment
* callback rates
* application efficiency
* technical branding
