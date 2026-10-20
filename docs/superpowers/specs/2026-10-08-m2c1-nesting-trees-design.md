# M2c-1 — Nesting trees for XML: Design Spec

- **Status:** draft for review, 2026-10-08. Scope and the decisions in §2 were agreed in conversation the same day.
- **Decision record:** Confluence *Data Import Agent — Design Decisions* (page 3774676996). This milestone resolves the
  "expressive specs" half of §8.22; its decisions will be recorded as §8.24 when the spec is approved.
- **Builds on:** M2a (`2026-10-02-m2a-fast-path-design.md`) and M2b (`2026-10-03-m2b-onboarding-design.md`), both
  implemented and accepted on DEV.

## 1. Goal

A complex nested XML template (the SERFF formulary and plan-benefit templates are the yardstick) is onboarded the
same way a flat file is: the importer profiles it, the user's Claude client drafts the mapping, the steward approves,
and every file with that signature lands. No per-template code.

The importer profiles the file's **nesting tree**: its element structure, with counts, and no values. The agent
**annotates that tree**: which nodes are records (one raw table each), which values are fields, and which ancestor
values (keys) carry down. One generic de-nester walks the file with the annotated tree. Each record node lands as
its own table, as close to the file as possible. The formulary is three pseudo-tables, so it lands as three tables.

The guiding principle is unchanged (§0). The importer joins nothing, cleans nothing, and never multiplies rows across
sibling datasets. Associations such as drugs × cost shares are left to downstream joins on the keys each table carries.

## 2. Decisions taken in conversation (2026-10-08)

1. **M2c is split into three cycles**, each with its own spec, plan and build: M2c-1 nesting trees for XML (this
   document), M2c-2 NDJSON + FHIR, M2c-3 plugin distribution. Anything beyond these is handled directly as it comes.
2. **Represent the file as closely as possible.** One raw table per record node. The importer never performs a cross
   or key join. When enumerating two nested datasets together would explode, it is not done.
3. **The signature is unchanged and is 1:1 with the nesting tree.** For XML the signature stays
   `PROJECT|CLASS|XML|<root>` (+ namespace). The tree is profiled once and stored with the signature.
4. **The annotated tree is the config (rule-as-data).** It is the input the generic de-nesting parser runs. The agent
   writes it; stable code executes it.
5. **The data boundary is unchanged.** The tree carries names and counts only. The few-rows samples stay masked
   (§8.23 #13). No real values cross MCP.
6. **The spec vocabulary borrows from SQL on FHIR v2 *ViewDefinition*** (`forEach`, `column` {name, path}, nested
   `select`), so M2c-2 can reuse it for FHIR resources. It does **not** borrow ViewDefinition's cross-product semantics
   (see §5.3).

## 3. Scope

**In M2c-1**
- **Tree profiler** (`trees.py`): the nesting tree of an XML document, with counts, repeat ranges and attributes.
  - It is stored on the signature row, shown through MCP, and checked for drift on every later file with that signature.
- **Record candidates:** repeating, record-like nodes scored against registered schemas, the way flat files are today.
  XML gets its first "looks like" candidates beyond reuse.
- **The `tree` spec kind and its de-nester** (`mappers/tree.py`). It covers:
  - carried keys and per-node tables;
  - `leaves: "auto"`;
  - unpivot;
  - document landing (a subtree into one column);
  - the specials `$identity` and `$name`.
- **MCP:**
  - a new `get_tree` tool;
  - `get_file_profile` returns the tree and the record candidates;
  - `preview_mapping` / `submit_mapping` accept `kind: "tree"` and warn about spec paths missing from the tree;
  - the spec reference and the `onboard` prompt cover trees.
- **The mapping manual** gains a chapter on nested XML with a tree spec.
- **Fixtures, tests, acceptance, and documentation/Confluence updates.**

**Not in M2c-1**
- **JSON / NDJSON trees, declared FHIR signatures, trees built from FHIR definitions:** M2c-2 (§13).
- **Plugin distribution and the plugin chapter of the manual:** M2c-3 (§13).
- **Streaming parse of very large files.** The pipeline already holds a file's bytes and its lxml tree in memory,
  which is fine for SERFF templates. Streaming comes with the first file that needs it (TIC, very large FHIR exports).
- **Joins of any kind inside the importer, and MongoDB as a landing target.**
- **Retiring the `xml` spec kind.** It stays supported. `tree` is preferred for nested templates.

## 4. The nesting tree (`src/importer/trees.py`)

### 4.1 Shape

One pass over the parsed document (`root.iter()`) builds a tree of distinct element paths. Names are namespace-free,
like today's profile and row identities. Example for the formulary (abridged: `"..."` stands for the omitted
children):

```json
{
  "root": "prescriptionDrugTemplateVO",
  "namespaces": {"http://vo.ffe.cms.hhs.gov": 135},
  "value_child": "cellValue",
  "nodes": 37, "truncated": false,
  "tree": {
    "name": "prescriptionDrugTemplateVO", "count": 1,
    "children": [
      {"name": "header", "count": 1, "children": [
        {"name": "templateVersion", "count": 1, "leaf": true},
        {"name": "issuerId", "count": 1, "leaf": true, "via": "cellValue", "attributes": ["cellLocation"]}
      ]},
      {"name": "formularyList", "count": 1, "children": [
        {"name": "formularyVO", "count": 2, "repeats": {"min": 2, "max": 2}, "children": ["..."]}
      ]}
    ]
  }
}
```

The node fields are:

- `count`: total occurrences in the file.
- `repeats`: present when the element occurs more than once under some parent; `min`/`max` give occurrences per
  parent element.
- `leaf`: the element has text and no child elements.
- `attributes`: attribute **names** only.
- `children`: the child nodes, in first-seen order.
- `via`: the value-wrapper compaction. An element whose only child element is the same leaf on every occurrence (the
  CMS `<cellValue>` convention) is shown as a leaf, with `via` naming the wrapper. That wrapper's name also becomes the
  top-level `value_child` hint when it is the same throughout. This halves the size of CMS trees, and the walker
  reads such values through the spec's `value_child`, exactly as the `xml` kind does.
- `ns`: given only when a node's namespace differs from the root's.

Caps are fixed, not settings: at most **5,000 distinct paths**, with `truncated: true` beyond that. No values, value
lengths or value shapes are stored. Everything in the tree is metadata under the data boundary.

### 4.2 Where the tree lives

- **On a miss**, `pipeline.process` builds the tree and stores it in `signature_registry.observed_schema` next to `root`
  and `namespace`. No DDL change is needed; the column is `NVARCHAR(MAX)` JSON. This is the 1:1 signature ↔ tree.
- **On a hit**, the pipeline computes the file's path set (cheap: the document is already parsed) and compares it with
  the stored tree:
  - **New or missing paths** write one `decision_log` row, `tree_drift`, with the paths (names only, capped at 50 each).
    Drift never fails or holds the file: the importer reports a number, not a verdict. New leaves under a table node
    already flow to `_extras`; new branches outside every table node are only visible through this row.
  - **No stored tree** (XML signatures registered before M2c-1): the tree is stored on that first hit (a backfill).

### 4.3 Functions

- `build_tree(doc) -> dict`
- `path_set(doc) -> set[str]`
- `drift(stored, paths) -> {"new": [...], "missing": [...]}`
- `subtree(tree, path, depth) -> dict`: deeper levels are replaced by `{"children_omitted": n}`, so the agent pages
  through a big template.

## 5. The `tree` spec kind (`src/importer/mappers/tree.py`)

### 5.1 Form

The spec object is the **root node**. Its context element is the document root. Every node may have:

| key | on | meaning |
|---|---|---|
| `forEach` | every node but the root | XPath, relative to the parent node's element, selecting this node's elements; one context per match |
| `table` | any node | a registered schema address: emit one row per element of this node |
| `column` | any node | list of `{"name", "path"}`; `path` is XPath relative to this node's element, or a special. A node's columns are **carried down** to every table node below it, and appear in its own rows when it is a table |
| `leaves` | table nodes | `"auto"`: every leaf child not consumed by `column` lands in the schema column whose normalized name matches (`normalize_name`), else under its own name in `_extras` |
| `unmapped` | table nodes | `"extras"` (default) or `"ignore"`, for leaf children that nothing mapped |
| `unpivot` | table nodes | `{over, name_column, value_column}`: one row per child selected by `over`, as in the `xml` kind (§8.9), identity suffixed `#<name>` |
| `document` | table nodes | a column name: the element's whole subtree, serialized as XML text, lands there (land-as-document for irregular branches; shred downstream with the XML methods) |
| `select` | any node | the child nodes |

Top-level only: `kind: "tree"`, `namespaces` (prefix → URI), `value_child` (as in the `xml` kind).

**Specials** for `path`:
- `$file_id`
- `$context.<key>`
- `$identity`: this node's element identity, the namespace-free path used as `row_identity`.
- `$name`: this node's element local name. It covers elements whose name is the data, e.g. `sbc` / `hsa` / `url`
  under a cost-share variance.

`$row_identity` is accepted as an alias of `$identity` on table nodes. `$parent(...)` is not needed: a parent link is
a carried `$identity` column on the parent node.

### 5.2 The formulary as a tree spec

```json
{
  "kind": "tree",
  "namespaces": {"x": "http://vo.ffe.cms.hhs.gov"},
  "value_child": "x:cellValue",
  "column": [
    {"name": "fileid", "path": "$file_id"},
    {"name": "serffbindername", "path": "$context.serffbindername"},
    {"name": "run_id", "path": "$context.run_id"},
    {"name": "templateVersion", "path": "x:header/x:templateVersion"},
    {"name": "issuer_id", "path": "x:header/x:issuerId"},
    {"name": "state_code", "path": "x:header/x:statePostalCode"}
  ],
  "select": [
    {"forEach": "x:formularyList/x:formularyVO",
     "column": [{"name": "formulary_id", "path": "x:formularyID"},
                {"name": "drug_list_id", "path": "x:drugListID"},
                {"name": "num_of_tiers", "path": "x:numberTiers"}],
     "select": [
       {"forEach": "x:formularyCostSharingTypeList/x:formularyCostSharingTypeVO",
        "column": [{"name": "drug_tier_id", "path": "x:drugTierLevel"},
                   {"name": "one_month", "path": "x:oneMonthOutNetworkRetailOfferedIndicator"},
                   {"name": "three_month_in", "path": "x:threeMonthInNetworkMailOfferedIndicator"},
                   {"name": "three_month_out", "path": "x:threeMonthOutNetworkMailOfferedIndicator"}],
        "select": [
          {"forEach": "x:costSharingTypeList/x:costSharingTypeVO",
           "table": "npvcs.xml.formulary_benefit_definition",
           "column": [{"name": "cost_coverage_type", "path": "x:networkCostType"},
                      {"name": "prescription_period", "path": "x:drugPrescriptionPeriodType"},
                      {"name": "copay", "path": "x:coPayment"},
                      {"name": "coin", "path": "x:coInsurance"}]}]}]},
    {"forEach": "x:drugList/x:drugListVO",
     "column": [{"name": "drug_list_id", "path": "x:drugListID"}],
     "select": [
       {"forEach": "x:drugTierLevelList/x:drugTierLevelVO",
        "table": "npvcs.xml.formulary_tier_definition",
        "column": [{"name": "tier_level", "path": "x:drugTierLevel"},
                   {"name": "tier_type", "path": "x:drugTierType"}]},
       {"forEach": "x:drugVOList/x:drugVO",
        "table": "npvcs.xml.formulary_drug_list",
        "column": [{"name": "rxcui", "path": "x:rXCUI"},
                   {"name": "tier_level", "path": "x:tier"},
                   {"name": "authorizationRequired", "path": "x:authorizationRequired"},
                   {"name": "stepTherapyRequired", "path": "x:stepTherapyRequired"}]}]}
  ]
}
```

It mirrors the file. The header values are written once at the root, and `drug_list_id` once per list. It produces
exactly the rows of today's `tests/fixtures/formulary_spec.json`; that equivalence is a test (§11).

### 5.3 Semantics

- **Walk.** Depth-first. At each node, evaluate `forEach` against each parent context. For every matched element,
  evaluate the node's `column`s and merge them over the carried set (the nearer node wins on a name clash), emit a row
  if the node is a table, then descend into `select`.
- **Sibling nodes never combine.** Two `select` children of the same node are walked independently; neither
  multiplies the other's rows. This deliberately departs from ViewDefinition, where sibling `forEach` selects form a
  cross product. Row counts are therefore bounded by element counts. That is what keeps an "exploding" association
  out of the importer.
- **Values.**
  - A path matching nothing gives NULL (not a reject).
  - A path matching several nodes lands the first value, with the full list in `_extras` under the column name.
  - An element value is read through `value_child` when present, otherwise from its text.
  - An attribute or XPath string result lands as is.
  - Values land exactly as written. These are the `xml` kind's rules (§8.9), and the helpers are shared (§9).
- **Schema fit.** Columns the target schema lacks go to `_extras`. Carried columns are included, so nothing is lost.
- **Rejects.** Any exception while building one row gives `RejectRow(target, row_identity, <element serialized>,
  "mapping_error", detail)`. The file continues.
- **Row identity.** The table element's identity (`/formularyList/formularyVO[2]/...`), with the existing
  256-character ordinal fallback. Unique per table within a file.

### 5.4 Validation (`parse_tree_spec`)

The following raise `SpecError` (→ `invalid_spec`):
- unknown keys;
- a missing `forEach` below the root;
- a `table` that is not a registered address (when `known_schemas` is given);
- `leaves` / `unmapped` / `unpivot` / `document` on a non-table node;
- a branch with no table node anywhere beneath it (dead config);
- invalid XPath (compiled once at parse);
- a column name outside `[A-Za-z0-9_]+`;
- an unknown special.

The whole spec must contain at least one table node.

**Against the stored tree** (onboarding only, never at run time): every `forEach` and column path is resolved
structurally against the signature's tree. Paths that cannot occur are returned as `tree_warnings` in the preview.
They are warnings, not errors, because a tree comes from one file.

## 6. Record candidates (`profiler.py`)

For an XML miss, besides `reuse_mapper`, every **record-like node** is matched against active schemas. A record-like
node is non-leaf and either repeats or holds at least two leaves. It is matched the same way flat files are today:
- the score is the share of the node's normalized leaf names found in the schema;
- Jaccard is the tie-break;
- the minimum score is 0.5.

A new candidate kind `record` carries `{path, schema_id, score, matched, missing, extra, jaccard}`. The deterministic
`suggested_draft` stays reuse-only for XML. The agent composes the tree spec, with the candidates telling it which
node probably feeds which table.

## 7. MCP (`mcp_server.py`, `onboarding.py`, `mcp_reference.md`)

| Tool | Change |
|---|---|
| `get_file_profile` | for XML: `tree` (depth-limited to 4, via `subtree`) and `record` candidates, in place of the flat 500-path map |
| `get_tree` *(new, read-only)* | `(signature_id, path=None, depth=4)` → a subtree of the stored tree, for paging through large templates |
| `get_samples` | unchanged: absolute namespace-free paths, masked shapes, fixed caps |
| `preview_mapping`, `submit_mapping` | accept `kind: "tree"`; the result adds `tree_warnings`; counts, `_extras` keys, rejects and masked samples as today |
| `get_spec_reference` | documents the tree kind (§5) with the formulary example |
| `accept_mapping`, `register_schema` | unchanged; still require the steward's sign-off |

`get_tree` joins the permission allow-list in `.claude/settings.json`, beside the other read tools. The `onboard`
prompt gains an XML branch:
1. read the tree;
2. pick record nodes, using the candidates;
3. write a tree spec that mirrors the file;
4. preview;
5. fix the `tree_warnings` and the `_extras` surprises;
6. submit.

`review_table` lists, per target, the record path and its carried columns.

## 8. Pipeline, loading and administration

- `mappers/loading.py`: `kind: "tree"` → `TreeMapper`. One `parse_definition(defn, ...)` dispatches on `kind` for
  `load_mapper`, `admin` (`importer mapper add`) and onboarding, replacing the three call sites that call `parse_spec`
  directly.
- `pipeline.process`, for XML files:
  - on a miss, store the tree (§4.2);
  - on a hit, run the drift check and backfill a missing tree.

  The extraction path is unchanged: `mapper.extract(doc, ctx)`.
- `onboarding._targets` and `prior_mappings` read targets from tree specs too (every `table` in the tree).

## 9. Targeted refactor

`mappers/spec.py` keeps value reading inside `DeclarativeMapper`: `_values`, `_value`, `_is_value_element`, `_put`.
These move to a small `mappers/values.py` taking the compiled `value_child`, so the `xml` and `tree` kinds share one
implementation of the value rules. There is no behaviour change; the existing `test_spec_xml.py` must pass unchanged.

## 10. Control-plane DDL

No new columns or tables. Comments only, mirrored on the Schema DDL Confluence page:
- `signature_registry.observed_schema`: "XML: root, namespace, nesting tree (§8.24)".
- `decision_log.action_type`: add `tree_drift`.

## 11. Testing and acceptance

**Fixtures (synthetic only; no real file is read to build them)**
- `tests/fixtures/formulary_dc.xml` (existing).
- `tests/fixtures/benefits_sample.xml` (new), modelled on the structure `npvcs_env/src/parsers/parseBenefits.py`
  walks: two packages, each with
  - a header,
  - benefits,
  - plan attributes,
  - cost-share variances with `sbc` / `hsa` / `url` / `avCalculator` and the `moopList` / `planDeductibleList` /
    `serviceVisitList` lists.

  It uses invented values and the CMS `cellValue` / `cellLocation` convention.
- `tests/fixtures/formulary_tree_spec.json`, `benefits_tree_spec.json`, and a synthetic schema set for the benefits
  tables.

**Unit (no database, no model)**
- **Tree profiler:**
  - counts and repeat ranges;
  - attributes are names only;
  - value-wrapper compaction;
  - mixed namespaces;
  - the 5,000-path cap;
  - `subtree` paging;
  - `drift` (new / missing).
- **Spec parsing:** every validation rule in §5.4.
- **Walker:**
  - carried keys and shadowing;
  - `$identity` / `$name` / `$context` / `$file_id`;
  - `leaves: "auto"` with normalized matching;
  - unpivot;
  - document landing;
  - multi-match to `_extras`;
  - a per-row `mapping_error` reject;
  - **siblings do not cross-join** (row counts equal element counts).
- **Golden equivalence:** the formulary tree spec yields the same rows (targets, identities, fields, `_extras`) as
  `formulary_spec.json`.
- **Benefits:** every table's row count equals its element count in the fixture; every child row carries its package
  `$identity`.
- **Record candidates** on both fixtures.
- **Pipeline:**
  - a miss stores the tree;
  - a hit with an extra branch writes `tree_drift` and still lands;
  - a hit on a pre-M2c signature backfills its tree.
- **MCP:**
  - `get_tree` paging;
  - `get_file_profile` returns the tree and no values;
  - preview and submit with `kind: "tree"` return `tree_warnings`;
  - masked samples are unchanged (the existing masking tests stay green).
- **Invariants #1 and #2 untouched:** their suites stay green.

**Integration (DEV, `pytest -m integration`)**
- The formulary and benefits fixtures run end to end through `process` → onboarding (tree spec submitted and
  accepted in-test) → retry → raw tables. Per-table counts are asserted.

**M2c-1 acceptance (live, DEV)**
1. All unit and integration suites are green.
2. One **real formulary** file and one **real plan-benefit** file from the catalog are onboarded through
   `/mcp__importer__onboard`. The agent drafts from the tree, the steward approves, and the files land. The data guard
   stays on, and no real value crosses MCP.
3. For each real file, per-table raw row counts are compared with the legacy tables in NPVCS for the same catalog file
   id. These are counts only, over the read-only (SELECT-only) destination engine. The differences are explained and
   recorded (for example, legacy rows dropped by its joins). The importer reports numbers, not a verdict.

## 12. Risks and open items

- **Very large XML:** lxml with `huge_tree=False` refuses text nodes over 10 MB and nesting deeper than 256 levels,
  which is fine for SERFF. Streaming is deferred (§3).
- **Tree size on big templates:** handled by depth-limited `get_file_profile` plus `get_tree` paging. The 5,000-path
  cap is the backstop.
- **`//` in `forEach`:** allowed (it is XPath) but discouraged in the reference. Child steps mirror the tree and keep
  identities stable.
- **Legacy surrogate keys** (`PackageId`, `costShareVarianceId`, `PlanAttributeId` were database IDENTITY values or
  lookups): in raw they become carried `$identity` strings or natural keys (`planId`). Rebuilding the integer keys is
  downstream work.

## 13. Next cycles (decisions so far, kept here so they are not lost)

**M2c-2 — NDJSON + FHIR**
- **Declared signatures:** `PROJECT|CLASS|NDJSON|fhir:R4:<resourceType>`, taken from the requestor's declaration and
  checked against each line's `resourceType` (a mismatched line is a row reject, not a file failure).
- **Trees from the standard:** the tree is built from the published FHIR R4 definition of the resource (the
  StructureDefinition, CC0-licensed, vendored as data), so a resource can be registered before any file exists.
- **The same `tree` spec over JSON:**
  - a JSON path evaluator (dotted names, `[*]` for arrays);
  - dynamic object keys collapse to `{*}` in the tree, so key values never cross MCP, and `$name` lands the key;
  - NDJSON is read line by line.
- **Flat-JSON signatures:** revisited then. The current deep key-union hash is fragile; the shallow form (root type +
  top-level keys) was the recommendation. Ad-hoc JSON otherwise stays with its dedicated parsers and MongoDB.

**M2c-3 — Plugins**
- Decide how a reviewed plugin reaches every workstation without executing code from the database (§8.22): repo +
  redeploy, or a shared plugins folder with the existing SHA-256 pin.
- Write the plugin chapter of the mapping manual.

## 14. Documentation updates (part of the work)

- `CLAUDE.md`: repo layout (`trees.py`, `mappers/tree.py`, `mappers/values.py`) and current status.
- `README.md`, `docs/mapping-manual.md`: the new chapter on nested XML with a tree spec.
- `docs/architecture/`: re-run the C4 generator and check the layer rule still holds.
- Confluence:
  - Design Decisions §8.24 (this design), with §8.22 updated to point at it;
  - Blueprint (M2c split);
  - project page task list;
  - Schema DDL comments.

## 15. Acceptance results (live, DEV, 2026-10-13)

1. **Suites:** 351 unit tests and 16 DEV integration tests pass on `main` after the merge.
2. **Real files**, both from SERFF run 64062 (project DC, class PC, binder CFBC-DC27-125122798), parked by locator
   with `importer process`. The data guard stayed on; only names, counts and masked shapes crossed MCP.

**Plan benefits** (catalog file 208246, ledger file 129)
- **Onboarding:** a miss (`DC|PC|XML|planBenefitTemplateVO`, signature 51), then the agent drafted from the tree.
  - The steward declined the suggested reuse (a test-run spec aimed at `import_agent_dev.it_m2c1.*`).
  - The agent's tree spec targets the ten registered `npvcs.xml.*` tables, with `leaves: "auto"` on every table node.
  - Carried columns: `fileid`, `run_id` and `serffbindername` from the root; `packageId`, the plan's
    `planAttributeId` and `costShareVarianceId` as carried `$identity` keys.
  - There were no tree warnings and no rejects. The steward accepted draft 408.
- **Landed:** 6,008 / 6,008 rows (100%). Every table matches legacy NPVCS (`WHERE fileid = 208246`):

| table | raw | legacy |
|---|---|---|
| package | 8 | 8 |
| benefits | 536 | 536 |
| planattributes | 44 | 44 |
| costsharevariance | 88 | 88 |
| sbc | 88 | 88 |
| hsa | 88 | 88 |
| avcalculator | 88 | 88 |
| mooplist | 88 | 88 |
| plandeductiblelist | 140 | 140 |
| servicevisitlist | 4,840 | 4,840 |

**Formulary** (catalog file 208247, ledger file 128)
- It hit the existing signature `DC|PC|XML|prescriptionDrugTemplateVO`. That signature was bound to the M2a `xml`-kind
  formulary spec by the M2a acceptance integration test, so the file landed without onboarding: 7,739 / 7,739 rows.
- The steward chose to keep it. The tree kind's live acceptance rests on the benefits file, the nested case it exists
  for. Counts still match legacy (`WHERE fileid = 208247`):

| table | raw | legacy |
|---|---|---|
| formulary_benefit_definition | 376 | 376 |
| formulary_tier_definition | 13 | 13 |
| formulary_drug_list | 7,350 | 7,350 |

**Differences from legacy (values, not counts):**
- `fileid` holds the importer ledger id, not the catalog file id.
- The link keys are element identities, not legacy integers (§12).
- `PLAN_YEAR`, `packageName`, `fileName`, `fileHash`, `tin`, `hpid`, `enrollmentPaymentURL`, `costShareVarianceName`,
  the tier-2 amounts and each table's own surrogate id stay NULL. The file does not carry them; legacy added them at
  load time.

**Findings (follow-ups):**
1. Explicit tree `column` names match schema columns case-sensitively (`PlanAttributeId` vs `planAttributeId`), while
   `leaves: "auto"` normalizes. The accepted spec carries the plan key under both spellings. Matching should
   normalize as `leaves` does.
2. Integration tests leave pending signatures on DEV: 17 test-run entries crowd `list_pending`.
3. The M2a acceptance integration test binds the real `DC|PC|XML|prescriptionDrugTemplateVO` signature on shared DEV
   on every run. It should use a synthetic project, as the M2c-1 tests do.
4. `$file_id` is the ledger id. If downstream needs the catalog file id, it should be a requestor context value (for
   example `$context.catalog_file_id`).
5. Locators: under Git Bash (MSYS), a `\server\share` argument is rewritten to `C:\server\share`. Run
   `importer process` from PowerShell or cmd.
