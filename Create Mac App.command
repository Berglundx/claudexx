#!/bin/bash
# Run once (double-click) on a Mac. Creates "C4D Render Guard.app" in this folder:
# open it, or drag .c4d files onto its icon (or its Dock icon) to render them.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || true)"
if [ -z "$PY" ]; then
  osascript -e 'display alert "Python 3 is needed" message "Install Python 3 from python.org first, then run this again."'
  exit 1
fi
APP="$DIR/C4D Render Guard.app"
SCRIPT="$(mktemp -t c4dguard).applescript"
cat > "$SCRIPT" <<APPLESCRIPT
property guardDir : "$DIR"
property py : "$PY"

on launchGuard(args)
	do shell script "cd " & quoted form of guardDir & " && " & quoted form of py & " -m c4dguard gui" & args & " > /dev/null 2>&1 &"
end launchGuard

on run
	launchGuard("")
end run

on open theFiles
	set args to ""
	repeat with f in theFiles
		set args to args & " " & quoted form of (POSIX path of f)
	end repeat
	launchGuard(args)
end open
APPLESCRIPT
rm -rf "$APP"
osacompile -o "$APP" "$SCRIPT"
rm -f "$SCRIPT"
echo "Created: $APP"
echo "Tip: drag it into your Dock or Applications folder (keep this folder where it is)."
open -R "$APP"
