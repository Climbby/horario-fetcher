"""Conventions both scrapers have to agree on.

The catalogue (apps.uc.pt) and the timetable (InforEstudante) only join if
they spell the academic year and the semester the same way. Keeping that in
one stdlib-only module means the .exe carries it without extra weight, and
neither side can drift from the other.
"""

import re
import unicodedata
from datetime import date

# InforEstudante writes "1.º Semestre", apps.uc.pt writes "1º Semestre".
SEMESTER_RE = re.compile(
    r'^(\d)\s*[.º°o]+\s*(semestre|trimestre|período|periodo)$',
    re.IGNORECASE,
)

# "2026-2027", the shape apps.uc.pt uses in its URLs.
ACADEMIC_YEAR_RE = re.compile(r'(20\d\d)\s*[/-]\s*(20\d\d)')


def academic_year(today=None):
    """August starts the new year: enrolment runs before September does."""
    today = today or date.today()
    start = today.year if today.month >= 8 else today.year - 1
    return f"{start}-{start + 1}"


def parse_academic_year(text):
    """Pulls '2026-2027' out of a label like 'Licenciatura ... 2026/2027'."""
    match = ACADEMIC_YEAR_RE.search(text or "")
    return f"{match.group(1)}-{match.group(2)}" if match else None


def normalize_semester(raw):
    if not raw:
        return ""
    text = re.sub(r'\s+', ' ', raw).strip()
    match = SEMESTER_RE.match(text)
    if match:
        return f"{match.group(1)}º {match.group(2).capitalize()}"
    if re.match(r'^anual$', text, re.IGNORECASE):
        return "Anual"
    return text


def slugify(text):
    stripped = unicodedata.normalize('NFKD', text or "")
    stripped = "".join(c for c in stripped if not unicodedata.combining(c))
    stripped = re.sub(r'[^a-zA-Z0-9]+', '-', stripped).strip('-').lower()
    return stripped or "curso"
