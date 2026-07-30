from atmos_gl.lib.text_sanitize import strip_html


def test_strips_a_single_tag_and_collapses_the_leading_space():
    assert strip_html("<p>Hello</p>") == "Hello"


def test_replaces_a_tag_with_a_space_so_adjacent_words_dont_run_together():
    assert strip_html("<b>Real</b>Name") == "Real Name"


def test_collapses_internal_whitespace_and_newlines():
    assert strip_html("\n<p>Line one.</p>\n<p>Line two.</p>\n") == "Line one. Line two."


def test_a_plain_string_with_no_tags_is_unchanged():
    assert strip_html("Colombia") == "Colombia"


def test_none_passes_through_unchanged():
    assert strip_html(None) is None


def test_empty_string_passes_through_unchanged():
    assert strip_html("") == ""


def test_a_string_that_is_only_tags_collapses_to_none():
    assert strip_html("<p></p>") is None
    assert strip_html("   <b>  </b>   ") is None


def test_does_not_remove_the_text_content_of_a_stripped_tag_pair():
    # strip_html only removes tag SYNTAX, not the text between tags -- it is not a
    # full sanitizer. The actual XSS-blocking control is escaping at the frontend
    # render sink (ui/modules/_feedhelpers.js's escapeHtml()); this is a secondary
    # hygiene layer, not a substitute for it.
    assert strip_html("<script>alert(1)</script>") == "alert(1)"
