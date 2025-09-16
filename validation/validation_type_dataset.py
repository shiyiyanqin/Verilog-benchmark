
import json, re, hashlib, csv, subprocess, shutil
from pathlib import Path


DATASETS_DIR = Path(r"......")
OUT_DIR = Path(r"......")


MAX_PROMPT_TOKENS = 3500
MAX_PROMPT_BYTES = 120000
BUCKETS = ["B0", "B1", "B3"]



def read_jsonl(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line: continue
            yield json.loads(line)


def estimate_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def has_verilog_block(prompt: str) -> bool:

    if "```" in prompt and re.search(r"```(verilog|systemverilog|v)?", prompt, re.I):
        return True
    if re.search(r"\bmodule\s+\w+\s*\(", prompt):
        return True
    return False


def basic_schema_ok(record: dict) -> list:

    errs = []
    if "case_id" not in record or not record.get("case_id"):
        errs.append("missing_case_id")
    if "prompt" not in record or not record.get("prompt"):
        errs.append("missing_prompt")
    return errs


def print_table(headers, rows, title=None):
    if not rows:
        print(f"\n{title}\n-- No data to display --")
        return

    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))

    def line(char="-", jun="+"):
        return jun + jun.join(char * (w + 2) for w in widths) + jun

    def row_out(cells):
        return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, widths)) + " |"

    if title:
        print(title)
    print(line("=", "+"))
    print(row_out(headers))
    print(line("=", "+"))
    for r in rows:
        print(row_out(r))
    print(line("=", "+"))


def fmt_pct(num, den):
    if den <= 0: return "0.00%"
    return f"{(100.0 * num / den):.2f}%"


def validate():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUT_DIR / "verify_report_modified.jsonl"
    summary_path = OUT_DIR / "verify_summary_modified.csv"

    totals = {b: 0 for b in BUCKETS}
    ok_cnt = {b: 0 for b in BUCKETS}
    agg = {b: {"len_too_long": 0, "leak_code": 0, "schema_err": 0} for b in BUCKETS}

    with open(report_path, "w", encoding="utf-8") as rep:
        for b in BUCKETS:
            ds = DATASETS_DIR / f"dataset_{b}_Hardtest.jsonl"

            if not ds.exists():
                ds_alt = DATASETS_DIR / f"dataset_{b}_Simpletest.jsonl"
                if not ds_alt.exists():
                    print(f"[WARN] Files {ds} and {ds_alt} do not exist, skipping {b}")
                    continue
                ds = ds_alt

            print(f"[INFO] Processing file: {ds.name}")

            for rec in read_jsonl(ds):
                totals[b] += 1
                case_id = rec.get("case_id", "")
                prompt = rec.get("prompt", "")
                errs, warns = [], []

                schema_errs = basic_schema_ok(rec)
                if schema_errs:
                    errs += schema_errs
                    agg[b]["schema_err"] += 1

                toks = estimate_tokens(prompt)
                if toks > MAX_PROMPT_TOKENS or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
                    warns.append("prompt_too_long")
                    agg[b]["len_too_long"] += 1

                if has_verilog_block(prompt):
                    warns.append("verilog_leak_like")
                    agg[b]["leak_code"] += 1

                if not errs:
                    ok_cnt[b] += 1

                rep.write(json.dumps({
                    "bucket": b, "case_id": case_id,
                    "ok": len(errs) == 0, "errors": errs, "warnings": warns
                }, ensure_ascii=False) + "\n")

    csv_headers = ["Bucket", "Total", "OK", "OK_Rate", "LenTooLong", "LeakLike", "SchemaErr"]
    with open(summary_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(csv_headers)
        for b in BUCKETS:
            tot = totals[b];
            okn = ok_cnt[b]
            rate = fmt_pct(okn, tot)
            if tot > 0:
                w.writerow([b, tot, okn, rate,
                            agg[b]["len_too_long"],
                            agg[b]["leak_code"],
                            agg[b]["schema_err"]])

    rows = []
    table_headers = ["Bucket", "Total", "OK", "OK_Rate", "LenTooLong", "LeakLike", "SchemaErr"]
    for b in BUCKETS:
        tot = totals.get(b, 0)
        okn = ok_cnt.get(b, 0)
        if tot > 0:
            rows.append([
                b,
                str(tot),
                str(okn),
                fmt_pct(okn, tot),
                str(agg[b]["len_too_long"]),
                str(agg[b]["leak_code"]),
                str(agg[b]["schema_err"]),
            ])

    print()
    print_table(
        headers=table_headers,
        rows=rows,
        title="== Prompt Datasets Validation Summary (Modified Script) =="
    )
    print(f"\nThe detailed report has been generated.: {report_path}")
    print(f"The summary statistics have been generated.: {summary_path}")


if __name__ == "__main__":
    validate()