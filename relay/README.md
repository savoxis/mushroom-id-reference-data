# mushroom-log-relay

A narrow, self-hosted relay that lets the `westcoast-mushroom-id` skill push
find-log entries straight to GitHub from inside a Claude session, without
the skill ever holding a GitHub credential and without depending on
`api.github.com` or `git push` being reachable from wherever the skill
happens to be running that day.

See the module docstring in `mushroom_log_relay.py` for the full design
rationale. Short version: a Claude Cowork sandbox gates GitHub's own write
paths (`api.github.com`, `git push` to `github.com`) behind a session-level
repo-authorization step that a fresh chat has no way to satisfy -- there's
no persistent "workspace" carried between sessions. Generic outbound HTTPS
to an arbitrary third-party domain isn't gated the same way, so instead of
fighting GitHub's chokepoints, this service sits somewhere else (your
homelab) and is the only thing that ever actually talks to GitHub.

## What it does, and deliberately does not do

One endpoint, `POST /log-find`, that validates a find-log entry against a
strict field whitelist and commits it as one appended line to
`logs/finds_log.jsonl` on `main`. Nothing else. There is no route to touch
`references/species_registry.json` or any other file in the repo -- species
corrections stay a human-reviewed `git commit` on your end, on purpose.
Given the life-safety stakes of that database, "the skill can silently
rewrite the deadly-species list on its own" is not a feature this relay is
willing to offer, even in principle.

## One-time setup

1. **Create the GitHub PAT.** Fine-grained, scoped to only
   `savoxis/mushroom-identifier-dontusethisever`, `Contents: Read and
   write`, nothing else. Steps are in `.env.example`. This is the only
   thing in this whole setup that can actually write to your repo -- keep
   it out of git, out of logs, out of anywhere the Claude session can see.

2. **Generate a relay auth token.** `openssl rand -hex 32` or equivalent.
   This is what the skill actually holds -- a much lower-stakes secret
   scoped to "can call this one endpoint on this one relay," not "has
   write access to GitHub."

3. **Build and run the container.**
   ```
   cd relay
   cp .env.example .env    # fill in GITHUB_PAT and RELAY_AUTH_TOKEN
   docker compose up -d --build
   ```
   Or, if you're managing this through UnRAID's Community Apps /
   Docker Compose Manager the same way you likely already run Memory
   Vault, point it at this folder's `docker-compose.yml` and set the two
   secrets as container environment variables there instead of a `.env`
   file, whichever matches your existing pattern.

4. **Expose it over HTTPS.** This is the part that's genuinely yours to
   own, since I don't know your reverse proxy setup -- but you've already
   solved this exact problem once for `mv.yeylandwutani.com` (Memory
   Vault), so the shape is the same: point a subdomain (e.g.
   `mushroom-relay.yeylandwutani.com`) at this container the same way,
   terminate TLS at the proxy, and forward to `:8787` internally. A
   Claude session calling this later needs a plain `https://` URL that
   resolves and has a valid cert -- it has no way to reach anything on
   your LAN directly, and no way to trust a self-signed cert.

5. **Check it's alive:**
   ```
   curl https://mushroom-relay.yeylandwutani.com/healthz
   ```
   should return `{"ok": true, "configured": true}`. If `configured` is
   `false`, the container started without one or both secrets set --
   check the container's environment.

## Wiring the skill up to use it

The skill never needs to know this exists unless you want it to. Two
environment variables:

```
MUSHROOM_LOG_RELAY_URL=https://mushroom-relay.yeylandwutani.com
MUSHROOM_LOG_RELAY_TOKEN=<the RELAY_AUTH_TOKEN you generated above>
```

**Set these yourself, out of band, in whatever environment the skill runs
in -- never in SKILL.md, never anywhere in this repo.** An earlier version
of this setup had you paste the token value straight into SKILL.md so the
skill could "just have it." That turned out to be a mistake independent
of the consent question below: a live credential sitting in a document
that every session loads and reads is a bad practice on its own terms, and
a session that reads a live secret sitting next to instructions to use it
is going to (correctly) treat the whole file with more suspicion, whether
or not those instructions also say "but ask first." Keeping the value out
of any file Claude reads as instructions is the actual fix, not a nice-to-
have on top of the consent gate. Put it in a shell profile export, a
scheduled task's own environment variable settings, or however the
platform running this skill lets you set process environment for a
session -- whatever keeps it out of SKILL.md and out of git.

Even with both variables set in the environment, the skill still asks the
user in that session whether they want find logs pushed to GitHub or kept
local (see SKILL.md's "Relay credentials" section and workflow step 12 --
the skill asks once per session, before the first push, and only uses the
already-set variables if the answer is yes). This is intentionally gated
per session, not baked into the skill or the repo as an always-on default
-- a session correctly declining to export a credential and push location
data without asking first is the design working, not a bug. If you ever
see a session push without having asked you first, that's the thing to
report back and fix, not a convenience to restore.

`scripts/log_find.py` checks for both at runtime. If they're not set, it
behaves exactly as before -- local `logs/finds_log.jsonl` only, no network
call, no error. If they are set, it also POSTs the entry to the relay after
the local write succeeds, and reports whether that push actually landed.
Nothing about the local-write behavior changes either way, so a relay
outage, wrong token, or DNS hiccup degrades to "logged locally, ask them to
push by hand" rather than breaking the find-logging step.

## Threat model, briefly

- **RELAY_AUTH_TOKEN leaks** (e.g. a Claude session logs it somewhere, or
  a transcript gets shared): worst case, someone can spam schema-valid
  entries into your finds log. Annoying, cheap to clean up (it's one file,
  one git revert), not a compromise of the repo. This token is set only in
  the environment the skill runs in, per the "Wiring the skill up" section
  above -- it is deliberately kept out of SKILL.md and out of this repo,
  because a live secret embedded in a document every session loads is a
  broadly-distributed secret whether or not any given session ever uses
  it, and that turned out to cause real problems (sessions correctly
  treating the whole skill file as suspect). Keeping it in the environment
  instead means only sessions actually run in a context where you set it
  can see it at all. Rotating it periodically anyway is still cheap
  insurance, not paranoia, given how many different sessions and machines
  might end up with it in their environment over time.
- **GITHUB_PAT leaks**: this is the one that actually matters, and it
  never leaves your own infrastructure -- it's a container environment
  variable, never sent to or received from any Claude session, never
  logged by the relay itself. Rotate it the same way you'd rotate any
  other credential if you ever suspect the host itself was compromised;
  that's a different, bigger problem than anything this relay introduces.
- **Rate limiting** is in-memory and per-relay-process (resets on
  restart) -- it's there to blunt casual abuse if the auth token leaks,
  not a substitute for keeping the token secret in the first place.
