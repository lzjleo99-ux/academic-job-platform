"""T17 acceptance: 'planted-duplication fixture set: (a) verbatim copy from a seeded corpus doc, (b) copy
with Serbian Latin->Cyrillic transliteration, (c) light paraphrase, (d) clean control; assert a/d ordered
correctly, b detected at verbatim-equivalent strength, c lands in paraphrase band, spans map back to correct
offsets'. The internal_corpus adapter stands in for the 'seeded OA corpus' here: it needs no network, so the
whole pipeline (segment -> normalize -> fingerprint -> match -> score) is exercised deterministically.
"""
from ajp.thesis.parser import ThesisDoc
from ajp.thesis.similarity.pipeline import run_similarity
from ajp.thesis.similarity.sources.internal_corpus import InternalCorpusAdapter
from ajp.thesis.sr_script import lat_to_cyr

SOURCE_PARAGRAPH = (
    "Istraživanje savremenih metoda mašinskog učenja pokazuje značajan napredak u oblasti obrade "
    "prirodnog jezika kod velikih tekstualnih korpusa. "
    "Modeli zasnovani na dubokim neuronskim mrežama omogućavaju preciznije razumevanje konteksta i "
    "semantike teksta unutar akademskih radova. "
    "Primena ovih tehnika u akademskim istraživanjima otvara nove mogućnosti za analizu velikih "
    "tekstualnih korpusa i naučnih publikacija."
)

PARAPHRASE_PARAGRAPH = (
    # a genuine "light" paraphrase: mostly the same sentences with a handful of words swapped, not a full
    # rewrite - a few short phrases (3-7 words) survive intact, which is what gives shingle-based Jaccard any
    # signal at all (a thorough reword shares almost no 5-word runs and reads as fully unrelated - a real,
    # documented limitation of shingle/MinHash techniques, not something worth hiding behind an unrealistic
    # fixture; see formula.py's own comment on PARAPHRASE_JACCARD_THRESHOLD).
    "Istraživanje savremenih metoda mašinskog učenja ukazuje na primetan napredak u oblasti obrade "
    "prirodnog jezika kod obimnih tekstualnih zbirki podataka. "
    "Modeli zasnovani na dubokim neuronskim mrežama omogućavaju bolje razumevanje konteksta i "
    "značenja teksta unutar naučnih radova. "
    "Primena ovih pristupa u naučnim istraživanjima stvara nove mogućnosti za proučavanje velikih "
    "tekstualnih korpusa i akademskih publikacija."
)

CLEAN_PARAGRAPH = (
    "Ovo poglavlje uopšte ne govori o mašinskom učenju niti obradi jezika u bilo kom obliku. "
    "Umesto toga, opisuje istorijski razvoj poljoprivrednih tehnika u južnoj Evropi tokom "
    "devetnaestog veka i njihov uticaj na tadašnje društvo. "
    "Analiza se fokusira na promene u korišćenju zemljišta i njihov dugoročni uticaj na lokalne "
    "seoske zajednice."
)

INTRO = "Uvod u ovo poglavlje sadrži opšti pregled teme koja se dalje razmatra u nastavku teksta. "
OUTRO = " Zaključak ovog odeljka sumira glavne nalaze predstavljene u prethodnim pasusima."


def _doc(body_paragraph: str) -> ThesisDoc:
    full_text = INTRO + body_paragraph + OUTRO
    return ThesisDoc(source_format="docx", page_count=None, full_text=full_text, metadata={"title": "Test Thesis"})


def _adapter() -> dict:
    corpus = InternalCorpusAdapter([("Seeded source document", "", SOURCE_PARAGRAPH)])
    return {"internal_corpus": corpus}


def test_a_verbatim_copy_scores_high_and_d_control_scores_zero():
    report_a = run_similarity(_doc(SOURCE_PARAGRAPH), _adapter())
    report_d = run_similarity(_doc(CLEAN_PARAGRAPH), _adapter())
    assert report_a.estimate_pct > 50
    assert report_d.estimate_pct == 0.0
    assert report_a.estimate_pct > report_d.estimate_pct           # a ordered above d


def test_b_transliterated_copy_matches_at_verbatim_equivalent_strength():
    cyrillic_copy = lat_to_cyr(SOURCE_PARAGRAPH)
    report_a = run_similarity(_doc(SOURCE_PARAGRAPH), _adapter())
    report_b = run_similarity(_doc(cyrillic_copy), _adapter())
    assert report_b.estimate_pct == report_a.estimate_pct          # script-agnostic: identical strength
    assert all(m.signal == "verbatim" for m in report_b.matched_spans)


def test_c_paraphrase_lands_in_the_paraphrase_band_not_verbatim():
    report_c = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    assert report_c.matched_spans == []                             # no verbatim matches at all
    assert len(report_c.paraphrase_spans) > 0
    assert report_c.estimate_pct == 0.0                             # paraphrase never counted as verbatim


def test_ordering_a_gt_c_gt_d():
    def band(report):
        if report.matched_spans:
            return 2
        if report.paraphrase_spans:
            return 1
        return 0
    a = run_similarity(_doc(SOURCE_PARAGRAPH), _adapter())
    c = run_similarity(_doc(PARAPHRASE_PARAGRAPH), _adapter())
    d = run_similarity(_doc(CLEAN_PARAGRAPH), _adapter())
    assert band(a) > band(c) > band(d)


def test_spans_map_back_to_the_correct_offsets_in_the_thesis_text():
    doc = _doc(SOURCE_PARAGRAPH)
    report = run_similarity(doc, _adapter())
    assert report.matched_spans, "expected at least one verbatim match to check offsets against"
    for m in report.matched_spans:
        assert doc.full_text[m.start:m.end] == m.snippet[: m.end - m.start] or doc.full_text[m.start:m.end].startswith(m.snippet[:50])
        # the snippet is a truncated prefix of the sentence at [start:end] - confirm it is NOT just "somewhere"
        # in the document but exactly the slice the offsets claim
        assert m.start < m.end <= len(doc.full_text)
        sentence_at_offset = doc.full_text[m.start:m.end]
        assert SOURCE_PARAGRAPH.split(".")[0][:30] in doc.full_text  # sanity: source text really is embedded
        assert sentence_at_offset.strip() != ""
