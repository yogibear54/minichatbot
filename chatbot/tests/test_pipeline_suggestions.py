import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.pipeline import parse_suggestions


def test_parses_json_array():
    assert parse_suggestions('["How to export?", "Pricing plans?"]') == \
        ["How to export?", "Pricing plans?"]


def test_parses_fenced_array():
    out = parse_suggestions('```json\n["Q one?", "Q two?"]\n```')
    assert out == ["Q one?", "Q two?"]


def test_parses_lines_fallback():
    out = parse_suggestions("- What is X?\n2. How does Y work?\njunk sentence that is very long and should be dropped")
    assert out == ["What is X?", "How does Y work?"]


def test_limits_and_drops_empty():
    # single-character items are junk, not questions
    assert parse_suggestions('["a", "", "b", "c", "d"]') == []
    assert parse_suggestions('["Pricing?", "Contact?"]') == ["Pricing?", "Contact?"]


def test_filters_prompt_echo_junk():
    junk = '["Analyze the Request:**", "Constraint 1: Max 8 words per question.", "What services do you offer?"]'
    out = parse_suggestions(junk)
    assert out == ["What services do you offer?"]


def test_strips_word_count_suffix():
    out = parse_suggestions('["What experience does Chris Li have? (6 words)"]')
    assert out == ["What experience does Chris Li have?"]


def test_cleans_explanations_and_grades():
    out = parse_suggestions(
        '["What is X? - \\"What is X?\\" (5 words) ✓", "How much? (3 words) ✓"]')
    assert out == ["What is X?", "How much?"]


def test_filters_meta_preamble():
    out = parse_suggestions('["Let me craft 3 questions:", "How do you handle inventory?", "Example format: \\"Q?\\""]')
    assert out == ["How do you handle inventory?"]


def test_drops_non_question_filler():
    out = parse_suggestions('["Fractional CTO services", "Technical partnership", "What is this page about?"]')
    assert out == ["What is this page about?"]
