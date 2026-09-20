# evergreen-holder

Find a book in an Evergreen ILS catalog (e.g. NC Cardinal), see where copies are
available, and place a title hold — with a human confirming every hold. Ships as three
small CLIs plus a Claude Code skill (`/library-hold`) that drives them.

## Install

```bash
make install
```

Then add to `~/.claude/settings.json` so the hold command always prompts, even in auto mode:

```json
{ "permissions": { "ask": ["Bash(evergreen-hold:*)", "Bash(evergreen-config set:*)"] } }
```

## Configure

Config lives at `$XDG_CONFIG_HOME/evergreen-holder/config.toml` (default
`~/.config/evergreen-holder/config.toml`), mode 0600. The skill walks you through it, or:

```bash
evergreen-config set base_url https://johnston.nccardinal.org   # also caches the server IDL
evergreen-search --orgs "hocutt"                                 # find your branch and its ancestors
evergreen-config set branch_id 501
evergreen-config set system_id 500
evergreen-config set consortium_id 1
evergreen-config set username <your catalog username>
evergreen-config set preferred_format hardcover
evergreen-config set-password                                    # interactive; verifies login
evergreen-config doctor
```

The password is only ever entered through `set-password` in your own terminal. Claude never
asks for it.

## Use

```
/library-hold The Overstory by Richard Powers
```

Or by hand:

```bash
evergreen-search "the overstory powers"
evergreen-hold 12547531 --dry-run
evergreen-hold 12547531
```

## Other Evergreen networks

Everything network-specific is in the config file. `base_url` is the catalog origin;
the three org ids come from `evergreen-search --orgs`. The fieldmapper IDL is fetched
from `<base_url>/reports/fm_IDL.xml`, so object decoding follows the server's version.

## Pika systems (read-only)

[Pika](https://github.com/Marmot-Library-Network/Pika-Discovery-Layer) is Marmot's
open-source VuFind fork; the first supported instance is Wake County Public Libraries
(`https://catalog.wake.gov`). Support is read-only for now — **placing holds on Pika
systems is not implemented**; use the catalog website.

Config lives in the same `config.toml` (same file, mode 0600), in a `[pika]` table:

```bash
evergreen-config set pika.base_url https://catalog.wake.gov
evergreen-config set pika.pickup_branch "Wendell Community"   # exact branch name as shown in holdings
evergreen-config doctor   # reports a "pika": {"configured": bool, "missing": [...]} sub-object
```

Search:

```bash
pika-search "Cryptonomicon"
```

Prints one JSON document: `system`, `base_url`, `pickup_branch`, `total_found`, and up
to 10 `results`, each a grouped work with `grouped_work_id`, `title`, `author`, `url`,
`wait_list` (`{"copies", "holds"}` or `null`), and `records` — one per ILS record
(e.g. regular print vs. large print) with `record_id`, `format`, `large_print`,
`copies` (`branch`/`system` availability tiers), `on_shelf_at` (branch names with an
available copy), and `record_url`.

## Development

```bash
uv sync
uv run pytest
uv run python tests/record_fixtures.py        # re-record live Evergreen fixtures (network)
uv run python tests/record_fixtures_pika.py    # re-record live Pika fixtures (network)
```
