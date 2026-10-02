"""The candidate profile: everything the scorer, email writer and form prefill need.

Lives in profile/ (gitignored), edited from the dashboard's Profile page:
    profile/profile.json   answers and preferences
    profile/resume.pdf     the file attached to applications
    profile/resume.md      resume text the scorer reads (extracted from the PDF, editable)
Set HN_PROFILE_DIR to keep it somewhere else (see config.py).
"""
import json
import shutil
import subprocess

from config import CLAUDE, PROFILE as DIR
JSON, PDF, TEXT = DIR / "profile.json", DIR / "resume.pdf", DIR / "resume.md"

# key, label, kind, hint. kind: text | textarea | list (one item per line)
FIELDS = [
    ("Contact", [
        ("name", "Full name", "text", ""),
        ("email", "Email", "text", ""),
        ("phone", "Phone", "text", ""),
        ("location", "Current location", "text", "City, State"),
        ("linkedin", "LinkedIn URL", "text", "https://www.linkedin.com/in/..."),
        ("github", "GitHub URL", "text", ""),
        ("website", "Website", "text", ""),
        ("current_company", "Current company", "text", ""),
    ]),
    ("What you're looking for", [
        ("roles", "Role focus, in priority order", "list", "One per line, most wanted first, e.g.\nAI / LLM product engineering\nFull-stack / product web"),
        ("locations", "Acceptable locations", "list", "One per line, e.g.\nSF Bay Area onsite or hybrid\nRemote (US)\nNew York"),
        ("seniority", "Seniority", "text", "e.g. Senior / Staff / Founding engineer"),
        ("dealbreakers", "Dealbreakers", "textarea", "Anything that should sink a posting's score"),
        ("strengths", "Strengths to lean on in outreach", "textarea", "Concrete projects and results you want emails to mention"),
    ]),
    ("Application answers", [
        ("work_authorization", "US work authorization", "text", "e.g. US Citizen"),
        ("requires_sponsorship", "Requires sponsorship now or later", "text", "Yes / No"),
        ("open_to_relocation", "Open to relocation", "text", "e.g. Yes, to New York or Chicago"),
        ("start_date", "Earliest start date", "text", ""),
        ("salary_expectation", "Salary expectation", "text", ""),
        ("degree", "Highest degree", "text", "e.g. BS Computer Science, or None"),
        ("years_experience", "Years of professional experience", "text", ""),
        ("skills", "Skills you'll attest to on forms", "textarea", "Only what you'd say yes to in an interview, e.g. React (hooks, state), Python + FastAPI, Postgres, AWS, Docker"),
        ("heard_about", "How did you hear about us", "text", 'e.g. Hacker News "Who is hiring?"'),
        ("other_answers", "Other standard answers", "textarea", "Anything else forms keep asking"),
    ]),
]
LIST_KEYS = {k for _, fs in FIELDS for k, _, kind, _ in fs if kind == "list"}


def load():
    return json.loads(JSON.read_text()) if JSON.exists() else {}


def save(data):
    DIR.mkdir(parents=True, exist_ok=True)
    JSON.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def resume_text():
    return TEXT.read_text() if TEXT.exists() else ""


def ready():
    p = load()
    return bool(p.get("name") and p.get("roles") and resume_text().strip())


def save_resume_pdf(blob):
    """Store the PDF and extract its text into resume.md. Returns the text ('' if extraction failed)."""
    DIR.mkdir(parents=True, exist_ok=True)
    PDF.write_bytes(blob)
    text = ""
    if shutil.which("pdftotext"):
        text = subprocess.run(["pdftotext", "-layout", str(PDF), "-"], capture_output=True, text=True).stdout
    else:  # no poppler: let headless Claude read the PDF
        out = subprocess.run(
            [CLAUDE, "-p", "--model", "haiku", "--output-format", "json", "--tools", "Read",
             "--allowedTools", "Read", "--strict-mcp-config", "--no-session-persistence"],
            input=f"Read the PDF at {PDF} and output its full text as clean Markdown, nothing else.",
            capture_output=True, text=True, timeout=300)
        try:
            text = json.loads(out.stdout).get("result", "")
        except json.JSONDecodeError:
            text = ""
    if text.strip():
        TEXT.write_text(text.strip() + "\n")
    return text.strip()


def as_prompt():
    """Profile + resume as one block for model prompts."""
    p = load()
    lines = []
    for section, fields in FIELDS:
        lines.append(f"## {section}")
        for key, label, kind, _ in fields:
            v = p.get(key)
            if not v:
                continue
            if key in LIST_KEYS:
                v = "\n" + "\n".join(f"  {i}. {x}" for i, x in enumerate(v, 1))
            lines.append(f"- {label}: {v}")
    return f"<profile>\n" + "\n".join(lines) + f"\n</profile>\n\n<resume>\n{resume_text()}\n</resume>"


def signature():
    p = load()
    contact = " | ".join(x for x in (p.get("email"), p.get("phone"), (p.get("linkedin") or "").replace("https://www.", "").rstrip("/")) if x)
    return f"{p.get('name', '')}\n{contact}".strip()
