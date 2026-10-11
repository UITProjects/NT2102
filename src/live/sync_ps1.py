"""sync_ps1.py - copy src/live/etwship.py into the $Ship here-string of src/scripts/08-live-shipper.ps1.

etwship.py is the source of truth; the VM gets it embedded in the provisioner because an isolated VM
cannot read the repo.
    python src/live/sync_ps1.py           # rewrite the embedded copy
    python src/live/sync_ps1.py --check   # exit 1 if the copy is stale
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "etwship.py")
PS1 = os.path.join(os.path.dirname(HERE), "scripts", "08-live-shipper.ps1")

code = open(PY, encoding="utf-8").read().replace("\r\n", "\n").rstrip("\n")
if re.search(r"^'@", code, re.M):
    sys.exit("etwship.py contains a line starting with '@ which would end the PowerShell here-string")
ps1 = open(PS1, encoding="utf-8").read()
rx = re.compile(r"(^\$Ship = @'\n)(.*?)(\n'@$)", re.S | re.M)
m = rx.search(ps1)
if not m:
    sys.exit(f"no $Ship here-string in {PS1}")
if m.group(2) == code:
    print("08-live-shipper.ps1 is up to date")
    sys.exit(0)
if "--check" in sys.argv:
    sys.exit("08-live-shipper.ps1 embeds a STALE etwship.py; run: python src/live/sync_ps1.py")
open(PS1, "w", encoding="utf-8", newline="\n").write(ps1[:m.start(2)] + code + ps1[m.end(2):])
print(f"updated {PS1}")
