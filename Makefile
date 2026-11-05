.PHONY: help check run open systemd-install systemd-uninstall status logs test
help: ## Show available commands
	@printf '%s\n' \
		'Usage: make <target>' \
		'' \
		'Portable:' \
		'  check              Check local runtime prerequisites' \
		'  run                Run the server in your terminal' \
		'  open               Open the activity panel in the default browser' \
		'' \
		'Linux user services:' \
		'  systemd-install    Install and enable socket activation' \
		'  systemd-uninstall  Stop and remove the user units' \
		'  status             Show socket and service status' \
		'  logs               Follow socket and service logs' \
		'' \
		'Tests:' \
		'  test               Run Python and userscript tests'

check: ## Check local runtime prerequisites only
	python3 scripts/check_environment.py

run: ## Run the server in your terminal
	python3 overlay_server.py

open: ## Open the local activity panel in the default browser
	@url='http://127.0.0.1:17391/'; \
	if [ "$$(uname -s)" = Darwin ] && command -v open >/dev/null 2>&1; then \
		open "$$url" || printf 'Could not open a browser; visit %s\n' "$$url"; \
	elif [ -n "$${DISPLAY:-}$${WAYLAND_DISPLAY:-}" ] && command -v xdg-open >/dev/null 2>&1; then \
		xdg-open "$$url" || printf 'Could not open a browser; visit %s\n' "$$url"; \
	else \
		printf 'No desktop browser opener available; visit %s\n' "$$url"; \
	fi

systemd-install: ## Install user units and enable socket activation
	@if [ "$$(uname -s)" != Linux ]; then printf 'systemd-install requires Linux\n' >&2; exit 1; fi
	@set -eu; \
	root="$$(pwd -P; printf '.')"; \
	root="$${root%.}"; \
	root="$${root%?}"; \
	SERVER_PATH="$$root/overlay_server.py"; \
	export SERVER_PATH; \
	config="$${XDG_CONFIG_HOME:-$$HOME/.config}/systemd/user"; \
	mkdir -p "$$config"; \
	tmp=''; \
	trap 'test -z "$$tmp" || rm -f "$$tmp"' EXIT HUP INT TERM; \
	for name in ai-browser-overlay.service ai-browser-overlay.socket; do \
		tmp="$$(mktemp "$$config/.ai-browser-overlay.XXXXXXXX")"; \
		awk 'BEGIN { \
			s=ENVIRON["SERVER_PATH"]; \
			if (index(s, "\n") || index(s, "\r")) { \
				print "Checkout path must not contain newline characters" > "/dev/stderr"; exit 1 \
			} \
			condition=s; \
			gsub(/%/, "%%", condition); \
			q="\""; \
			for (i=1; i<=length(s); i++) { \
				c=substr(s,i,1); \
				if (c=="\\" || c=="\"") q=q "\\"; \
				q=q c; \
				if (c=="%") q=q "%"; \
			} \
			q=q "\"" \
		} { \
			p=index($$0,"@SERVER@"); \
			if (p) print substr($$0,1,p-1) q substr($$0,p+8); \
			else { \
				p=index($$0,"@SERVER_CONDITION@"); \
				if (p) print substr($$0,1,p-1) condition substr($$0,p+18); \
				else print \
			} \
		}' "systemd/$$name" > "$$tmp"; \
		chmod 644 "$$tmp"; \
		mv -fT "$$tmp" "$$config/$$name"; \
		tmp=''; \
	done
	systemctl --user daemon-reload
	systemctl --user enable --now ai-browser-overlay.socket
	systemctl --user try-restart ai-browser-overlay.service

systemd-uninstall: ## Stop and remove the installed user units
	@if [ "$$(uname -s)" != Linux ]; then printf 'systemd-uninstall requires Linux\n' >&2; exit 1; fi
	systemctl --user disable --now ai-browser-overlay.socket
	systemctl --user stop ai-browser-overlay.service
	rm -f "$${XDG_CONFIG_HOME:-$$HOME/.config}/systemd/user/ai-browser-overlay.socket" "$${XDG_CONFIG_HOME:-$$HOME/.config}/systemd/user/ai-browser-overlay.service"
	systemctl --user daemon-reload

status: ## Show socket and service status
	systemctl --user status ai-browser-overlay.socket ai-browser-overlay.service

logs: ## Follow systemd socket and service logs
	journalctl --user -u ai-browser-overlay.socket -u ai-browser-overlay.service -p debug -f

test: ## Run Python and userscript tests
	python3 -m unittest discover -s tests
	node tests/test_overlay.cjs
