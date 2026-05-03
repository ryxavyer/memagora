"""Smoke tests for i18n dictionaries."""

from mempalace.i18n import load_lang, t, available_languages


def test_all_languages_load():
    """Every JSON file loads without error and has required keys."""
    required_sections = ["terms", "cli"]
    required_terms = ["palace", "wing", "closet", "drawer"]

    langs = available_languages()
    assert len(langs) >= 7, f"Expected 7+ languages, got {len(langs)}"

    for lang in langs:
        strings = load_lang(lang)
        for section in required_sections:
            assert section in strings, f"{lang}: missing section '{section}'"
        for term in required_terms:
            assert term in strings["terms"], f"{lang}: missing term '{term}'"
            assert len(strings["terms"][term]) > 0, f"{lang}: empty term '{term}'"

    print(f"  PASS: {len(langs)} languages load correctly")


def test_interpolation():
    """String interpolation works for all languages."""
    for lang in available_languages():
        load_lang(lang)
        result = t("cli.mine_complete", closets=5, drawers=100)
        assert "5" in result, f"{lang}: closets count missing from mine_complete"
        assert "100" in result, f"{lang}: drawers count missing from mine_complete"

    print("  PASS: interpolation works for all languages")


def test_korean_status_drawers_uses_count():
    """ko.json status_drawers must use {count}, not {drawers}."""
    load_lang("ko")
    result = t("cli.status_drawers", count=42)
    assert "42" in result, f"Expected '42' in '{result}' -- count variable not interpolated"


def test_de_entity_section_loads():
    """German entity section loads all pattern lists non-empty."""
    from mempalace.i18n import get_entity_patterns

    p = get_entity_patterns(("de",))
    assert p["candidate_patterns"], "de: empty candidate_patterns"
    assert p["multi_word_patterns"], "de: empty multi_word_patterns"
    assert p["person_verb_patterns"], "de: empty person_verb_patterns"
    assert p["pronoun_patterns"], "de: empty pronoun_patterns"
    assert p["dialogue_patterns"], "de: empty dialogue_patterns"
    assert p["direct_address_patterns"], "de: empty direct_address_patterns"
    assert p["project_verb_patterns"], "de: empty project_verb_patterns"
    assert len(p["stopwords"]) > 50, f"de: stopwords too short ({len(p['stopwords'])})"


def test_es_entity_section_loads():
    """Spanish entity section loads all pattern lists non-empty."""
    from mempalace.i18n import get_entity_patterns

    p = get_entity_patterns(("es",))
    assert p["candidate_patterns"], "es: empty candidate_patterns"
    assert p["multi_word_patterns"], "es: empty multi_word_patterns"
    assert p["person_verb_patterns"], "es: empty person_verb_patterns"
    assert p["pronoun_patterns"], "es: empty pronoun_patterns"
    assert p["dialogue_patterns"], "es: empty dialogue_patterns"
    assert p["direct_address_patterns"], "es: empty direct_address_patterns"
    assert p["project_verb_patterns"], "es: empty project_verb_patterns"
    assert len(p["stopwords"]) > 50, f"es: stopwords too short ({len(p['stopwords'])})"


def test_fr_entity_section_loads():
    """French entity section loads all pattern lists non-empty."""
    from mempalace.i18n import get_entity_patterns

    p = get_entity_patterns(("fr",))
    assert p["candidate_patterns"], "fr: empty candidate_patterns"
    assert p["multi_word_patterns"], "fr: empty multi_word_patterns"
    assert p["person_verb_patterns"], "fr: empty person_verb_patterns"
    assert p["pronoun_patterns"], "fr: empty pronoun_patterns"
    assert p["dialogue_patterns"], "fr: empty dialogue_patterns"
    assert p["direct_address_patterns"], "fr: empty direct_address_patterns"
    assert p["project_verb_patterns"], "fr: empty project_verb_patterns"
    assert len(p["stopwords"]) > 50, f"fr: stopwords too short ({len(p['stopwords'])})"


def test_direct_address_key_is_singular_string_for_all_locales():
    """Schema invariant: any locale declaring direct-address uses the singular
    ``direct_address_pattern`` (str), never the plural ``direct_address_patterns`` (list).

    The loader at ``mempalace/i18n/__init__.py:209-210`` only reads the singular key;
    the plural form is the output schema of the merged dict, not the input schema.
    Declaring the plural form in a locale file silently drops every direct-address
    pattern in that locale after load.
    """
    from mempalace.i18n import _load_entity_section, available_languages

    for lang in available_languages():
        section = _load_entity_section(lang)
        if not section:
            continue
        assert "direct_address_patterns" not in section, (
            f"{lang}: declares plural 'direct_address_patterns' (list); "
            f"loader only reads singular 'direct_address_pattern' (str). "
            f"Collapse the list into one `|`-alternation string and rename the key."
        )
        if "direct_address_pattern" in section:
            val = section["direct_address_pattern"]
            assert isinstance(
                val, str
            ), f"{lang}: 'direct_address_pattern' must be str, got {type(val).__name__}"
            assert val, f"{lang}: 'direct_address_pattern' is empty"
