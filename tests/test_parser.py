from pathlib import Path
import unittest

from normalizer import apply_verified_share_capital, normalize_record
from parser import decode_html, deduplicate_candidates, next_page_url, parse_company_detail, parse_discovery_page, parse_share_capital


FIXTURES = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_discovery_parses_relative_detail_urls_and_pagination(self):
        html = (FIXTURES / "discovery.html").read_text(encoding="utf-8")
        candidates = parse_discovery_page(html, "https://www.tunisieindustrie.nat.tn/en/dbi.asp")
        self.assertEqual([candidate.source_id for candidate in candidates], ["17", "17", "26"])
        self.assertEqual(candidates[0].detail_url, "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=result&ident=17")
        self.assertEqual(next_page_url(html, "https://www.tunisieindustrie.nat.tn/en/dbi.asp"), "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=search&pagenum=2")

    def test_duplicate_identity_uses_source_id(self):
        html = (FIXTURES / "discovery.html").read_text(encoding="utf-8")
        candidates = parse_discovery_page(html, "https://www.tunisieindustrie.nat.tn/en/dbi.asp")
        unique = deduplicate_candidates(candidates)
        self.assertEqual([candidate.source_id for candidate in unique], ["17", "26"])

    def test_detail_parses_unicode_links_and_missing_optional_fields(self):
        html = (FIXTURES / "detail.html").read_text(encoding="utf-8")
        record = parse_company_detail(html, "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=result&ident=17")
        record = normalize_record(record)
        self.assertEqual(record.short_name, "SOCIÉTÉ ÉTOILE")
        self.assertEqual(record.products, "Huile d’olive")
        self.assertEqual(record.phone, "+216 71 000 111 / 72 000 222")
        self.assertIsNone(record.fax)
        self.assertEqual(record.email, "contact@example.tn")
        self.assertEqual(record.url, "https://www.tunisieindustrie.nat.tn/company-site")
        self.assertEqual(record.share_capital_dt, "1?500?000")

    def test_legacy_encoding_is_not_assumed_utf8(self):
        content = '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"><p>Société</p>'.encode("iso-8859-1")
        self.assertEqual(decode_html(content), '<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"><p>Société</p>')

    def test_actual_source_share_capital_separator_is_preserved(self):
        english_bytes = (FIXTURES / "share_capital_english.html").read_bytes()
        french_template = (FIXTURES / "share_capital_french.html").read_text(encoding="utf-8")
        french_bytes = french_template.encode("iso-8859-1")
        english_html = decode_html(english_bytes, "text/html")
        french_html = decode_html(french_bytes, "text/html")
        english_record = parse_company_detail(
            english_html,
            "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=result&ident=17",
            "17",
        )
        french_value = parse_share_capital(
            french_html,
            "https://www.tunisieindustrie.nat.tn/fr/dbi.asp?action=result&ident=17",
        )
        corrected = normalize_record(apply_verified_share_capital(english_record, french_value))
        self.assertEqual(english_record.share_capital_dt, "1?500?000")
        self.assertEqual(french_value, "1\xa0500\xa0000")
        self.assertEqual(corrected.share_capital_dt, "1\xa0500\xa0000")
        self.assertEqual([f"U+{ord(c):04X}" for c in corrected.share_capital_dt], ["U+0031", "U+00A0", "U+0035", "U+0030", "U+0030", "U+00A0", "U+0030", "U+0030", "U+0030"])


if __name__ == "__main__":
    unittest.main()
