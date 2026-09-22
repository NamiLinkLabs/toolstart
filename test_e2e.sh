#!/bin/bash
# End-to-end test of toolstart.py using a throwaway GNUPGHOME + HOME.
set -euo pipefail
TS="$(cd "$(dirname "$0")" && pwd)/toolstart.py"
T=$(mktemp -d)
export HOME="$T/home" GNUPGHOME="$T/gnupg" SHELL=/bin/zsh
mkdir -p "$HOME" "$GNUPGHOME"; chmod 700 "$GNUPGHOME"
export TS_CONFIG="$HOME/cfg.yaml.gpg"

gpg --batch --quiet --passphrase '' --quick-gen-key toolstart-test@example.com default default never 2>/dev/null

cat > "$T/plain.yaml" <<'EOF'
gpg_recipient: toolstart-test@example.com
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

echo "== list"; python3 "$TS" list
echo "== get"; python3 "$TS" get envtool only TS_E2E_VAR; echo
echo "== get missing (expect error)"; python3 "$TS" get envtool only NOPE || true
echo "== hook single-profile list cmd"; python3 "$TS" hook envtool | grep TS_E2E_VAR
echo "== hook: global env injected"; python3 "$TS" hook envtool | grep 'TS_E2E_GLOBAL=from-global'
echo "== get: global fallback"; [ "$(python3 "$TS" get envtool only TS_E2E_GLOBAL)" = "from-global" ] && echo ok
echo "== get: profile overrides global"; [ "$(python3 "$TS" get envtool only TS_E2E_VAR)" = "hello" ] && echo ok
echo "== hook str cmd w/ quoted extra args"; python3 "$TS" hook envtool sh -c 'printf "[%s]" "$@"' _ 'a b' 'c"d' ; echo
echo "== get: \$(cmd) substituted"; [ "$(python3 "$TS" get subtool only TOK)" = "--helper: sub" ] && echo ok
echo "== get: embedded \$(cmd) sees global env, \$VAR literal"; [ "$(python3 "$TS" get subtool only MIX)" = 'Bearer from-global keep$LITERAL' ] && echo ok
echo "== get: failing \$(cmd) (expect error)"; python3 "$TS" get subtool only FAIL || true
echo "== install (first)"; python3 "$TS" install
echo "== install (second, must not duplicate)"; python3 "$TS" install >/dev/null
echo "-- rc file:"; cat "$HOME/.zshrc"
echo "block count: $(grep -c 'end toolstart hooks' "$HOME/.zshrc")"
echo "== zsh parses rc + function defined"; zsh -c "source $HOME/.zshrc; whence -w envtool"
echo "== edit: no change"; EDITOR=true python3 "$TS" edit
echo "== edit: add tool via editor script"
cat > "$T/ed.sh" <<'EOF'
#!/bin/bash
printf '  newtool:\n    profiles:\n      d:\n        env: {K: v}\n        cmd: newtool\n' >> "$1"
EOF
chmod +x "$T/ed.sh"
EDITOR="$T/ed.sh" python3 "$TS" edit
echo "-- rc after edit:"; grep -E '^\w+\(\)' "$HOME/.zshrc"
echo "== edit: editor keeps running after save (vscode-like)"
cat > "$T/slowed.sh" <<'EOF'
#!/bin/bash
printf '  slowtool:\n    profiles:\n      d:\n        env: {K: v}\n        cmd: slowtool\n' >> "$1"
sleep 30
EOF
chmod +x "$T/slowed.sh"
t0=$(date +%s)
EDITOR="$T/slowed.sh" python3 "$TS" edit
t1=$(date +%s)
[ $((t1 - t0)) -lt 15 ] && echo "did not block on lingering editor"
python3 "$TS" list | grep -q slowtool && echo "config saved"
pkill -f "slowed.sh" 2>/dev/null || true
echo "== edit: invalid yaml (expect NOT saved)"
cat > "$T/bad.sh" <<'EOF'
#!/bin/bash
printf ':: bad: [\n' >> "$1"
EOF
chmod +x "$T/bad.sh"
EDITOR="$T/bad.sh" python3 "$TS" edit || true
python3 "$TS" list | grep -q newtool && echo "config intact"
echo "== edit: editor from config key (EDITOR unset)"
cat > "$T/cfged.sh" <<'EOF'
#!/bin/bash
printf '# via config editor\n' >> "$1"
EOF
chmod +x "$T/cfged.sh"
cat > "$T/seted.sh" <<'EOF'
#!/bin/bash
sed -i '' "1s|^|editor: $CFGED\n|" "$1"
EOF
chmod +x "$T/seted.sh"
CFGED="$T/cfged.sh" EDITOR="$T/seted.sh" python3 "$TS" edit >/dev/null
env -u EDITOR python3 "$TS" edit >/dev/null
env -u EDITOR EDITOR_CHECK=1 python3 - "$TS_CONFIG" <<'EOF'
import subprocess,sys
out=subprocess.run(["gpg","--quiet","--batch","--decrypt",sys.argv[1]],capture_output=True,text=True).stdout
print("config editor used:", "via config editor" in out)
EOF
echo "== perms: $(stat -f '%Lp' "$TS_CONFIG")"
echo "== leftover temp files: $(ls /tmp /var/folders 2>/dev/null | grep -c 'toolstart-\|toolstart-plain-' || true)"
echo "== bad cmd"; python3 "$TS" bogus || true; python3 "$TS" get a b || true
gpgconf --kill gpg-agent 2>/dev/null || true
rm -rf "$T"
echo ALL DONE
