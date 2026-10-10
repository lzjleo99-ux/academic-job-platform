"""T16 acceptance: '3 SYNTHETIC thesis fixtures generated in-repo (build them with python-docx/pdf...)'. This
module IS that generator - the fixtures are built fresh into a tmp dir by the tests that use it (see
tests/test_thesis_checks.py), never committed as opaque binaries, so the "ground truth" (what's clean, what
defect was seeded where) lives in one place, in source, next to the code that builds it.

Three fixtures:
  clean()    - fully conforms to templates/rs_phd_generic.yaml; every check should report zero warn/fail.
  defects()  - the same skeleton with 10 independent, individually-identifiable seeded defects (returns the
               doc path plus a list of short defect ids for the test to assert against).
  cyrillic() - a clean, template-conforming document whose Serbian content (headings, abstract, body) is
               written in Cyrillic script throughout, to exercise script detection end to end.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.shared import Cm, Pt

MARGINS_OK = {"top": Cm(2.5), "bottom": Cm(2.5), "left": Cm(3.0), "right": Cm(2.0)}


def _base_doc(font_size_pt: float = 12.0, line_spacing: float = 1.5, font_name: str = "Times New Roman",
             margins=MARGINS_OK, justify: bool = True) -> Document:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = font_name
    style.font.size = Pt(font_size_pt)
    pf = style.paragraph_format
    if line_spacing == 1.5:
        pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    elif line_spacing == 1.0:
        pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    elif line_spacing == 2.0:
        pf.line_spacing_rule = WD_LINE_SPACING.DOUBLE
    else:
        pf.line_spacing = line_spacing
    if justify:
        pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    sec = doc.sections[0]
    sec.top_margin, sec.bottom_margin = margins["top"], margins["bottom"]
    sec.left_margin, sec.right_margin = margins["left"], margins["right"]
    return doc


def _heading(doc: Document, level: int, text: str) -> None:
    doc.add_heading(text, level=level)


def _para(doc: Document, text: str) -> None:
    doc.add_paragraph(text)


REFERENCES = [
    "Petrović, M. (2019). Osnove istraživačke metodologije. Beograd: Univerzitet u Beogradu.",
    "Jovanović, A., Nikolić, S. (2020). Advances in the field. Journal of Examples, 12(3), 45-60.",
    "Smith, J. (2018). A study of relevant topics. Example Press.",
    "Ilić, D. (2021). Metodološki pristupi u savremenim istraživanjima. Novi Sad: FTN.",
    "Kovačević, R. (2017). Applied statistics for researchers. Wiley.",
    "Stojanović, B. (2022). Novi pristupi u analizi podataka. Niš: Univerzitet u Nišu.",
    "Marković, T. (2016). Theoretical foundations revisited. Academic Publishing.",
    "Popović, V. (2020). Empirijska istraživanja u praksi. Kragujevac: Univerzitet u Kragujevcu.",
    "Đorđević, N. (2019). Comparative methods in science. Springer.",
    "Radić, M. (2021). Kvantitativne metode istraživanja. Beograd: Institut za nauku.",
]


def _add_common_body(doc: Document, *, skip_conclusion: bool = False, dup_materials_heading: bool = False,
                     swap_hierarchy: bool = False, dup_figure_number: bool = False,
                     dangling_citation: bool = False, uncited_reference: bool = False) -> None:
    _heading(doc, 1, "Materijal i metode")
    _para(doc, "Ovaj rad koristi kvalitativne i kvantitativne metode istraživanja [1].")
    if dup_materials_heading:
        _heading(doc, 1, "Materijal i metode")
        _para(doc, "Dodatni opis metodologije, greškom ponovljen naslov iznad.")

    if swap_hierarchy:
        _heading(doc, 3, "2.1.1 Detaljna podoblast bez nadređenog naslova nivoa 2")
        _para(doc, "Ovaj odeljak nema roditeljski naslov nivoa 2 - namerno uneta greška.")

    _heading(doc, 1, "Rezultati")
    _para(doc, "Rezultati istraživanja prikazani su u nastavku [2].")
    _para(doc, "Slika 1: Prikaz prvog rezultata.")
    _para(doc, "Slika 2: Prikaz drugog rezultata.")
    if dup_figure_number:
        _para(doc, "Slika 1: Greškom ponovljen broj slike.")
    _para(doc, "Tabela 1: Sumarni podaci.")

    _heading(doc, 1, "Diskusija")
    _para(doc, "Diskusija dobijenih rezultata u poređenju sa literaturom [3][4].")
    if dangling_citation:
        _para(doc, "Dodatno poređenje sa novijim radovima [12].")

    if not skip_conclusion:
        _heading(doc, 1, "Zaključak")
        _para(doc, "Zaključci istraživanja sumirani su u ovom poglavlju.")

    _heading(doc, 1, "Literatura")
    for ref in REFERENCES:                                            # ref index 5 (Kovačević) is deliberately
        _para(doc, ref)                                                # never cited above when uncited_reference=True

    _heading(doc, 1, "Prilozi")
    _para(doc, "Dodatni materijali priloženi uz rad.")

    _heading(doc, 1, "Biografija")
    _para(doc, "Kratka biografija autora.")


def build_clean(path: Path) -> Path:
    doc = _base_doc()
    _heading(doc, 1, "Zahvalnica")
    _para(doc, "Zahvaljujem se mentoru i komisiji na podršci.")

    _para(doc, "Apstrakt")
    _para(doc, "Ovo istraživanje se bavi analizom relevantnih pojava u datoj oblasti nauke.")
    _para(doc, "Ključne reči: istraživanje, metodologija, analiza.")

    _para(doc, "Abstract")
    _para(doc, "This research addresses the analysis of relevant phenomena in the given scientific field.")
    _para(doc, "Keywords: research, methodology, analysis.")

    _para(doc, "Sadržaj")
    _para(doc, "Uvod .......... 1")
    _para(doc, "Pregled literature .......... 2")
    _para(doc, "Materijal i metode .......... 3")
    _para(doc, "Rezultati .......... 4")
    _para(doc, "Diskusija .......... 5")
    _para(doc, "Zaključak .......... 6")
    _para(doc, "Literatura .......... 7")

    _heading(doc, 1, "Uvod")
    _para(doc, "Ovaj rad predstavlja uvod u temu istraživanja.")

    _heading(doc, 1, "Pregled literature")
    _para(doc, "Pregled dosadašnjih istraživanja u ovoj oblasti [5][6].")
    _heading(doc, 2, "Teorijski okvir")
    _para(doc, "Detaljan opis teorijskog okvira korišćenog u radu [7][8][9][10].")

    _heading(doc, 1, "Ciljevi i hipoteze istraživanja")
    _para(doc, "Ciljevi i hipoteze istraživanja definisani su u ovom poglavlju.")

    _add_common_body(doc)

    doc.save(str(path))
    return path


DEFECT_IDS = (
    "missing_section_zakljucak", "heading_hierarchy_gap", "missing_english_abstract", "toc_mismatch",
    "duplicate_figure_number", "dangling_citation", "uncited_reference", "wrong_font_size",
    "wrong_line_spacing", "duplicate_heading_title",
)


def build_defects(path: Path) -> Tuple[Path, List[str]]:
    """10 independent seeded defects (see DEFECT_IDS) - >=8 required by the acceptance criteria."""
    doc = _base_doc(font_size_pt=11.0, line_spacing=1.0)          # D8 wrong_font_size, D9 wrong_line_spacing

    _heading(doc, 1, "Zahvalnica")
    _para(doc, "Zahvaljujem se mentoru i komisiji na podršci.")

    _para(doc, "Apstrakt")
    _para(doc, "Ovo istraživanje se bavi analizom relevantnih pojava u datoj oblasti nauke.")
    _para(doc, "Ključne reči: istraživanje, metodologija, analiza.")
    # D3 missing_english_abstract: no "Abstract" block at all

    _para(doc, "Sadržaj")
    _para(doc, "Uvod .......... 1")
    _para(doc, "Metodologija istraživanja .......... 2")            # D4 toc_mismatch: no such heading exists
    _para(doc, "Rezultati .......... 4")
    _para(doc, "Diskusija .......... 5")
    _para(doc, "Literatura .......... 7")

    _heading(doc, 1, "Uvod")
    _para(doc, "Ovaj rad predstavlja uvod u temu istraživanja.")

    _heading(doc, 1, "Pregled literature")
    _para(doc, "Pregled dosadašnjih istraživanja u ovoj oblasti.")

    _heading(doc, 1, "Ciljevi i hipoteze istraživanja")
    _para(doc, "Ciljevi i hipoteze istraživanja definisani su u ovom poglavlju.")

    _add_common_body(
        doc, skip_conclusion=True,                                  # D1 missing_section_zakljucak
        dup_materials_heading=True,                                 # D10 duplicate_heading_title
        swap_hierarchy=True,                                        # D2 heading_hierarchy_gap
        dup_figure_number=True,                                     # D5 duplicate_figure_number
        dangling_citation=True,                                     # D6 dangling_citation ([12], only 10 refs)
        uncited_reference=True,                                     # D7 uncited_reference (ref [5] never cited - see below)
    )
    # D7 uncited_reference is structural: REFERENCES[4] ("Kovačević...") is never mentioned via [5] anywhere above.

    doc.save(str(path))
    return path, list(DEFECT_IDS)


def build_cyrillic(path: Path) -> Path:
    """Same clean skeleton, written in Cyrillic throughout (headings, abstract, body) - exercises script
    detection (parser.detect_script) end to end, not just as a unit test of the regex in isolation."""
    doc = _base_doc()
    _heading(doc, 1, "Захвалница")
    _para(doc, "Захваљујем се ментору и комисији на подршци.")

    _para(doc, "Апстракт")
    _para(doc, "Ово истраживање се бави анализом релевантних појава у датој области науке.")
    _para(doc, "Кључне речи: истраживање, методологија, анализа.")

    _para(doc, "Abstract")
    _para(doc, "This research addresses the analysis of relevant phenomena in the given scientific field.")
    _para(doc, "Keywords: research, methodology, analysis.")

    _para(doc, "Садржај")
    _para(doc, "Увод .......... 1")
    _para(doc, "Преглед литературе .......... 2")
    _para(doc, "Материјал и методе .......... 3")
    _para(doc, "Резултати .......... 4")
    _para(doc, "Дискусија .......... 5")
    _para(doc, "Закључак .......... 6")
    _para(doc, "Литература .......... 7")

    _heading(doc, 1, "Увод")
    _para(doc, "Овај рад представља увод у тему истраживања.")

    _heading(doc, 1, "Преглед литературе")
    _para(doc, "Преглед досадашњих истраживања у овој области.")
    _heading(doc, 2, "Теоријски оквир")
    _para(doc, "Детаљан опис теоријског оквира коришћеног у раду.")

    _heading(doc, 1, "Циљеви и хипотезе истраживања")
    _para(doc, "Циљеви и хипотезе истраживања дефинисани су у овом поглављу.")

    _heading(doc, 1, "Материјал и методе")
    _para(doc, "Овај рад користи квалитативне и квантитативне методе истраживања [1].")

    _heading(doc, 1, "Резултати")
    _para(doc, "Резултати истраживања приказани су у наставку [2].")
    _para(doc, "Слика 1: Приказ првог резултата.")
    _para(doc, "Табела 1: Сумарни подаци.")

    _heading(doc, 1, "Дискусија")
    _para(doc, "Дискусија добијених резултата у поређењу са литературом [3][4].")

    _heading(doc, 1, "Закључак")
    _para(doc, "Закључци истраживања сумирани су у овом поглављу.")

    _heading(doc, 1, "Литература")
    for ref in REFERENCES[:4]:
        _para(doc, ref)

    _heading(doc, 1, "Прилози")
    _para(doc, "Додатни материјали приложени уз рад.")
    _heading(doc, 1, "Биографија")
    _para(doc, "Кратка биографија аутора.")

    doc.save(str(path))
    return path
