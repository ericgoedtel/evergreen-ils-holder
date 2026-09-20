---
name: library-hold
description: Find a book in the user's Evergreen ILS library catalog, report where copies are available, and place a title hold only after the user explicitly confirms. Use when the user asks to find, look up, check availability of, or place a hold on a book at their library.
---

# /library-hold <title> by <author>

You drive three deterministic CLIs. You never talk to the library system directly, never
compose URLs or curl commands, and never edit the config file by hand.

- `evergreen-config doctor` / `evergreen-config set <key> <value>`
- `evergreen-search "<title> <author>"` / `evergreen-search --orgs <name>`
- `evergreen-hold <bib_id> [--dry-run]`

All three print one JSON document. Non-zero exit means the JSON is `{"error", "desc"}`.

## Hard rules

1. **Never run `evergreen-hold` (without `--dry-run`) until the user has answered an
   AskUserQuestion that names the exact bib id, title, format labels, and pickup library
   and chosen "Yes, place the hold".** One confirmation per hold. A "yes" earlier in the
   conversation does not carry over.
2. **Never ask the user for their library password, never run `evergreen-config
   set-password`, and never pass a password on any command line.** If the password is
   not set, tell the user to run `evergreen-config set-password` in their own terminal
   and stop.
3. Do not place holds on more than one bib per confirmation.

## Step 1: doctor

Run `evergreen-config doctor`. If `ok` is true, go to Step 2. Otherwise:

- If `mode_ok` is false: tell the user to run `chmod 600 <config_path>` and stop.
- For each key in `missing`, collect it with AskUserQuestion and write it with
  `evergreen-config set <key> <value>`:
  - `base_url`: the library's Evergreen catalog origin, e.g. `https://johnston.nccardinal.org`.
    Set this first; it also downloads the server's IDL.
  - `branch_id`, `system_id`, `consortium_id`: ask for the user's home branch **by name**,
    run `evergreen-search --orgs "<name>"`, show the matching org and its `ancestors`
    chain, and confirm. `branch_id` = the match's `id`; `consortium_id` = `ancestors[0].id`;
    `system_id` = the ancestor directly above the branch (`ancestors[-1].id`). If the branch
    sits directly under the consortium, set `system_id` equal to `branch_id`.
  - `username`: the user's catalog login name.
  - `preferred_format`: `hardcover` or `paperback`.
- If `password_set` is false: tell the user to run `evergreen-config set-password` in a
  terminal (it prompts without echo and verifies the login), then stop. Do not continue
  to the search until they say it's done and `doctor` reports `ok: true`.

## Step 2: search

Run `evergreen-search "<title> <author>"`. From `results`:

- Drop hits that are clearly not the requested book (different title/author). Use judgment;
  the catalog's relevance ranking is loose.
- Rank the remaining hits by `preferred_format`: bibs whose `formats` contain only the
  preferred format first, mixed-format bibs second, other-only last, then by consortium
  availability. `formats: []` means the record has no labeled ISBNs; treat it as unknown.
- Flag `large_print: true` hits as large print; they are usually not what the user wants.
- Show the user a short list: bib id, title, year, formats, and the three tiers as
  "<available>/<total>" for branch, system, consortium, using the names from the output.
- If a bib is mixed-format, say so: "this record has both hardcover and paperback copies;
  the hold is filled by whichever comes free first."
- If there are no plausible hits, say so and stop. Do not try alternative spellings more
  than once.

## Step 3: decide by tier

For the bib the user wants:

- `copies.branch.available > 0` → tell them it is on the shelf at `<branch name>` right now
  and that no hold is needed. Do not offer a hold unless they ask.
- else `copies.system.available > 0` → offer the hold (Step 4).
- else `copies.consortium.available > 0` → say it is not available within `<system name>`
  and would ship from elsewhere in `<consortium name>` (a week or more). Ask whether they'd
  rather pick another book, with placing the hold anyway as the non-default option.
- else → say no copies are available anywhere right now; a hold would queue. Offer it.

## Step 4: confirm and place

AskUserQuestion with the exact bib id, title, formats, and pickup library name, options
"Yes, place the hold" / "No". Only on "Yes" run `evergreen-hold <bib_id>`.

Notification is taken from the user's catalog account preference (`opac.hold_notify`), the
same way the catalog's own hold form pre-fills it; the output's `notify` is a list of the
method names that were set, e.g. `["email"]` or `["email", "sms"]` (never the raw phone/SMS
number). Mention it in one clause, e.g. "email notification". If `notify` is `[]`, warn the
user that no notification is configured on their account.

Report `hold_id`, `queue_position` of `total_holds`, and `potential_copies`. If `targeted`
is non-null, say which library's copy was assigned ("Evergreen assigned a copy at
<targeted.library>; it ships once their staff pull it"). The catalog site shows only
"Waiting for copy" for this state, so this is information the user cannot see there. If
`targeted` is null, say no copy has been assigned yet and the hold is queued. Then print
both links from the output as clickable markdown links: `record_url` (the catalog page for
the bib the hold targets) and `holds_url` (the user's holds list; requires them to be logged
in to the catalog site). On
`{"error": "HOLD_EXISTS"}` tell the user they already have a hold on this title. On any
other error, show `desc` and stop; do not retry.
