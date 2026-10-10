"""T17: normalize/segment/fingerprint unit tests."""
from ajp.thesis.similarity.fingerprint import exact_hash, exact_overlap_ratio, fingerprint_text, jaccard, word_shingles
from ajp.thesis.similarity.normalize import looks_like_masked_artifact, mask_references, normalize
from ajp.thesis.similarity.segment import segment_sentences
from ajp.thesis.parser import Reference


def test_normalize_is_script_agnostic_and_case_insensitive():
    latin = normalize("Ovo je Sažetak istraživanja.")
    cyrillic = normalize("Ово је Сажетак истраживања.")
    assert latin == cyrillic


def test_normalize_unifies_punctuation_and_whitespace():
    assert normalize("hello—world") == normalize("hello-world")
    assert normalize("a   b\tc") == "a b c"
    assert normalize("“quoted”") == normalize('"quoted"')


def test_mask_references_preserves_offsets():
    refs = [Reference(raw="Petrović, M. (2019). Something.", index=1)]
    text = "Uvod tekst.\nPetrović, M. (2019). Something.\nDalji tekst."
    masked = mask_references(text, refs)
    assert len(masked) == len(text)
    assert "Petrović" not in masked
    assert masked.split("\n")[0] == text.split("\n")[0]           # untouched lines keep exact offsets
    assert masked.split("\n")[2] == text.split("\n")[2]


def test_segment_sentences_respects_abbreviations_and_offsets():
    text = "Ovo je prva rečenica sa npr. jednim primerom. Ovo je druga rečenica koja sledi."
    sentences = segment_sentences(text)
    assert len(sentences) == 2
    for s in sentences:
        assert text[s.start:s.end] == s.text                      # offsets are exact into the ORIGINAL text
    assert sentences[0].text.startswith("Ovo je prva")
    assert sentences[1].text.startswith("Ovo je druga")


def test_masked_reference_stretches_never_produce_a_fake_giant_sentence():
    """Found live, 2026-09-23: a masked reference block has no sentence-ending punctuation inside it, so it
    fuses with whatever text follows - up to the NEXT real period - into one abnormally long pseudo-sentence,
    which pipeline.build_probes then picked as a top 'longest sentence' probe (a mostly-blank query sent to
    every search adapter). The filter discards that whole fused sentence; a documented, bounded side effect
    is that the first clause right after the mask (up to its own next period) is lost along with it - real
    documents always have more than one sentence in 'Prilozi'/'Biografija' after 'Literatura', so this is a
    small, bounded loss right at the reference-list boundary, never the rest of the document."""
    refs = [Reference(raw=f"Author {i}. ({2000 + i}). Some long reference title about a topic.", index=i)
           for i in range(1, 6)]
    body = "Uvod tekst prve rečenice ove teze dovoljne dužine za segmentaciju. "
    text = (body + "Literatura\n" + "\n".join(r.raw for r in refs) +
           "\nOvo je prva rečenica odmah nakon liste referenci u ovom dokumentu. " +
           "Zaključak teze je prikazan ovde sasvim detaljno i razumljivo za citanje.")
    masked = mask_references(text, refs)
    sentences = segment_sentences(masked)
    assert any(looks_like_masked_artifact(s.text) for s in sentences), "fixture should reproduce the fused artifact"
    clean = [s for s in sentences if not looks_like_masked_artifact(s.text)]
    assert all(len(s.text) < 200 for s in clean)                  # no abnormally long fused sentence remains
    assert any("Uvod tekst" in s.text for s in clean)
    # the sentence immediately after the mask is the documented bounded loss; the ONE after that (with its
    # own period reached before any masked content) is unaffected and must survive.
    assert any("Zaključak teze" in s.text for s in clean)


def test_segment_sentences_drops_very_short_fragments():
    text = "1. Uvod\n\nOvo je jedna prava rečenica dovoljne dužine za segmentaciju."
    sentences = segment_sentences(text)
    assert all(len(s.text) >= 15 for s in sentences)
    assert any("prava rečenica" in s.text for s in sentences)


def test_word_shingles_and_exact_hash():
    shingles = word_shingles(normalize("the quick brown fox jumps over the lazy dog"))
    assert shingles[0] == "the quick brown fox jumps"
    assert len(shingles) == 9 - 5 + 1
    assert exact_hash(shingles[0]) == exact_hash(shingles[0])
    assert exact_hash(shingles[0]) != exact_hash(shingles[1])


def test_fingerprint_verbatim_vs_unrelated():
    a = fingerprint_text(normalize("The quick brown fox jumps over the lazy dog near the river bank."))
    b = fingerprint_text(normalize("The quick brown fox jumps over the lazy dog near the river bank."))
    c = fingerprint_text(normalize("Completely unrelated content about marine biology and coral reefs."))
    assert exact_overlap_ratio(a, b) == 1.0
    assert jaccard(a, b) == 1.0
    assert exact_overlap_ratio(a, c) == 0.0
    assert jaccard(a, c) == 0.0


def test_fingerprint_is_script_agnostic():
    latin = fingerprint_text(normalize("Ovo je potpuno identičan tekst za proveru transliteracije skripta."))
    cyrillic = fingerprint_text(normalize("Ово је потпуно идентичан текст за проверу транслитерације скрипта."))
    assert exact_overlap_ratio(latin, cyrillic) == 1.0
    assert jaccard(latin, cyrillic) == 1.0
