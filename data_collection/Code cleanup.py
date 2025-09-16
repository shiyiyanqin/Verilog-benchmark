from pathlib import Path
import json, re, csv, sys

IN_PATH   = r"......\dataset.json"
OUT_PATH  = r"......\cleaned.json"
REPORT_CSV= r"......\clean_report1.csv"
CODE_FIELD= "output"
COMPRESS_BLANKS = False

BANNER_CHARS = r"/*=_\-\~#\.\+\|<>:;\\"
def banner_line_re(min_repeat=7):
    return re.compile(rf"^\s*([{BANNER_CHARS}])\1{{{min_repeat},}}\s*$")
MOSTLY_NON_ALNUM_RE = re.compile(r"^[^A-Za-z0-9]{20,}$")

def is_banner_line(line: str) -> bool:
    if "`timescale" in line: return False
    if banner_line_re().match(line): return True
    st = line.strip()
    if not st: return False
    if MOSTLY_NON_ALNUM_RE.match(st): return True
    if len(st) >= 16:
        punct = sum(1 for ch in st if not ch.isalnum())
        if punct / len(st) >= 0.8: return True
    return False

def strip_banners(text: str) -> tuple[str,int]:
    removed = 0
    out = []
    for ln in text.splitlines():
        if is_banner_line(ln):
            removed += 1
            continue
        out.append(ln)
    s = "\n".join(out)
    if COMPRESS_BLANKS:
        s = re.sub(r"\n{3,}", "\n\n", s)
    return s, removed

def read_field(obj, dotted):
    cur = obj
    for k in dotted.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur

def write_field(obj, dotted, val):
    cur = obj
    parts = dotted.split(".")
    for k in parts[:-1]:
        if k not in cur or not isinstance(cur[k], dict):
            cur[k] = {}
        cur = cur[k]
    cur[parts[-1]] = val

def is_probably_csv(sample: str) -> bool:
    head = sample.strip().splitlines()[0] if sample else ""
    return head.lower().startswith("idx,decision") or head.lower().startswith("decision,")

def try_load_json_array(p: Path):
    try:
        with p.open("r", encoding="utf-8", errors="ignore") as f:
            return json.load(f)
    except Exception:
        return None

def process_items(items):
    kept = 0; removed_total = 0
    rows = [["idx","decision","removed_banner_lines","reason"]]
    with Path(OUT_PATH).open("w", encoding="utf-8") as fo:
        for i, obj in enumerate(items):
            if not isinstance(obj, dict):
                rows.append([i,"kept_raw",0,"not-object"])
                fo.write(json.dumps(obj, ensure_ascii=False)+"\n")
                continue
            code = read_field(obj, CODE_FIELD)
            if isinstance(code, str):
                new_code, removed = strip_banners(code)
                write_field(obj, CODE_FIELD, new_code)
                removed_total += removed
                kept += 1
                rows.append([i,"kept_cleaned",removed,""])
            else:
                rows.append([i,"kept_raw",0,"field-missing-or-not-str"])
            fo.write(json.dumps(obj, ensure_ascii=False)+"\n")
    with Path(REPORT_CSV).open("w", newline="", encoding="utf-8") as fcsv:
        csv.writer(fcsv).writerows(rows)
    print(f"[DONE] cleaned={kept} removed_banner_lines={removed_total}")
    print(f"[REPORT] {REPORT_CSV}")

def main():
    pin = Path(IN_PATH)
    text = pin.read_text(encoding="utf-8", errors="ignore")
    if is_probably_csv(text[:4096]):
        print(f"[ERROR] Input appears to be CSV (possibly a report file): {IN_PATH}")
        print("       Please provide the original data (JSONL or JSON array)."); sys.exit(1)

    valid = 0; total=0
    objs = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln: continue
        total += 1
        try:
            obj = json.loads(ln)
            objs.append(obj); valid += 1
        except Exception:
            objs.append(None)
    if total>0 and valid/total >= 0.8:
        print(f"[INFO] Identified as JSONL ({valid}/{total} parsable)")
        def gen():
            with pin.open("r", encoding="utf-8", errors="ignore") as fi:
                for ln in fi:
                    ln = ln.strip()
                    if not ln: continue
                    try:
                        yield json.loads(ln)
                    except Exception:
                        yield {"__raw__": ln}
        process_items(gen()); return

    arr = try_load_json_array(pin)
    if isinstance(arr, list):
        print(f"[INFO] Identified as JSON array, total {len(arr)} items")
        process_items(iter(arr)); return

    print(f"[ERROR] Unable to identify file format (neither JSONL nor JSON array): {IN_PATH}")
    print("       If this is a report CSV, please provide the original data; if it's a large object, convert it to JSONL first.")
    sys.exit(2)

if __name__ == "__main__":
    main()