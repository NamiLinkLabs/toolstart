#!/bin/bash
# End-to-end test of the toolstart package using a throwaway GNUPGHOME + HOME.
set -euo pipefail
export PYTHONPATH="$(cd "$(dirname "$0")/.." && pwd)"
T=$(mktemp -d)
export HOME="$T/home" GNUPGHOME="$T/gnupg" SHELL=/bin/zsh
mkdir -p "$HOME" "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
export TS_CONFIG="$HOME/cfg.yaml.gpg"

gpg --batch --quiet --passphrase '' --quick-gen-key toolstart-test@example.com default default never 2>/dev/null

cat > "$T/plain.yaml" <<'EOF'
gpg_recipient: toolstart-test@example.com
update_check: false
env:
  TS_E2E_GLOBAL: from-global
  TS_E2E_VAR: global-should-be-overridden
tools:
  envtool:
    profiles:
      only:
        env: {TS_E2E_VAR: hello}
        cmd: [env]
  shtool:
    profiles:
      a:
        env: {X: "1"}
        cmd: "printf '%s|' \"$X\""
      b:
        env: {X: "2"}
        cmd: "printf '%s|' \"$X\""
  subtool:
    profiles:
      only:
        env:
          TOK: "$(echo --helper: sub)"
          MIX: "Bearer $(printf %s \"$TS_E2E_GLOBAL\") keep$LITERAL"
          FAIL: "$(exit 3)"
        cmd: [env]
EOF
gpg --batch --quiet --encrypt --recipient toolstart-test@example.com --output "$TS_CONFIG" "$T/plain.yaml"

echo "== list"; python3 -m toolstart list
echo "== get"; python3 -m toolstart get envtool only TS_E2E_VAR; echo
echo "== get missing (expect error)"; python3 -m toolstart get envtool only NOPE || true
echo "== hook single-profile list cmd"; python3 -m toolstart hook envtool | grep TS_E2E_VAR
echo "== hook: global env injected"; python3 -m toolstart hook envtool | grep 'TS_E2E_GLOBAL=from-global'
echo "== get: global fallback"; [ "$(python3 -m toolstart get envtool only TS_E2E_GLOBAL)" = "from-global" ] && echo ok
echo "== get: profile overrides global"; [ "$(python3 -m toolstart get envtool only TS_E2E_VAR)" = "hello" ] && echo ok
echo "== hook str cmd w/ quoted extra args"; python3 -m toolstart hook envtool sh -c 'printf "[%s]" "$@"' _ 'a b' 'c"d' ; echo
echo "== get: \$(cmd) substituted"; [ "$(python3 -m toolstart get subtool only TOK)" = "--helper: sub" ] && echo ok
echo "== get: embedded \$(cmd) sees global env, \$VAR literal"; [ "$(python3 -m toolstart get subtool only MIX)" = 'Bearer from-global keep$LITERAL' ] && echo ok
echo "== get: failing \$(cmd) (expect error)"; python3 -m toolstart get subtool only FAIL || true
echo "== malformed configs: pointed message, secret value never printed, no traceback"
bad() {  # encrypt $1 as a throwaway config, run `list` on it, print the output
  printf '%s\n' "$1" | gpg --batch --quiet --yes --encrypt --recipient toolstart-test@example.com --output "$T/bad.gpg"
  TS_CONFIG="$T/bad.gpg" python3 -m toolstart list 2>&1 || true
}
clean() { [[ "$1" != *do-not-print* && "$1" != *Traceback* ]]; }
out=$(bad $'tools:\n  t:\n    profiles:\n      p:\n        env:\n          SECRET:do-not-print'); echo "$out"
[[ "$out" == *"'t/p' \`env:\`"* && "$out" == *"Missing space after 'SECRET:'"* ]] && clean "$out" && echo ok
out=$(bad $'tools:\n  t:\n    profiles:\n      p:\n        env: SECRET=do-not-print'); echo "$out"
[[ "$out" == *"must be indented"* ]] && clean "$out" && echo ok
out=$(bad $'tools:\n  t:\n    profiles:\n      p: {cmd: {x: do-not-print}}'); echo "$out"
[[ "$out" == *"'t/p' \`cmd:\` must be a string or a list"* ]] && clean "$out" && echo ok
out=$(bad $'tools:\n  t: [\n  SECRET: do-not-print'); echo "$out"
[[ "$out" == *"invalid YAML in config at line"* ]] && clean "$out" && echo ok
echo "== install (first)"; python3 -m toolstart install
echo "== install (second, must not duplicate)"; python3 -m toolstart install >/dev/null
echo "-- rc file:"; cat "$HOME/.zshrc"
echo "block count: $(grep -c 'end toolstart hooks' "$HOME/.zshrc")"
echo "== zsh parses rc + function defined"; zsh -c "source $HOME/.zshrc; whence -w envtool"
echo "== save_config (what the editor's Save calls): encrypted via a pipe, hooks updated"
python3 - <<'EOF'
from toolstart import cli
cfg = cli.load_config()
cfg["tools"]["newtool"] = {"profiles": {"d": {"cmd": "newtool", "env": {"K": "v"}}}}
cli.save_config(cfg)
EOF
python3 -m toolstart list | grep -q newtool && echo ok
grep -q '^newtool()' "$HOME/.zshrc" && echo ok
[ ! -e "$TS_CONFIG.new" ] && echo ok
echo "== symmetric: passphrase asked by toolstart (twice to set), wrong one asked again"
TS_CONFIG="$T/sym.gpg" python3 - <<'EOF' && echo ok
import subprocess
from toolstart import cli
answers, asked = ["s3cret", "s3cret"], []
cli.write_config({"env": {}, "tools": {"t": {"profiles": {"p": {"cmd": "t", "env": {}}}}}},
                 ask=lambda p: (asked.append(p), answers.pop(0))[1])
subprocess.run(["gpgconf", "--reload", "gpg-agent"], check=True)
answers += ["wrong", "s3cret"]
assert list(cli.load_config(ask=lambda p: (asked.append(p), answers.pop(0))[1])["tools"]) == ["t"]
assert asked == ["New passphrase for the config:", "Repeat it:", "GPG passphrase:", "Wrong passphrase, try again:"], asked
EOF
echo "== perms: $(stat -f '%Lp' "$TS_CONFIG")"
echo "== leftover temp files: $(ls /tmp /var/folders 2>/dev/null | grep -c 'toolstart-\|toolstart-plain-' || true)"
echo "== bad cmd"; python3 -m toolstart bogus || true; python3 -m toolstart get a b || true
gpgconf --kill gpg-agent 2>/dev/null || true
rm -rf "$T"
echo ALL DONE
