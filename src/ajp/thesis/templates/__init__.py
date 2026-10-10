"""YAML template loader for T16's structural checks. A template is data, not code - adding a faculty-specific
one later is a new .yaml file here, never a code change to checks.py."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ajp.thesis.sr_script import to_latin_for_matching

TEMPLATES_DIR = Path(__file__).parent
REQUIRED_TOP_KEYS = ("name", "label", "required_sections", "abstract_languages", "heading_numbering",
                    "page_style_rules", "page_numbering_required")


class TemplateError(ValueError):
    pass


@dataclass
class RequiredSection:
    key: str
    match: List[str]
    optional: bool = False


@dataclass
class Template:
    name: str
    label: str
    generic: bool
    required_sections: List[RequiredSection]
    abstract_languages: List[str]
    heading_numbering: Dict[str, Any]
    page_style_rules: Dict[str, Any]
    page_numbering_required: bool
    citation_style_hint: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def match_section_key(self, title: str) -> Optional[str]:
        """The template's canonical section key a heading title matches, if any (case-insensitive substring,
        script-agnostic - a Cyrillic-written heading still matches this (Latin-written) template's keywords,
        via sr_script.to_latin_for_matching; found live building the Cyrillic fixture that it otherwise
        wouldn't match at all)."""
        low = to_latin_for_matching(title.strip()).lower()
        for rs in self.required_sections:
            if any(m.lower() in low or low in m.lower() for m in rs.match):
                return rs.key
        return None


def validate_template_schema(raw: Dict[str, Any]) -> List[str]:
    """Returns a list of problems (empty = valid). Never raises - callers decide what to do with issues."""
    problems = []
    for key in REQUIRED_TOP_KEYS:
        if key not in raw:
            problems.append(f"missing top-level key: {key}")
    for i, rs in enumerate(raw.get("required_sections", [])):
        if "key" not in rs or "match" not in rs:
            problems.append(f"required_sections[{i}] missing 'key' or 'match'")
        elif not isinstance(rs["match"], list) or not rs["match"]:
            problems.append(f"required_sections[{i}] ('{rs.get('key')}') 'match' must be a non-empty list")
    if not isinstance(raw.get("abstract_languages"), list):
        problems.append("abstract_languages must be a list")
    return problems


def load_template(name: str = "rs_phd_generic", directory: Path = TEMPLATES_DIR) -> Template:
    path = directory / f"{name}.yaml"
    if not path.is_file():
        raise TemplateError(f"no such template: {name!r} ({path})")
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    problems = validate_template_schema(raw)
    if problems:
        raise TemplateError(f"template {name!r} failed validation: {'; '.join(problems)}")
    sections = [RequiredSection(key=rs["key"], match=rs["match"], optional=bool(rs.get("optional", False)))
                for rs in raw["required_sections"]]
    return Template(
        name=raw["name"], label=raw["label"], generic=bool(raw.get("generic", False)),
        required_sections=sections, abstract_languages=list(raw["abstract_languages"]),
        heading_numbering=raw["heading_numbering"], page_style_rules=raw["page_style_rules"],
        page_numbering_required=bool(raw["page_numbering_required"]),
        citation_style_hint=raw.get("citation_style_hint"), raw=raw,
    )


def list_templates(directory: Path = TEMPLATES_DIR) -> List[str]:
    return sorted(p.stem for p in directory.glob("*.yaml"))
