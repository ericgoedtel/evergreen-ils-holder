# library-hold: design spec

A user-scoped Claude Code skill, `/library-hold <title> by <author>`, backed by
three small deterministic Python CLIs that talk to an Evergreen ILS over its
OpenSRF HTTP gateway. Claude does the judgment (which search hit is the book,
applying the user's format preference); the tools do all the talking to
Evergreen; the human does the confirming. No hold is ever placed without the
user saying yes in the active session.

## Verified facts (NC Cardinal, 2026-09-20)

- Gateway: `https://johnston.nccardinal.org/osrf-gateway-v1` (GET or POST,
  `service`, `method`, repeated JSON-encoded `param`). Evergreen 3.15.5.
- Org units: Hocutt-Ellington Memorial Library = **501**, Clayton Library
  System = **500**, NC Cardinal consortium root = **1**.
- `open-ils.search.biblio.multiclass.query({limit, org_unit}, "<terms>
  search_format(book) -item_form(d)", 1)` returns physical books, excluding
  large print. Result: `{count, ids: [[bib_id, ...], ...]}`.
- `open-ils.search.biblio.record.mods_slim.retrieve(bib_id)` → `mvr` object
  (title, author, pubdate, first isbn, physical_description, ...).
- `open-ils.supercat.record.object.retrieve(bib_id)` → `[bre]`; `bre.marc` is
  MARCXML. `020 $a` = ISBN, `020 $q` = "(paperback)" / "(hardcover)".
  Cataloging is inconsistent: hardcover and paperback are sometimes separate
  bibs and sometimes merged on one bib with several 020 fields.
- `open-ils.search.biblio.record.copy_count(org_id, bib_id)` → list of
  `{org_unit, depth, count, available}` for the org and every ancestor, so one
  call scoped to the branch yields branch/system/consortium tiers.
- `open-ils.actor.org_tree.retrieve()` → nested `aou` tree.
- `open-ils.auth.login({username, password, type: "opac"})` → on success
  `{ilsevent: 0, textcode: "SUCCESS", payload: {authtoken, authtime}}`; on
  failure `{ilsevent: 1000, textcode: "LOGIN_FAILED"}`.
- `open-ils.auth.session.retrieve(authtoken)` → `au` object; `au.id` is the
  patron id.
- `open-ils.circ.holds.test_and_create.batch(authtoken, {patronid, pickup_lib,
  hold_type: "T"}, [bib_id])` → streams `{target, result}` per target; `result`
  is the integer hold id on success, an event hash on failure (e.g.
  `HOLD_EXISTS` for duplicates).
- `open-ils.circ.hold.queue_stats.retrieve(authtoken, hold_id)` →
  `{total_holds, queue_position, potential_copies, status, estimated_wait}`.
- Fieldmapper objects arrive as `{"__c": "<class>", "__p": [values...]}`; the
  field order comes from `<base_url>/reports/fm_IDL.xml` (1.1 MB). It varies
  between Evergreen versions, so it is fetched once and cached rather than
  hardcoded.
- There is no Evergreen/OpenSRF client library on PyPI.

## Components

### `evergreen-config`

- `doctor` → JSON: `{config_path, exists, mode_ok, idl_cached, missing:
  [keys], password_set: bool}`. Exit 0 when everything is present, 1 otherwise.
  Local checks only; no network.
- `set <key> <value>` → writes one key to
  `$XDG_CONFIG_HOME/evergreen-holder/config.toml` (default
  `~/.config/evergreen-holder/config.toml`), creating dir (0700) and file
  (0600) as needed. Setting `base_url` also fetches `fm_IDL.xml` into
  `$XDG_CACHE_HOME/evergreen-holder/fm_IDL.xml`. Refuses `password`.
- `set-password` → interactive `getpass` (no echo), verifies with
  `open-ils.auth.login`, writes on success. Only ever run by the human in a
  real terminal; the skill must never run it and Claude must never ask for the
  password.

Config keys: `base_url`, `branch_id`, `system_id`, `consortium_id`,
`username`, `password`, `preferred_format` (`hardcover` | `paperback`).

### `evergreen-search`

- `evergreen-search "<title> <author>"` → JSON:
  ```
  {"preferred_format": "hardcover",
   "branch": {"id": 501, "name": "..."}, "system": {...}, "consortium": {...},
   "results": [
     {"bib_id": 12547531, "title": "...", "author": "...", "year": "2018",
      "isbns": [{"isbn": "039335668X", "label": "paperback"}, ...],
      "formats": ["paperback", "hardcover"],
      "physical_description": "print 502 pages ; 25 cm",
      "large_print": false,
      "copies": {"branch": {"available": 1, "total": 1},
                 "system": {"available": 1, "total": 1},
                 "consortium": {"available": 72, "total": 102}}}, ...]}
  ```
  Up to 25 results, in Evergreen relevance order. No ranking in the tool;
  Claude ranks.
- `evergreen-search --orgs "<name fragment>"` → JSON list of matching org
  units with `id`, `name`, `shortname`, and the ancestor chain
  (`[{id, name}, ...]` from the root down), so a user can find their branch,
  system, and consortium ids.

### `evergreen-hold`

- `evergreen-hold <bib_id> [--dry-run]` → logs in, resolves patron id,
  places a `T` hold with `pickup_lib = branch_id`, then fetches queue stats.
  JSON: `{"hold_id": ..., "bib_id": ..., "pickup_lib": ..., "queue_position":
  3, "total_holds": 7, "potential_copies": 102, "estimated_wait": ...}`.
  On an Evergreen event (e.g. `HOLD_EXISTS`) → JSON `{"error": textcode,
  "desc": ...}` and exit 2.
- `--dry-run` does everything through login + patron lookup, prints the
  payload it would send, and exits 0 without calling the create method.
- Reads the password only from the config file. Never accepts it as an
  argument or env var.

### Skill: `/library-hold`

Lives at `skill/SKILL.md` in this repo, symlinked to
`~/.claude/skills/library-hold`. Flow:

1. Run `evergreen-config doctor`. If anything is missing: ask (via
   AskUserQuestion) for `base_url`, the branch by name (resolve with
   `evergreen-search --orgs`, show the match and ancestor chain, confirm),
   `username`, `preferred_format`; write each with `evergreen-config set`.
   If `password_set` is false, tell the user to run `evergreen-config
   set-password` in a terminal and stop. Never ask for the password.
2. Run `evergreen-search "<title> <author>"`. Collapse obvious non-matches.
   Rank by preferred format: bibs whose formats are only the preferred
   format first, mixed second, other-only last. When a bib is mixed, say so
   ("this record has both; you'll get whichever frees up first").
3. Tier logic for the chosen bib:
   - `branch.available > 0` → "on the shelf at <branch>, go get it". No hold
     offered.
   - else `system.available > 0` → offer the hold.
   - else `consortium.available > 0` → "not available in <system>; want to
     pick another book?" with the hold as the non-default option.
   - else → report nothing available; still offer the hold (it will queue).
4. Before running `evergreen-hold`, AskUserQuestion showing bib id, title,
   formats, and pickup library. Only on an explicit yes run
   `evergreen-hold <bib_id>`. Report hold id and queue position.
5. No results / nothing matches → say so and stop.

### The gate

1. `evergreen-search`, `evergreen-config`, and `evergreen-hold` are separate
   executables, so no Bash allowlist pattern for one covers another.
2. The skill mandates AskUserQuestion before `evergreen-hold`.
3. `~/.claude/settings.json` gets `"permissions": {"ask":
   ["Bash(evergreen-hold:*)"]}`, forcing a prompt even in auto mode. This is
   verified during implementation, not assumed.

## Non-goals (v1)

Open Library, books.txt / reading list, progress tracking, pre-hold
eligibility checks (Evergreen rejects duplicates itself), ILL, MCP server,
per-library copy breakdown, hard format filtering, token caching.

## Tooling decisions

Python ≥ 3.11, `uv`, `httpx`, stdlib `tomllib` for reading and a tiny writer
for TOML, `argparse`, `pytest`. Recorded gateway fixtures under
`tests/fixtures/`. `make install` runs `uv tool install .` and symlinks the
skill.
