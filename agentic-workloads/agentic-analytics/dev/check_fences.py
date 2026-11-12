"""Simulate a participant uncommenting every fence in the workshop top-up template the way the
Code Editor does (Ctrl+/ strips '# ' or '#' from each selected line), then check that each
fence's top-level keys land under Resources (or Outputs/Conditions) and the YAML parses.
Usage: python3 dev/check_fences.py [template]"""
import re, sys, yaml

class L(yaml.SafeLoader): pass
L.add_multi_constructor("!", lambda l, s, n: None)
path = sys.argv[1] if len(sys.argv) > 1 else "workshop/code/app/agentcore_strands/agentcore-topup-stack.yaml"
lines = open(path).read().split("\n")
out, inside, fences, bad = [], None, [], []
for i, ln in enumerate(lines, 1):
    m = re.search(r"UNCOMMENT FROM HERE \((Step [^:)]+)", ln)
    if m: inside = m.group(1); fences.append((inside, i)); out.append(ln); continue
    if "UNCOMMENT TO HERE" in ln: inside = None; out.append(ln); continue
    if inside and ln.lstrip().startswith("#"):
        s = re.sub(r"^(\s*)# ?", r"\1", ln, count=1)
        if re.match(r"^[A-Za-z]", s): bad.append((inside, i, s[:60]))
        ln = s
    out.append(ln)
try:
    yaml.load("\n".join(out), Loader=L)  # nosec B506 - L is a SafeLoader that only ignores CFN tags
    print("YAML parses with all fences uncommented")
except yaml.YAMLError as e:
    print("YAML ERROR:", str(e)[:300]); bad.append(("parse", 0, ""))
print("fences:", ", ".join(f"{n}@{i}" for n, i in fences))
for b in bad: print("TOP-LEVEL after uncomment:", b)
sys.exit(1 if bad else 0)
