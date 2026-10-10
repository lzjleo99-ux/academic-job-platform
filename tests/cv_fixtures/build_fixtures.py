"""T25 acceptance: synthetic CV fixtures generated in-repo (docx via python-docx, pdf via reportlab - the
same convention tests/thesis_fixtures/build_fixtures.py already established for theses, reused rather than
adding a second document-generation approach). Both formats carry the IDENTICAL text content, so a
parse_cv_and_extract_profile golden-value test can assert the same extracted profile from either file.
"""
from __future__ import annotations

from pathlib import Path

from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from reportlab.lib.styles import getSampleStyleSheet

SAMPLE_CV_TEXT = """Jane Doe

PhD in Physics, University of Belgrade, 2022
Postdoctoral Researcher, Max Planck Institute, 2022-2024

Research interests: quantum physics, computer science, materials science

Skills:
Python, MATLAB, machine learning, data analysis, LaTeX

Languages:
English: fluent
Serbian: native
German: basic

I have 5 years of experience in experimental physics research.
Open to remote work. Full-time preferred.
Desired location: Germany, Netherlands, Serbia

Expected salary: EUR 45000 per year

I am a Serbian citizen and eligible to work in the EU without sponsorship.
"""


def build_docx(path: Path, text: str = SAMPLE_CV_TEXT) -> Path:
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    doc.save(str(path))
    return path


def build_pdf(path: Path, text: str = SAMPLE_CV_TEXT) -> Path:
    styles = getSampleStyleSheet()
    story = []
    for line in text.split("\n"):
        story.append(Paragraph(line if line.strip() else "&nbsp;", styles["Normal"]))
        story.append(Spacer(1, 4))
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                      topMargin=2 * cm, bottomMargin=2 * cm).build(story)
    return path


# T33 (packet 018): PM's own 2 real golden CVs (content as PM described verbatim, reconstructed here as
# a real, committed fixture rather than PM's own one-off test file) plus a 3rd social-sciences CV PM's own
# packet explicitly asked for, to prove the Q1 discipline fix never over-corrects. Real CV formatting habits
# reproduced deliberately (a header with no trailing colon on its own line, e.g. 'Languages'/'Preferences';
# an inline 'Skills: a, b, c.' shape on the PDF) - these are exactly the shapes that broke extraction live.
ML_CV_TEXT = """Ana Petrović

PhD Candidate in Computer Science, University of Belgrade

Research interests: machine learning, artificial intelligence, computer science, data science

Skills:
Python, PyTorch, NLP, data science, statistics, Linux, Docker.
Languages
Serbian (native)
English (C1)
German (B1)
Preferences
Seeking a full-time PhD/postdoc position in AI. Open to remote work and relocation to Germany, Sweden, Denmark, Netherlands.
Minimum salary: EUR 38000 per year.
Visa sponsorship desired in EU.
"""

MECH_ENG_CV_TEXT = """Marko Nikolić

PhD Candidate in Mechanical Engineering, University of Belgrade

Skills: SolidWorks, CAD, FEM, ANSYS, Python, materials testing.
Languages
Serbian (native)
English (B2)
Preferences
Seeking a full-time PhD position in mechanical engineering. Open to relocation to Austria, Germany.
Minimum salary: EUR 30000 per year.
"""

SOCIAL_SCIENCE_CV_TEXT = """Milica Jovanović

PhD Candidate in Sociology, University of Belgrade

Research interests: sociology, social policy, political science, education studies

Skills:
Qualitative research, SPSS, survey design, academic writing
Languages
Serbian (native)
English (C1)
"""


def build_ml_cv_docx(path: Path) -> Path:
    return build_docx(path, ML_CV_TEXT)


def build_mech_eng_cv_pdf(path: Path) -> Path:
    return build_pdf(path, MECH_ENG_CV_TEXT)


def build_social_science_cv_docx(path: Path) -> Path:
    return build_docx(path, SOCIAL_SCIENCE_CV_TEXT)
