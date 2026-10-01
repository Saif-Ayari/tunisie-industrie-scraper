# Tunisie Industrie scraper — Phase 1

This repository contains a small, bounded scraper for the public Tunisie Industrie industrial-enterprise directory. Phase 1 collects a reviewable sample of real source records and writes a raw Excel workbook. It does not integrate with SIMPLE CRM, call any CRM API, enrich records, or perform a full-directory crawl.

## Source discovery

The inspected starting page is <https://www.tunisieindustrie.nat.tn/en/home.asp>.

The public navigation reaches the directory through **Online Services → Directory of industrial enterprises**. The menu link `link.asp?mcat=4&mrub=193` redirects to `/en/dbi.asp`.

The directory flow observed on 1 October 2026 was:

1. `GET /en/dbi.asp` returns a server-rendered search form.
2. The form uses `POST /en/dbi.asp` with hidden `action=search`. The inspected fields include `secteur`, `branche`, `produit`, `Denomination`, `Gouvernorat`, `delegation`, `pays`, `regime`, `cap1`, `cap2`, and `emp1`/`emp2`.
3. A search must include a criterion. Phase 1 uses the observed sector code `05` (food-product manufacturing) by default, which returned 980 records over 33 pages during inspection.
4. Result pages are server-rendered, show 30 rows, and paginate with `dbi.asp?action=search&pagenum=N` while retaining search state in the session.
5. Each result row contains a JavaScript navigation target such as `dbi.asp?action=result&ident=17`. The scraper extracts that stable `ident` and resolves the detail URL safely.
6. Detail pages are server-rendered HTML tables. Ordinary HTTP is sufficient; browser automation is not required.

`robots.txt` is publicly accessible and currently contains an empty `Disallow` value for `User-agent: *`, plus a sitemap reference. The server sets an `ASPSESSIONID` cookie; the scraper keeps one `requests.Session` for the search and pagination flow. No authentication or CAPTCHA was encountered.

The server currently omits a charset from the HTTP `Content-Type` header. Observed page bytes use the older ISO-8859-1-compatible encoding (`Fran\xe7ais` was present in the response), so the parser detects explicit header/meta declarations and otherwise uses an ISO-8859-1 fallback rather than assuming UTF-8.

## Share Capital data-quality correction

The English detail pages for several records contain literal ASCII question-mark bytes (`0x3F`, decoded as `U+003F`) between digit groups, for example `1?500?000`. This is already present in the HTTP body; it is not introduced by `requests`, ISO-8859-1 decoding, BeautifulSoup, normalization, or Excel.

The same public records in the site's French detail pages contain byte `0xA0`, which ISO-8859-1 decodes as `U+00A0` (non-breaking space), for example `1\xa0500\xa0000`. When an English Share Capital value contains a literal `?`, the scraper fetches that same record's French representation and accepts its `Capital en DT` value only when it contains digits and no question mark. This is same-site corroboration, not external enrichment or a replacement table. The NBSP is preserved through parsing, normalization, and Excel export.

## Current source fields

The inspected detail cards expose these source fields, which are exported with stable column names:

- Short Name
- Company Name
- Manager
- Activities
- Products
- Factory's Address
- Gouvernorate
- Delegation
- Phone Number Head Office/Factory
- Fax Number Head Office/Factory
- E-mail
- URL
- Market
- Foreign Participant Country
- Created
- Share Capital DT
- Employees

The scraper-generated provenance fields are `Source ID`, `Source URL`, and `Scraped At`. `Scraped At` is an ISO-8601 UTC timestamp ending in `Z`. Empty source values remain empty; they are not inferred or enriched.

## Setup on Windows / Python 3.12

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run

The safe default is 10 records and the Phase 1 CLI refuses limits above 100:

```powershell
python scraper.py --limit 10
```

At the time of inspection, the website's TLS certificate was expired. Until the site renews it, the controlled public smoke test must explicitly use:

```powershell
python scraper.py --limit 10 --insecure-tls
```

The flag is intentionally opt-in and should be removed after certificate renewal. The scraper remains sequential, uses a one-second default delay, bounded retries, ordinary identifying User-Agent text, and a 30-second timeout.

The workbook is written to `output/tunisie_industrie_raw.xlsx` by default. Use `--output` to select another path. `--sector` changes the source directory criterion; it does not turn Phase 1 into an unrestricted crawl.

## Tests

Tests use small local HTML fixtures and do not contact the live site:

```powershell
python -m unittest discover -s tests -v
```

They cover discovery links and pagination, source-ID deduplication, detail parsing, missing optional fields, Unicode and legacy encoding, relative URL handling, workbook layout, text preservation, and formula-injection protection.

## Output behavior and limitations

The workbook has one `Companies` worksheet, a frozen first row, an autofilter, deterministic columns, readable widths, and strings for phones, postal-code text embedded in addresses, capital values, employees, and source identifiers. Values beginning with `=`, `+`, `-`, or `@` receive a spreadsheet-safe leading apostrophe so untrusted source text cannot become a formula.

Phase 1 intentionally stops after the requested small sample. It does not implement full-directory checkpointing, incremental runs, raw snapshots, or CRM-specific transformation. Before scaling, the search criteria and pagination behavior should be revalidated, the TLS certificate should be renewed, and a checkpoint/resume strategy should be added.

Generated XLSX files are ignored by Git; `output/.gitkeep` keeps the directory available without committing company data.
