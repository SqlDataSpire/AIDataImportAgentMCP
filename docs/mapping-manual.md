# Hand-mapping manual

How to complete an unknown file's signature by hand: write a mapping spec, register it, check it, bind it,
and land the waiting files. (With Claude, `/mcp__importer__onboard` does steps 2–5 for you; this manual is the
fallback and the reference for what Claude produces.)

## 1. Find what is waiting

    importer pending

Each entry shows the signature, a "looks like ..." candidate list, the suggested path (`reuse`, `exact`,
`fallback` or `model`), how many files are waiting, and the triggering file's locator.

- `reuse`: another project's identical template is registered. Use its mapper version as-is (step 4, skip 2–3).
- `exact` / `fallback`: an `"auto": true` spec onto the best schema is enough (exact), or is a lossless start
  (fallback: unmatched columns land in `_extras`).
- `model`: write the spec yourself (XML/JSON feeding several tables, or columns that need explicit mapping).

## 2. Write the spec

Start from the target schema's columns:

    importer config show                  # destinations
    importer schema import ... (if the target table is not registered yet)

Write `spec.json` using chapter 3. Rules: every `target` is a registered address (`database.schema.table`);
values land exactly as written; anything you cannot place goes to `_extras` (`"unmapped": "extras"`).
For nested XML, start from the tree: `importer pending` names the signature; the MCP `get_tree` tool (or the
profile in `get_file_profile`) shows the structure.

Example — the URL template variant whose link column is called `url`:

```json
{"kind": "tabular",
 "extractions": [{"target": "app_db.relational.url_template", "records": "*", "auto": true,
                  "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"}}]}
```

## 3. Spec reference

A mapping spec is JSON data that stable importer code runs. You never write code;
`"kind": "plugin"` is refused. Every `target` must be a registered schema address
(`database.schema.table`, see `search_schemas` / `get_schema`). Values land exactly
as written: never clean, trim, convert or guess. A file column you cannot place with
confidence stays unmapped and lands in `_extras` (nothing is lost).

### Tabular files (CSV/TSV/pipe, Excel, JSON, NDJSON): `"kind": "tabular"`

```json
{"kind": "tabular",
 "extractions": [
   {"target": "app_db.relational.url_template",
    "records": "*",
    "auto": true,
    "fields": {"newurl": "url", "file_id": "$file_id", "run_id": "$context.run_id"},
    "unmapped": "extras"}]}
```

- `records`: the Excel sheet name, or `"*"` (the only table of CSV/JSON/NDJSON).
- Column names are normalized: lowercase letters and digits only ("URL ID" -> `urlid`).
- `auto: true` maps every file column whose normalized name equals a schema column.
- `fields`: `{schema_column: source}`. The source is a normalized file column or a special source. `fields` overrides `auto`.
- `unmapped`: `"extras"` (default; leftover columns go to `_extras`) or `"ignore"`.
- `unpivot`: `{"over": "*" | ["col", ...], "name_column": "...", "value_column": "..."}` gives one row per listed column. The row identity gets the suffix `#<column>`.
- `shapers` (Excel layouts), applied before extraction:
  - `{"name": "excel.anchor_header", "options": {"sheet": "...", "anchor": "<normalized header cell>"}}` flattens multi-row headers starting at the anchor cell.
  - `{"name": "excel.crosstab_unpivot", "options": {"sheet": "...", "id_columns": [...]}}` melts a cross-tab into `benefit_name` / `value`.

### XML files: `"kind": "xml"`

```json
{"kind": "xml",
 "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
 "value_child": "x:cellValue",
 "extractions": [
   {"target": "app_db.xml.formulary_drug_list",
    "records": "/x:prescriptionDrugTemplateVO/x:drugList/x:drugListVO/x:drugVOList/x:drugVO",
    "fields": {"rxcui": "x:rxcui", "drug_list_id": "../../x:drugListID", "fileid": "$file_id"},
    "unmapped": "extras"}]}
```

- `namespaces`: prefixes for every XPath. `value_child` handles templates whose values sit in a child element (e.g. CMS `<cellValue>`).
- `records`: an absolute XPath. Each match becomes one row.
- `fields`: XPath relative to the record. `../` reaches ancestors, and absolute paths read header values.
  - A path matching nothing lands NULL.
  - A path matching several nodes lands the first value, and the full list goes to `_extras`.
- `{"*": "x:*"}` maps every child element to the column of the same name (unknown names go to `_extras`).
- `unpivot`: `{"over": "<XPath>", "name_column": "...", "value_column": "..."}`.
- Several extractions in one spec feed several tables. `$parent(<target>)` links a child row to its parent row's identity.

### Nested XML: `"kind": "tree"` (preferred for nested templates)

The spec mirrors the file's nesting tree (`get_tree`). The spec object is the root node, whose context is the
document root. Each node in `select` has a `forEach` (XPath relative to its parent node's element) and may have:

- `table`: a registered schema address; one row per matched element.
- `column`: `[{"name": ..., "path": ...}]`, where `path` is XPath relative to this node's element (attributes with
  `@name`) or a special source. **Columns are carried down** to every table below the node, so write header values
  and keys once, where they live. The nearer node wins on a name clash.
- `leaves: "auto"` (table nodes): every leaf child whose normalized name matches a schema column lands there.
  Leftover leaves go to `_extras`: a repeated leaf as a list, and a leaf whose name is already taken (by a carried
  column, for example) under `./<name>`, so nothing is lost.
- `unmapped` (`"extras"` default, or `"ignore"`), `unpivot` (as in `xml`), `document: "<column>"` (the whole element
  as XML text, to shred downstream).
- `select`: child nodes.

```json
{"kind": "tree",
 "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
 "value_child": "x:cellValue",
 "column": [{"name": "fileid", "path": "$file_id"},
            {"name": "templateVersion", "path": "x:header/x:templateVersion"}],
 "select": [
   {"forEach": "x:drugList/x:drugListVO",
    "column": [{"name": "drug_list_id", "path": "x:drugListID"}],
    "select": [
      {"forEach": "x:drugTierLevelList/x:drugTierLevelVO", "table": "app_db.xml.formulary_tier_definition",
       "column": [{"name": "tier_level", "path": "x:drugTierLevel"}, {"name": "tier_type", "path": "x:drugTierType"}]},
      {"forEach": "x:drugVOList/x:drugVO", "table": "app_db.xml.formulary_drug_list",
       "column": [{"name": "rxcui", "path": "x:rXCUI"}, {"name": "tier_level", "path": "x:tier"}]}]}]}
```

Rules:

- **Represent the file, not a report.** One table per record node. Sibling nodes never combine: the importer never
  joins (drugs × cost shares is a downstream join on the keys each table carries).
- **Specials** for `path`: `$file_id`, `$context.<key>`, `$identity` (this element's identity; carry it down to link
  children to their parent), and `$name` (this element's name, for elements whose name is the data).
- `$row_identity` is accepted as an alias of `$identity` on table nodes.
- `preview_mapping` returns `tree_warnings` (paths that cannot occur in the stored tree) and a count for every table,
  0 included. Fix the warnings before submitting.

### Special sources

`$file_id`, `$row_identity`, `$context.<key>` (requestor context such as `batch_id`, `run_id`), and `$parent(<target>)` (XML only).

### Onboarding rules

1. Prefer a draft from `get_file_profile` (`reuse` / `exact` / `fallback`) when it fits.
2. Mappings already bound to the schema (`get_prior_mappings`) are the strongest evidence.
3. Use `get_samples` only for columns whose names do not settle the mapping.
4. Always `preview_mapping` before `submit_mapping`, and give a rationale for every non-exact column.
5. Never call `accept_mapping` until the user explicitly approves the latest draft.
6. In rationale `evidence`, notes and reasons, describe values ("URLs", "5-digit codes"); never quote sample values. Those fields are kept in the audit log.
7. Row values are never shown to you. `get_samples` and preview sample rows are masked shapes: every letter is `a`, every digit `9`, other characters kept (`78079DC0320001-01` -> `99999aa9999999-99`), at most 3 rows x 20 fields x 40 characters. Reason from names, counts, shapes and prior mappings.

## 4. Register and preview

    importer mapper add spec.json --key <name>
    importer preview "<path or UNC locator of the waiting file>" --mapper-version <id> --project <P> --class <C> --type <T>

Check per-target row counts, the `_extras` keys (columns you did not map), and rejects. Repeat 2–4 until it looks
right; every `mapper add` creates a new version, nothing is edited in place. (`preview` and `signature register`
read local or UNC paths; for a Blob-only file, copy it locally first or use `/mcp__importer__onboard`.)

## 5. Bind and land

    importer signature register "<path or UNC locator of the waiting file>" --project <P> --class <C> --type <T> --mapper-version <id>
    importer retry <file_id>              # for each waiting file (see `importer pending` before binding)

`signature register` activates the signature under your Windows login. From then on, files with this signature
land automatically.

## 6. When something goes wrong

- `retry` says the file changed since discovery: deliver it as a new file.
- `retry` says the file is not reachable: you need read access to its share/container.
- `importer pending` warns that a file is "stranded" (quarantined under a signature that is already bound, e.g.
  its retry failed during approval): run the `importer retry <file_id>` it prints.
- A Blob URL with a `?sv=...` SAS token is refused: use the plain URL; access uses your Entra identity.
- A spec error names the problem (unknown target, invalid XPath, unknown shaper): fix the spec, `mapper add`
  again (new version), preview, then bind.
- Files with deeply nested structures that a spec cannot express stay on the legacy parsers until the
  complex-files milestone (Design Decisions §8.22).
