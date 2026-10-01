from pathlib import Path
import tempfile
import unittest

from exporter import export_records
from models import CompanyCandidate, CompanyRecord
from parser import parse_result_page, parse_search_form
from state import CheckpointStore


FIXTURES = Path(__file__).parent / "fixtures"


class Phase2Tests(unittest.TestCase):
    def test_search_form_discovers_nonsequential_scopes_without_inventing_all(self):
        html = (FIXTURES / "search_form.html").read_text(encoding="utf-8")
        form = parse_search_form(html, "https://www.tunisieindustrie.nat.tn/en/dbi.asp")
        self.assertEqual(form.method, "POST")
        self.assertEqual(form.hidden_fields["action"], "search")
        self.assertEqual([scope.code for scope in form.scopes], ["05", "01", "09"])
        self.assertFalse(form.has_all_scope)
        self.assertIn("branche", form.controls)
        self.assertIn("produit", form.controls)

    def test_result_pages_expose_count_pagination_and_candidates(self):
        first = parse_result_page(
            (FIXTURES / "result_page_1.html").read_text(encoding="utf-8"),
            "https://www.tunisieindustrie.nat.tn/en/dbi.asp",
        )
        second = parse_result_page(
            (FIXTURES / "result_page_2.html").read_text(encoding="utf-8"),
            "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=search&pagenum=2",
        )
        self.assertEqual((first.page_number, first.total_pages, first.result_count, first.page_size), (1, 2, 2, 2))
        self.assertEqual(first.next_url, "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=search&pagenum=2")
        self.assertEqual(second.page_number, 2)
        self.assertIsNone(second.next_url)
        self.assertEqual([candidate.source_id for candidate in first.candidates], ["10", "2"])

    def test_checkpoint_deduplicates_persists_records_and_keeps_failures_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            store = CheckpointStore(state)
            first = CompanyCandidate("10", "https://example.test/10")
            duplicate = CompanyCandidate("10", "https://example.test/10-other")
            failed = CompanyCandidate("2", "https://example.test/2")
            self.assertTrue(store.add_candidate(first, "05"))
            self.assertFalse(store.add_candidate(duplicate, "01"))
            self.assertTrue(store.add_candidate(failed, "01"))
            self.assertEqual(store.candidate_keys(), ["2", "10"])
            record = CompanyRecord(source_id="10", source_url=first.detail_url, scraped_at="2026-10-01T00:00:00Z")
            self.assertTrue(store.save_record(record))
            store.record_failure(failed, "timeout")
            resumed = CheckpointStore(state, resume=True)
            self.assertTrue(resumed.has_record("10"))
            self.assertEqual(resumed.candidate("10").scopes, ("01", "05"))
            self.assertIn("2", resumed.failures)
            self.assertEqual(resumed.failures["2"]["attempts"], 1)
            self.assertTrue(resumed.save_record(CompanyRecord(source_id="2", source_url=failed.detail_url)))
            self.assertNotIn("2", resumed.failures)

    def test_export_rejects_duplicate_source_ids(self):
        record_a = CompanyRecord(source_id="10", source_url="https://example.test/10")
        record_b = CompanyRecord(source_id="10", source_url="https://example.test/10-again")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                export_records([record_a, record_b], Path(directory) / "duplicate.xlsx")

    def test_detail_with_known_source_id_can_preserve_blank_source_names(self):
        from parser import parse_company_detail

        html = """
        <table>
          <tr><td><b>Short Name</b></td><td></td></tr>
          <tr><td><b>Company name</b></td><td></td></tr>
          <tr><td><b>Factory's address</b></td><td>RTE DE AIN TEBOURNOK - 8030 - GROMBALIA</td></tr>
        </table>
        """
        record = parse_company_detail(
            html,
            "https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=result&ident=1",
            "1",
        )
        self.assertEqual(record.source_id, "1")
        self.assertIsNone(record.short_name)
        self.assertIsNone(record.company_name)
        self.assertEqual(record.factory_address, "RTE DE AIN TEBOURNOK - 8030 - GROMBALIA")


if __name__ == "__main__":
    unittest.main()
