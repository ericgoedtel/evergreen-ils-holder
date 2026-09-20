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

## Development

```bash
uv sync
uv run pytest
uv run python tests/record_fixtures.py   # re-record live fixtures (network)
```
