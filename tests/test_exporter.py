from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from exporter import HEADERS, export_records, verify_workbook
from models import CompanyRecord


class ExporterTests(unittest.TestCase):
    def test_workbook_layout_text_preservation_and_formula_protection(self):
        record = CompanyRecord(
            short_name="=HYPERLINK(\"http://bad.example\")",
            company_name="Société Étoile",
            phone="+216 71 000 111",
            share_capital_dt="1\xa0500\xa0000",
            source_id="00017",
            source_url="https://www.tunisieindustrie.nat.tn/en/dbi.asp?action=result&ident=00017",
            scraped_at="2026-10-01T13:00:00Z",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.xlsx"
            export_records([record], path)
            summary = verify_workbook(path, 1)
            self.assertEqual(summary["worksheet"], "Companies")
            self.assertEqual(summary["rows"], 1)
            workbook = load_workbook(path, data_only=False)
            worksheet = workbook["Companies"]
            self.assertEqual(tuple(cell.value for cell in worksheet[1]), HEADERS)
            self.assertEqual(worksheet.freeze_panes, "A2")
            self.assertTrue(worksheet.auto_filter.ref)
            self.assertEqual(worksheet["A2"].value, "'=HYPERLINK(\"http://bad.example\")")
            self.assertEqual(worksheet["I2"].value, "'+216 71 000 111")
            self.assertEqual(worksheet["R2"].value, "00017")
            self.assertEqual(worksheet["R2"].data_type, "s")
            self.assertEqual(worksheet["P2"].value, "1\xa0500\xa0000")
            self.assertEqual(worksheet["P2"].data_type, "s")
            self.assertEqual(worksheet["B2"].value, "Société Étoile")


if __name__ == "__main__":
    unittest.main()
