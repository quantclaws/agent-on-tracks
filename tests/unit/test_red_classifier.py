"""classify_red pure function (FR-0050, IF-004 §1g RedClass).

The test data itself is the ground truth (TP-004 §3.1 row 3): each fixture's
returncode + stdout/stderr is the single source of truth for the expected class.
"""
from tracks.executor.executor import classify_red

# AC-FR0050-02@v0.4: legit Red classes


def test_legit_red_classes():
    """AC-FR0050-02@v0.4: assertion failure, stub token failure, symbol missing."""
    # stub_token_failure: NotImplementedError("IF-...")
    assert classify_red("t1", 1, "",
                        'raise NotImplementedError("IF-MTEST-001")') == "stub_token_failure"
    # assertion_failure: AssertionError / assert
    assert classify_red("t2", 1, "", "AssertionError: 1 != 2") == "assertion_failure"
    assert classify_red("t3", 1, "def test_x():\n    assert False\n", "") == "assertion_failure"
    # symbol_missing: AttributeError / NameError
    assert classify_red("t4", 1, "",
                        "AttributeError: 'NoneType' object has no attribute") == "symbol_missing"
    assert classify_red("t5", 1, "", "NameError: name 'foo' is not defined") == "symbol_missing"


# AC-FR0050-03@v0.4: illegit Red classes


def test_illegit_red_classes():
    """AC-FR0050-03@v0.4: collection/syntax/fixture/import errors."""
    assert classify_red("t1", 1, "", "ImportError: No module named 'foo'") == "collection_error"
    assert classify_red("t2", 1, "",
                       "ModuleNotFoundError: No module named 'bar'") == "collection_error"
    assert classify_red("t3", 1, "", "SyntaxError: invalid syntax") == "collection_error"
    assert classify_red("t4", 1, "", "FixtureLookupError: fixture not found") == "collection_error"
    assert classify_red("t5", 1, "ERROR collecting tests/test_x.py", "") == "collection_error"


# AC-FR0050-04@v0.4: unexpected pass


def test_unexpected_pass():
    """AC-FR0050-04@v0.4: returncode 0 (test should fail but passed) -> illegit."""
    assert classify_red("t1", 0, "1 passed in 0.01s", "") == "unexpected_pass"
    assert classify_red("t2", 0, "", "") == "unexpected_pass"


# AC-FR0050-05@v0.4: unclassified fallback


def test_unclassified():
    """AC-FR0050-05@v0.4: unclassifiable failure -> illegit, enters DIAGNOSE."""
    assert classify_red("t1", 1, "", "some weird error") == "unclassified"
    assert classify_red("t2", 2, "segfault", "") == "unclassified"
