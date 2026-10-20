# Data Wrangler Consumer Evaluation: `data-import-agent` MCP

**Evaluator Persona:** Data Wrangler / Analyst  
**Technical Background:** Tabular data mapping, schema validation, data profiling; limited SQL Server internals/ODBC knowledge.  
**Date of Evaluation:** October 20, 2026  
**Test Asset:** `file_example_CSV_5000.csv` (5,000 records, 8 columns)  
**Target Environment:** `sql1/import_agent_dev [DEV]`

---

## Executive Summary

The **`data-import-agent` MCP** delivers an outstanding core design for ELT onboarding:
- Masking sample values into shape signatures (`99/99/9999`, `aaaaaa`) provides a robust compliance and privacy boundary while still conveying semantic data types.
- The declarative JSON mapping spec format (`kind: tabular`, `auto: true`, unmapped column routing to `_extras`) is intuitive and avoids requiring Python code or SQL scripts.
- The `preview_mapping` dry-run provides immediate pre-flight visibility with exact row counts, reject metrics, and shape samples.

However, during real-world execution, the onboarding workflow hit significant friction at both the **entry point** (introducing files) and the **exit point** (signing off and staging data). This document evaluates what worked, what failed, why it failed, and recommendations for improvements.

---

## 1. What Worked

### 1.1 Masked Shape Profiling (`get_samples`)
The masked sampling mechanism is the single strongest feature from a governance and usability standpoint:
- `99/99/9999` immediately indicated date formats without leaking personal dates.
- `aaaaaa aaaaaa` clearly indicated multi-word text strings (country names) without exposing sensitive names.
- `99` (age) vs `9999` (ID) made distinguishing numeric identifiers from integer values effortless.
- **Verdict:** Highly effective for data wranglers needing to understand semantics without violating privacy boundaries.

### 1.2 Declarative Mapping Spec (`get_spec_reference`, `submit_mapping`)
- The JSON specification separates business rules from database mechanics.
- The `"auto": true` flag paired with automatic routing of unknown columns to `_extras` makes mapping painless for standardized files.
- The clear contract ensures no custom scripting or pipeline coding is required.

### 1.3 Pre-Flight Dry Run (`preview_mapping`)
- The preview dry-run returned exact projected metrics:
  - Projected landed: `5,000`
  - Rejects: `0`
  - Unmapped leftovers: `0`
- Previewing sample rows in masked form before anything commits to disk or database provides strong confidence.

---

## 2. What Failed and Root Cause Analysis

### 2.1 The "Missing Front Door" (No File Ingestion MCP Tool)
* **Observed Symptom:** Calling `/mcp:data-import-agent:onboard` on a new file returned `list_pending: []`.
* **Root Cause:** The MCP server is strictly an onboarding review interface for files *already* fingerprinted and quarantined inside the control database. It does not provide any tool to introduce, upload, or trigger discovery on an incoming file from a local path or file share.
* **Wrangler Friction:** The user was forced out of the MCP chat workflow and into a command-line environment to run `importer process ...` with technical arguments (`--project`, `--class`, `--type`, `--source`). A non-technical data wrangler will stall at this step.

### 2.2 The "Empty Room" Problem (Destination Schema Bootstrapping)
* **Observed Symptom:** Calling `search_schemas` returned an empty list (`[]`). Attempting to register a target table address (`import_agent_dev.dbo.demofile`) failed with:
  > `database 'import_agent_dev' is not a configured destination (importer config set destination ...)`
* **Root Cause:** In a fresh or unconfigured database, no destination schemas exist, and multi-tenant destination connections are unconfigured by default.
* **Wrangler Friction:** Data wranglers think in terms of datasets and tables, not multi-database server connection mappings or ODBC strings.

### 2.3 The Sign-Off Elicitation Trap (`accept_mapping` & `register_schema`)
* **Observed Symptom:** When calling `register_schema` or `accept_mapping` after the user explicitly approved, the tools returned:
  ```json
  {
    "accepted": false,
    "reason": "the user declined"
  }
  ```
* **Root Cause:** Both sign-off tools are decorated with `@mcp.tool(annotations=SIGN_OFF)` and resolve parameters using `Annotated[ElicitationResult[Confirm], Resolve(...)]`. If the connected MCP client or bridge lacks interactive form elicitation support, `_confirmed(approval)` defaults to `False`.
* **Wrangler Friction:** Even when the user typed "accept" or "register schema" in plain text, the MCP server blocked them because the protocol-level UI handshake was unsupported.

### 2.4 Infrastructure Leakage (`MemoryError` during pyodbc Staging)
* **Observed Symptom:** Once accepted, retrying the waiting file resulted in:
  ```text
  file_id=1 failed: stage_failed: MemoryError:
  ```
* **Root Cause:** `src/importer/mssql_store.py` enables `cursor.fast_executemany = True` for bulk inserts. On SQL Server ODBC drivers with `NVARCHAR(MAX)` columns and large batch sizes without explicit `setinputsizes`, pyodbc triggers a known C-level driver memory allocation failure.
* **Wrangler Friction:** Driver buffer limits and bulk insert optimizations leaked directly into the user experience, causing a standard 5,000-row file to fail unexpectedly.

---

## 3. Improvement Recommendations

### High Priority (Usability & Completeness)

1. **Add an Ingestion MCP Tool (`ingest_file` or `discover_file`)**
   - Provide a tool accepting a file locator/path (`path: str`, optional `project: str = "DEFAULT"`, `cls: str = "IMPORT"`).
   - This keeps the entire lifecycle inside MCP, eliminating the need to drop into a terminal.

2. **Add Direct Confirmation Fallback to Sign-Off Tools**
   - Add an optional boolean argument (e.g. `user_confirmed: bool = False`) or confirmation token to `accept_mapping` and `register_schema`.
   - If the client environment does not complete interactive elicitation, allow an explicit `user_confirmed=True` argument passed by the agent when authorized by the user.

3. **Simplify Destination Table Creation (`register_target_schema`)**
   - Allow registering a destination table directly in the control database (`import_agent_dev.dbo.<table_name>`) without requiring pre-configured external destination aliases via `importer config set`.

4. **Resilient Batch Staging in `mssql_store.py`**
   - In `stage_batch`, add exception handling around `fast_executemany`. If a `MemoryError` or pyodbc driver error occurs, automatically fall back to smaller sub-batches (e.g. 500 rows) or standard `executemany`.

---

## 4. Evaluation Matrix

| Capability | Score | Assessment |
| :--- | :---: | :--- |
| **Privacy & Masking** | 10 / 10 | Best-in-class; masked shapes preserve context without leaking PII. |
| **Mapping Spec Design** | 9 / 10 | Clean, declarative, and easy to reason about. |
| **Dry-Run Preview** | 9 / 10 | Immediate metrics and rejection tracking. |
| **File Discovery / Ingestion** | 2 / 10 | Missing in MCP; requires manual CLI execution. |
| **Client Protocol Compatibility** | 4 / 10 | Elicitation dependency blocks approval in non-eliciting clients. |
| **Driver Robustness** | 5 / 10 | Fast-executemany memory errors leak into user-facing status. |

---

## Conclusion
The data abstraction and review mechanisms of `data-import-agent` are exceptionally well designed for data wranglers. Adding an MCP-level file ingestion tool, a fallback confirmation path for sign-off tools, and driver-level retry logic for batch staging will make the agent completely self-sufficient.
