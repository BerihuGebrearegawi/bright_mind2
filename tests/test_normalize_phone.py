"""Exhaustive coverage of app.py's _normalize_phone() - flagged in the
handoff notes as an edge-case gap ("not all accepted phone-number shapes
individually tested"). This is a pure function (no Firestore/network), so
it's tested directly rather than through an HTTP route.

Ethiopian mobile numbers are 9 digits starting with 7 or 9 (e.g.
912345678, 712345678). _normalize_phone accepts that local number
prefixed any of: nothing, a leading 0, 251, or +251 - optionally with
spaces/dashes/parentheses anywhere - and always returns E.164 (+251...).
Anything that doesn't reduce to a 9-digit 7xxxxxxxx/9xxxxxxxx after
prefix-stripping returns None.
"""
import pytest


@pytest.fixture
def normalize_phone(app_module):
    return app_module._normalize_phone


class TestAcceptedFormats:
    """Every prefix style the function claims to support, for both the
    7-series and 9-series local prefixes."""

    @pytest.mark.parametrize("raw", [
        "912345678",        # bare local, 9-series
        "0912345678",       # leading 0, 9-series
        "251912345678",     # country code, no plus
        "+251912345678",    # full E.164 already
    ])
    def test_nine_series_local_number(self, normalize_phone, raw):
        assert normalize_phone(raw) == "+251912345678"

    @pytest.mark.parametrize("raw", [
        "712345678",
        "0712345678",
        "251712345678",
        "+251712345678",
    ])
    def test_seven_series_local_number(self, normalize_phone, raw):
        assert normalize_phone(raw) == "+251712345678"

    @pytest.mark.parametrize("raw,expected", [
        ("+251 91 234 5678", "+251912345678"),
        ("+251-91-234-5678", "+251912345678"),
        ("(+251) 912345678", "+251912345678"),
        ("0912 345 678", "+251912345678"),
        ("  0912345678  ", "+251912345678"),
        ("091-234-5678", "+251912345678"),
    ])
    def test_punctuation_and_whitespace_are_stripped(self, normalize_phone, raw, expected):
        assert normalize_phone(raw) == expected


class TestRejectedFormats:
    @pytest.mark.parametrize("raw", [
        None,
        "",
        "   ",
        "12345",                # too short, no valid prefix reduction
        "091234567",            # one digit short after stripping leading 0
        "09123456789",          # one digit too long
        "0812345678",           # local prefix 8 - not 7 or 9
        "0012345678",           # local prefix 0 (after stripping leading 0) - invalid
        "abcdefghij",           # non-numeric
        "+1912345678",          # wrong country code
        "+25191234567",         # country code but local part too short
        "+2519123456789",       # country code but local part too long
        "251812345678",         # country code, invalid local prefix (8)
    ])
    def test_invalid_shapes_return_none(self, normalize_phone, raw):
        assert normalize_phone(raw) is None

    def test_non_string_input_does_not_raise(self, normalize_phone):
        # register/login routes pass whatever JSON gave them; body.get('phone')
        # could in principle be a non-string JSON type. The function must not
        # raise even though it clearly won't reduce to a valid local number.
        assert normalize_phone({"not": "a phone"}) is None
        assert normalize_phone(["0912345678"]) is None

    def test_int_that_happens_to_look_like_a_valid_local_number(self, normalize_phone):
        # A bare int is stringified before matching, so a 9-digit int that
        # matches the local-number shape is still accepted - documenting
        # this (rather than assuming it's rejected) avoids a false negative.
        assert normalize_phone(912345678) == "+251912345678"
