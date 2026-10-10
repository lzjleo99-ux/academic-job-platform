"""T16: the 9 structural/style checks packet 008 names, run against a ``ThesisDoc`` + a ``Template``.

Every ``Finding`` carries a severity ('pass' | 'warn' | 'fail'), a human message, and - for anything
heuristic rather than a direct structural fact - a ``conf`` score and the ``excerpt`` it was read from (D6).
'pass' findings are emitted too (not just problems) so the report is a complete checklist, not just a list of
complaints; a clean, template-conforming document is expected to show only 'pass' entries, zero 'warn'/'fail'.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ajp.thesis.parser import ThesisDoc
from ajp.thesis.templates import Template

Severity = str  # 'pass' | 'warn' | 'fail'


@dataclass
class Finding:
    check: str
    severity: Severity
    message: str
    page: Optional[int] = None
    excerpt: Optional[str] = None
    conf: Optional[float] = None


@dataclass
class CheckReport:
    template_name: str
    findings: List[Finding] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for f in self.findings:
            out[f.severity] = out.get(f.severity, 0) + 1
        return out


def _norm_heading(title: str) -> str:
    return re.sub(r"\s+", " ", title.strip().lower())


# ---- 1. required/extra/out-of-order sections -----------------------------------------------------------
def check_required_sections(doc: ThesisDoc, tmpl: Template) -> List[Finding]:
    out: List[Finding] = []
    top_level = [s for s in doc.sections if s.level == 1]
    matched_keys_in_order: List[str] = []
    matched_section_for_key: Dict[str, str] = {}
    for s in top_level:
        key = tmpl.match_section_key(s.title)
        if key:
            matched_keys_in_order.append(key)
            matched_section_for_key.setdefault(key, s.raw_heading_text)
        else:
            out.append(Finding("required_sections", "warn",
                                f"top-level heading not in the template: '{s.title}'", page=s.page_start,
                                excerpt=s.raw_heading_text))

    for rs in tmpl.required_sections:
        if rs.key in matched_section_for_key:
            out.append(Finding("required_sections", "pass", f"section present: {rs.key}",
                               excerpt=matched_section_for_key[rs.key]))
        elif not rs.optional:
            out.append(Finding("required_sections", "fail", f"required section missing: {rs.key}"))
        else:
            out.append(Finding("required_sections", "pass", f"optional section absent (ok): {rs.key}"))

    expected_order = [rs.key for rs in tmpl.required_sections if rs.key in matched_keys_in_order]
    actual_order = [k for k in matched_keys_in_order if k in expected_order]
    # de-dup consecutive repeats from multi-mention headings before comparing order
    actual_order_dedup = [k for i, k in enumerate(actual_order) if i == 0 or actual_order[i - 1] != k]
    if actual_order_dedup == expected_order:
        if expected_order:
            out.append(Finding("required_sections", "pass", "required sections appear in template order"))
    else:
        out.append(Finding("required_sections", "fail",
                           f"required sections out of order: found {actual_order_dedup}, expected {expected_order}"))
    return out


# ---- 2. heading hierarchy gaps --------------------------------------------------------------------------
def check_heading_hierarchy(doc: ThesisDoc) -> List[Finding]:
    out: List[Finding] = []
    prev_level = 0
    gap_found = False
    for s in doc.sections:
        if s.level - prev_level > 1:
            out.append(Finding("heading_hierarchy", "fail",
                               f"heading level jumps from {prev_level} to {s.level} at '{s.title}' "
                               f"(a level {prev_level + 1} heading is skipped)", page=s.page_start,
                               excerpt=s.raw_heading_text))
            gap_found = True
        prev_level = s.level
    if not gap_found and doc.sections:
        out.append(Finding("heading_hierarchy", "pass", "no heading-level gaps"))
    return out


# ---- 3. abstracts ----------------------------------------------------------------------------------------
def check_abstracts(doc: ThesisDoc, tmpl: Template) -> List[Finding]:
    out: List[Finding] = []
    by_lang: Dict[str, int] = {}
    for a in doc.abstracts:
        by_lang[a.lang or "unknown"] = by_lang.get(a.lang or "unknown", 0) + 1
    for lang in tmpl.abstract_languages:
        n = by_lang.get(lang, 0)
        if n == 1:
            out.append(Finding("abstracts", "pass", f"exactly one '{lang}' abstract found"))
        elif n == 0:
            out.append(Finding("abstracts", "fail", f"no abstract detected for language '{lang}'"))
        else:
            out.append(Finding("abstracts", "warn", f"{n} abstracts detected for language '{lang}' (expected 1)"))
    for a in doc.abstracts:
        if a.script:
            out.append(Finding("abstracts", "pass", f"abstract script detected: {a.script} ({a.lang or 'unknown'})",
                               excerpt=a.text[:200], conf=0.9 if a.lang else 0.5))
    if by_lang.get("unknown"):
        out.append(Finding("abstracts", "warn",
                           f"{by_lang['unknown']} abstract(s) found but language could not be determined", conf=0.4))
    return out


# ---- 4. TOC vs actual headings --------------------------------------------------------------------------
def check_toc(doc: ThesisDoc) -> List[Finding]:
    out: List[Finding] = []
    if not doc.toc:
        out.append(Finding("toc", "warn", "no table of contents detected"))
        return out
    heading_titles = {_norm_heading(s.title): s for s in doc.sections}
    unmatched = 0
    page_mismatches = 0
    for entry in doc.toc:
        norm = _norm_heading(entry.title)
        match = heading_titles.get(norm)
        if match is None:
            # loose fallback: substring match either way
            match = next((s for s in doc.sections if norm in _norm_heading(s.title)
                         or _norm_heading(s.title) in norm), None)
        if match is None:
            unmatched += 1
            out.append(Finding("toc", "warn", f"TOC entry has no matching heading: '{entry.title}'",
                               page=entry.page, conf=0.6))
            continue
        if entry.page is not None and match.page_start is not None and entry.page != match.page_start:
            page_mismatches += 1
            out.append(Finding("toc", "fail",
                               f"TOC page for '{entry.title}' says {entry.page}, heading is on page {match.page_start}",
                               page=match.page_start))
    if unmatched == 0:
        out.append(Finding("toc", "pass", f"all {len(doc.toc)} TOC entries matched a heading"))
    if doc.page_texts and page_mismatches == 0 and unmatched == 0:
        out.append(Finding("toc", "pass", "TOC page numbers match heading pages"))
    return out


# ---- 5. figure/table numbering + captions -----------------------------------------------------------------
_FIG_MENTION = re.compile(r"\b(slika|figure|fig\.)\s*(\d+(?:\.\d+)*)\b", re.IGNORECASE)
_TAB_MENTION = re.compile(r"\b(tabela|table)\s*(\d+(?:\.\d+)*)\b", re.IGNORECASE)


def _check_numbering_continuity(items, kind: str) -> List[Finding]:
    out: List[Finding] = []
    numbers = []
    for it in items:
        if it.number is None:
            out.append(Finding(f"{kind}_numbering", "warn", f"{kind} caption without a number: '{it.caption}'",
                               page=it.page, excerpt=it.caption))
            continue
        numbers.append(it.number)
    seen: Dict[str, int] = {}
    for n in numbers:
        seen[n] = seen.get(n, 0) + 1
    dups = [n for n, c in seen.items() if c > 1]
    for d in dups:
        out.append(Finding(f"{kind}_numbering", "fail", f"duplicate {kind} number: {d}"))
    try:
        ints = sorted(int(n.split(".")[-1]) for n in numbers if n.split(".")[-1].isdigit())
        gaps = [i for i in range(1, len(ints)) if ints[i] - ints[i - 1] > 1]
        if gaps and not dups:
            out.append(Finding(f"{kind}_numbering", "warn", f"{kind} numbering has gaps: sequence is {ints}"))
        elif not dups and ints:
            out.append(Finding(f"{kind}_numbering", "pass", f"{kind} numbering is continuous: {ints}"))
    except ValueError:
        pass
    return out


def check_figures_tables(doc: ThesisDoc) -> List[Finding]:
    out = _check_numbering_continuity(doc.figures, "figure") + _check_numbering_continuity(doc.tables, "table")
    caption_numbers = {f.number for f in doc.figures if f.number} | {t.number for t in doc.tables if t.number}
    mentioned = {m.group(2) for m in _FIG_MENTION.finditer(doc.full_text)} | \
                {m.group(2) for m in _TAB_MENTION.finditer(doc.full_text)}
    orphan_mentions = mentioned - caption_numbers
    if orphan_mentions:
        out.append(Finding("figures_tables", "warn",
                           f"figure/table number(s) referenced in text with no matching caption: {sorted(orphan_mentions)}",
                           conf=0.5))
    elif mentioned:
        out.append(Finding("figures_tables", "pass", "every figure/table reference in the text has a matching caption"))
    return out


# ---- 6. reference/citation cross-check --------------------------------------------------------------------
_CITATION_NUMS = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


def check_references(doc: ThesisDoc) -> List[Finding]:
    out: List[Finding] = [Finding("references", "pass" if doc.references else "warn",
                                  f"{len(doc.references)} reference entries parsed")]
    if not doc.references:
        return out
    ref_raw_set = {r.raw for r in doc.references}
    body_text = "\n".join(line for line in doc.full_text.splitlines() if line.strip() not in ref_raw_set)
    cited: set = set()
    for m in _CITATION_NUMS.finditer(body_text):
        for tok in m.group(1).split(","):
            tok = tok.strip()
            if tok.isdigit():
                cited.add(int(tok))
    if not cited:
        out.append(Finding("references", "warn",
                           "no numbered in-text citations ([n]) detected - citation cross-check skipped "
                           "(a non-numbered citation style cannot be checked this way)", conf=0.3))
        return out
    ref_indices = {r.index for r in doc.references if r.index is not None}
    never_cited = sorted(ref_indices - cited)
    dangling = sorted(cited - ref_indices)
    if never_cited:
        out.append(Finding("references", "warn", f"reference(s) never cited in text: {never_cited}", conf=0.7))
    if dangling:
        out.append(Finding("references", "fail", f"in-text citation(s) with no matching reference entry: {dangling}"))
    if not never_cited and not dangling:
        out.append(Finding("references", "pass", "every reference is cited and every citation has a reference"))
    return out


# ---- 7. page numbering ------------------------------------------------------------------------------------
def check_page_numbering(doc: ThesisDoc, tmpl: Template) -> List[Finding]:
    if doc.has_page_numbers is None:
        return [Finding("page_numbering", "warn",
                        "page-numbering presence cannot be verified directly from a .docx file (no fixed "
                        "pagination before printing/export) - re-check the PDF/printed export instead")]
    if doc.has_page_numbers:
        return [Finding("page_numbering", "pass", "page numbers detected on most pages")]
    sev = "fail" if tmpl.page_numbering_required else "warn"
    return [Finding("page_numbering", sev, "no page numbers detected")]


# ---- 8. DOCX style compliance -------------------------------------------------------------------------------
def check_style_compliance(doc: ThesisDoc, tmpl: Template) -> List[Finding]:
    out: List[Finding] = []
    if doc.page_style is None or doc.page_style.source != "docx":
        out.append(Finding("style_compliance", "warn",
                           "style compliance (margins/font/spacing) can only be measured directly from a "
                           ".docx file; this document was not one"))
        return out
    rules = tmpl.page_style_rules
    ps = doc.page_style

    if ps.margins_cm and rules.get("margins_cm"):
        tol = rules.get("margins_tolerance_cm", 0.3)
        for side, expected in rules["margins_cm"].items():
            measured = ps.margins_cm.get(side)
            if measured is None:
                continue
            if abs(measured - expected) <= tol:
                out.append(Finding("style_compliance", "pass", f"{side} margin {measured}cm matches template ({expected}cm)"))
            else:
                out.append(Finding("style_compliance", "fail",
                                   f"{side} margin is {measured}cm, template expects {expected}cm (+/-{tol}cm)"))

    if rules.get("font_name") and ps.font_name:
        if ps.font_name.strip().lower() == str(rules["font_name"]).strip().lower():
            out.append(Finding("style_compliance", "pass", f"font matches template: {ps.font_name}"))
        else:
            out.append(Finding("style_compliance", "fail",
                               f"font is '{ps.font_name}', template expects '{rules['font_name']}'"))

    if rules.get("font_size_pt") and ps.font_size_pt:
        if abs(ps.font_size_pt - float(rules["font_size_pt"])) <= 0.5:
            out.append(Finding("style_compliance", "pass", f"font size {ps.font_size_pt}pt matches template"))
        else:
            out.append(Finding("style_compliance", "fail",
                               f"font size is {ps.font_size_pt}pt, template expects {rules['font_size_pt']}pt"))

    if rules.get("line_spacing") and ps.line_spacing:
        if abs(ps.line_spacing - float(rules["line_spacing"])) <= 0.1:
            out.append(Finding("style_compliance", "pass", f"line spacing {ps.line_spacing} matches template"))
        else:
            out.append(Finding("style_compliance", "fail",
                               f"line spacing is {ps.line_spacing}, template expects {rules['line_spacing']}"))

    if rules.get("justification") and ps.justification:
        if ps.justification == rules["justification"]:
            out.append(Finding("style_compliance", "pass", f"justification matches template: {ps.justification}"))
        else:
            out.append(Finding("style_compliance", "fail",
                               f"justification is '{ps.justification}', template expects '{rules['justification']}'"))
    return out


# ---- 9. duplicate headings --------------------------------------------------------------------------------
def check_duplicate_headings(doc: ThesisDoc) -> List[Finding]:
    seen: Dict[str, int] = {}
    for s in doc.sections:
        key = _norm_heading(s.title)
        seen[key] = seen.get(key, 0) + 1
    dups = {k: c for k, c in seen.items() if c > 1}
    if not dups:
        return [Finding("duplicate_headings", "pass", "no duplicate heading titles")]
    return [Finding("duplicate_headings", "fail", f"heading title used {c} times: '{k}'")
            for k, c in dups.items()]


CHECK_IDS = ("required_sections", "heading_hierarchy", "abstracts", "toc", "figures_tables",
            "references", "page_numbering", "style_compliance", "duplicate_headings")


def run_checks(doc: ThesisDoc, tmpl: Template) -> CheckReport:
    findings: List[Finding] = []
    findings += check_required_sections(doc, tmpl)
    findings += check_heading_hierarchy(doc)
    findings += check_abstracts(doc, tmpl)
    findings += check_toc(doc)
    findings += check_figures_tables(doc)
    findings += check_references(doc)
    findings += check_page_numbering(doc, tmpl)
    findings += check_style_compliance(doc, tmpl)
    findings += check_duplicate_headings(doc)
    return CheckReport(template_name=tmpl.name, findings=findings)
