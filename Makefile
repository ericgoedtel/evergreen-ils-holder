SKILL_DIR := $(HOME)/.claude/skills/library-hold

.PHONY: install uninstall test

install:
	uv tool install --force .
	mkdir -p $(HOME)/.claude/skills
	ln -sfn $(CURDIR)/skill $(SKILL_DIR)
	@echo "Installed evergreen-config, evergreen-search, evergreen-hold and linked $(SKILL_DIR)"
	@echo "Now add to ~/.claude/settings.json:  \"permissions\": {\"ask\": [\"Bash(evergreen-hold:*)\"]}"

uninstall:
	uv tool uninstall evergreen-holder || true
	rm -f $(SKILL_DIR)

test:
	uv run pytest
