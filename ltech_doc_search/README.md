# ltech_doc_search (Odoo 20)

Jedno miejsce wyszukiwania pełnotekstowego w dokumentach projektów
(załączniki `project.project` i `project.task`). Tekst wyciąga Apache Tika,
indeksuje Apache Solr, a GUI to OWL client action z podświetlaniem.

## Uruchomienie

1. `cd deploy && docker compose up -d`
2. `./solr_schema.sh` (albo `SOLR=http://host:8983/solr/odoo_docs ./solr_schema.sh`)
3. Jeśli Solr/Tika nie są pod `solr` / `tika`, ustaw parametry systemowe
   `doc_search.solr_url` i `doc_search.tika_url` (Ustawienia → Techniczne → Parametry
   systemowe albo `env["ir.config_parameter"].set_str(...)`). Moduł nie tworzy ich sam:
   bez nich używa domyślnych adresów z dockera.
4. Zainstaluj moduł. Istniejące załączniki dostają stan `pending` i indeksuje je
   cron (domyślnie 100 sztuk co 2 minuty). Pierwsze indeksowanie można przyspieszyć z shella:
   `env['ir.attachment']._cron_solr_index(limit=5000); env.cr.commit()`
5. Pełny reindeks: `env['ir.attachment']._solr_reindex_all()`

## Bezpieczeństwo

- Zapytanie do Solr jest filtrowane do projektów, które użytkownik może czytać
  (`project.project.search([])`), więc facety i paginacja się zgadzają.
- Wyniki są dodatkowo sprawdzane przez ORM (`ir.attachment.search`), co uwzględnia
  record rules dla zadań.
- `hl.encoder=html` escapuje treść dokumentów, więc snippety można bezpiecznie renderować.

## Dodawanie dokumentów (1.1)

- Każdy nowy załącznik projektu/zadania od razu odpala cron indeksujący (`ir.cron._trigger()`),
  więc dokument jest w wyszukiwarce po kilku sekundach, bez blokowania uploadu.
- Przycisk „Dodaj dokumenty” w wyszukiwarce: wybór projektu (i opcjonalnie zadania),
  przeciągnij i upuść lub wybór z dysku, pasek postępu na plik, status indeksowania
  i automatyczne odświeżenie wyników. Plik trafia też do chattera rekordu.
- Endpointy: `POST /doc_search/upload` (http, CSRF), `/doc_search/index_status` (json).

## Odoo 20 — zmiany względem wersji 17

- Wymagania Odoo 20: Python 3.12+, PostgreSQL 16+.
- `type="json"` → `type="jsonrpc"` w trasach, `check_access_rights/_rule` → `check_access`,
  `_()` → `request.env._()`.
- `ir.config_parameter`: `get_param` → `get_str`; usunięty plik danych z domyślnymi parametrami.
- `ir.cron`: usunięte pole `numbercall`.
- `att.raw` może zwracać `BinaryValue` → do Tiki idzie `.content` (bajty).
- JS: serwis `rpc` → `import { rpc } from "@web/core/network/rpc"`; w szablonie `t-esc` → `t-out`.
