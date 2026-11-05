# AI browser activity

A local server and optional browser overlay let LLMs report actions against
specific URLs. Reporting is voluntary. Hooks for Codex, Claude, Gemini, and
other tools remain future work.

## Requirements and checks

Run the server directly or as a systemd user service. Requirements:

- Python 3.10+ for the server. It uses only the standard library.
- Node.js for `make test`, which runs Python and userscript tests.
- `make check` checks runtime prerequisites only. It does not parse source or
  run tests. It reports whether Node.js and Linux service commands are
  available, but does not check browser or extension installation.
- Systemd user service is optional.

[Violentmonkey](https://violentmonkey.github.io/get-it/) in Google Chrome on
Linux is the tested setup. Firefox is untested. Install Violentmonkey in each
browser profile.

## Start and install

`make help` lists commands by category. Run `make run` to start the server in
your terminal. On Linux, `make systemd-install` installs and starts socket
activation. It does not open the panel; run `make open` separately when needed.
Install and uninstall report an error on other operating systems.

The systemd service works as follows:

- Its units point to this checkout. Keep the checkout in place and rerun
  `make systemd-install` after moving it.
- The socket listens on `127.0.0.1:17391` and starts Python when a request
  arrives.
- After five idle minutes, the service exits when its handlers finish. The
  socket stays available.
- `AI_OVERLAY_IDLE_MINUTES` changes the idle interval for both `make run` and
  the service.

To change the service interval, run
`systemctl --user edit ai-browser-overlay.service` and add:

```ini
[Service]
Environment=AI_OVERLAY_IDLE_MINUTES=10
```

Then restart it with `systemctl --user restart ai-browser-overlay.service`.
Do not run `make run` while the socket owns the port. Per-unit debug logging
requires systemd 258+. Older versions support socket activation.

`make open` launches the panel in the default browser:

- macOS uses `open`.
- A graphical Linux session uses `xdg-open`.
- If no opener is available or it fails, visit <http://127.0.0.1:17391/>
  manually. This also works in headless sessions.

To install or update the userscript:

1. Open <http://127.0.0.1:17391/ai-overlay-http.user.js> in Violentmonkey.
2. Reload the page.

Version 1.5.5 changes update metadata, so manually reinstall or update once.
Disable the older OpenAI-named script before replacing it. The panel detects
installed versions 1.5.4 and later. If no version is reported, the script may
be missing, disabled, or older.

The server creates the local-file channel at startup. Restart it after source
updates. Use the HTTP channel if the checkout is not writable. To update through
the file channel:

- Paste the generated file URL into the browser address bar, or drag the file
  into Violentmonkey with **Track external edits** and leave its installer open.
- Chrome requires Violentmonkey's **Allow access to file URLs** permission.
  This grants broad local-file access.
- Background file updates are unverified. Browsers may block links to files
  from an HTTP page.
- HTTP and file channels share one script identity. Each metadata field can
  hold only one URL, so neither channel is an automatic fallback.

## Use and troubleshooting

Open <http://127.0.0.1:17391/> and publish:

- An agent name and stable session ID.
- The page URL and a concise action.

Each agent and session pair has separate updates. Do not include credentials or
secrets. Updates stay in memory and clear when the server stops. The overlay
polls without sending page content or form values.

Activate each page document using the same agent and session. Choose one method:

- Use Violentmonkey's **Activate activity on this page** menu.
- Replace the current URL fragment with `#ai-activity=` followed by
  percent-encoded JSON
  `{"agent":"Codex","session":"SESSION_ID","hash":"ORIGINAL_FRAGMENT"}`.
  Include the original `#` in `hash`, or use an empty string. The script
  restores the fragment.

Activation limits:

- Page scripts may briefly see the marker, which may affect fragment-based
  routing.
- Matching uses scheme, host, and path; query and fragment are ignored. Every
  activated tab for the same agent and session displays the matching update;
  the server does not track individual tab IDs. For example, if five tabs have
  the same page URL and are activated for the same agent and session, all five
  display that session's update, including tabs other than the one used to
  publish it. Tabs activated for another session, and tabs that are not
  activated, do not display that message.
- A hidden tab refreshes its update when it becomes visible again; it does not
  display a newly published update instantly while hidden.
- Activation is per document. Repeat it after reload or navigation.
- The script runs only on permitted top-level HTTP(S) pages, not restricted
  browser pages.
- Strict page CSP may block the stylesheet fallback in browsers without
  constructed stylesheet support.

Use `make status` and `make logs` to diagnose systemd. Run
`make systemd-uninstall` to stop the units and remove their files. This project
is licensed under the [GNU GPL v3](LICENSE).
