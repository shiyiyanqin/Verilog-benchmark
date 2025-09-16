from __future__ import annotations

import os, re, json, csv, time, argparse, hashlib, threading, sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
from collections import Counter


try:
    from openai import OpenAI
    from openai import APIConnectionError, APITimeoutError, RateLimitError, APIStatusError
except ImportError:
    print("ERROR: Please install OpenAI SDK: pip install openai")
    sys.exit(1)

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-coder"

api_key = "xxxxxxxxxx"

DEFAULT_INPUT_DIR = Path(r"xxxxxxxxx")
DEFAULT_OUT_ROOT = Path(r"xxxxxxxxxxxxxx")

LEVEL_MAP = {
    "Simpletest": "Simple",
    "Mediumtest": "Medium",
    "Hardtest": "Hard",
}
BUCKETS = ["B0", "B1", "B3"]

TIMEOUT_MAP = {
    "Simple": 60,
    "Medium": 120,
    "Hard": 180,
}
def setup_logging(log_dir: Path) -> logging.Logger:
    log_file = log_dir / f"generation_{time.strftime('%Y%m%d_%H%M%S')}.log"

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ],
        force=True
    )
    return logging.getLogger(__name__)


def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8", errors="ignore")).hexdigest()


def read_jsonl(path: Path, logger: logging.Logger):
    if not path.exists():
        logger.error(f"File does not exist: {path}")
        return
    try:
        count = 0
        with path.open("r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                    count += 1
                except json.JSONDecodeError as e:
                    logger.warning(f"JSONL decode error at {path} line {i}: {e}")
                    continue
        logger.info(f"Loaded {count} records from {path.name}")
    except Exception as e:
        logger.error(f"Failed to read file {path}: {e}")


def sanitize_filename(s: str) -> str:
    return re.sub(r"[^\w\-.]+", "_", s)


def clean_case_id_for_filename(case_id: str) -> str:

    prefixes_to_remove = ["Simpletest_", "Mediumtest_", "Hardtest_"]
    for prefix in prefixes_to_remove:
        if case_id.startswith(prefix):
            case_id = case_id[len(prefix):]
            break

    suffixes_to_remove = ["_singal_dataset", "_signal_dataset", "_dataset"]
    for suffix in suffixes_to_remove:
        if case_id.endswith(suffix):
            case_id = case_id[:-len(suffix)]
            break

    return sanitize_filename(case_id)
def scan_existing_files(out_root: Path, logger: logging.Logger) -> Dict[str, Set[str]]:

    existing = {}

    for bucket in BUCKETS:
        for level in LEVEL_MAP.values():
            bucket_level = f"{bucket}_{level}"
            existing[bucket_level] = set()

            out_dir = out_root / bucket / level
            if out_dir.exists():
                v_files = list(out_dir.glob("*.v"))
                for v_file in v_files:
                    filename_no_ext = v_file.stem
                    existing[bucket_level].add(filename_no_ext)

                logger.info(f"Found {len(v_files)} existing .v files in {bucket}/{level}")

    total_existing = sum(len(files) for files in existing.values())
    logger.info(f"Total existing .v files: {total_existing}")
    return existing


def should_skip_task(case_id: str, bucket: str, level: str, existing_files: Dict[str, Set[str]],
                     logger: logging.Logger) -> bool:

    bucket_level = f"{bucket}_{level}"
    if bucket_level not in existing_files:
        return False

    expected_filename = clean_case_id_for_filename(case_id)

    if expected_filename in existing_files[bucket_level]:
        logger.debug(f"Skipping {case_id} - file already exists: {expected_filename}.v")
        return True

    return False

def strip_code_fences(text: str) -> str:
    if not text:
        return ""
    code_blocks = re.findall(
        r"```(?:verilog|systemverilog|v)\s*([\s\S]*?)```",
        text,
        flags=re.IGNORECASE
    )
    if code_blocks:
        return "\n\n".join(x.strip() for x in code_blocks if x.strip())
    code_blocks = re.findall(r"```\s*([\s\S]*?)```", text)
    if code_blocks:
        return "\n\n".join(x.strip() for x in code_blocks if x.strip())
    return text.strip()


def extract_verilog_module(text: str) -> Optional[str]:
    if not text:
        return None
    code = strip_code_fences(text)
    candidates = []
    for src in [code, text]:
        modules = re.findall(r"(?is)\bmodule\b[\s\S]*?\bendmodule\b", src)
        for m in modules:
            m = m.strip()
            if m:
                candidates.append(m)
    if not candidates:
        return None

    return candidates[0]


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def test_api_simple(client: OpenAI, model: str, logger: logging.Logger) -> bool:
    try:
        logger.info(f"Testing API with model: {model}")
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Say 'API test OK'"}],
            temperature=0.0,
            max_tokens=10,
            timeout=30
        )
        if resp.choices and resp.choices[0].message.content:
            logger.info(f"✓ API test successful: {resp.choices[0].message.content.strip()}")
            return True
        else:
            logger.error("✗ API test failed: empty response")
            return False
    except Exception as e:
        logger.error(f"✗ API test failed: {e}")
        return False


def call_api(client: OpenAI, model: str, prompt_text: str,
             temperature: float, max_tokens: int, timeout: int, logger: logging.Logger) -> Tuple[str, Dict[str, Any]]:
    try:
        logger.debug(f"Calling API with prompt length: {len(prompt_text)}, timeout: {timeout}")

        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            timeout=timeout
        )

        if not resp.choices:
            logger.error("API returned empty choices")
            return "failed", {"error_message": "Empty choices in API response"}

        if not resp.choices[0].message:
            logger.error("API returned empty message")
            return "failed", {"error_message": "Empty message in API response"}

        if not resp.choices[0].message.content:
            logger.error("API returned empty content")
            return "failed", {"error_message": "Empty content in API response"}

        logger.debug(f"API call successful, response length: {len(resp.choices[0].message.content)}")
        return "success", {"response": resp}

    except RateLimitError as e:
        logger.warning(f"Rate limit hit: {e}")
        return "rate_limited", {"error_message": str(e)}
    except APITimeoutError as e:
        logger.warning(f"API timeout: {e}")
        return "timeout", {"error_message": str(e)}
    except APIConnectionError as e:
        logger.error(f"API connection error: {e}")
        return "connection_error", {"error_message": str(e)}
    except APIStatusError as e:
        logger.error(f"API status error {e.status_code}: {e}")
        if e.status_code == 401:
            return "auth_error", {"error_code": e.status_code, "error_message": f"Authentication failed: {e}"}
        elif e.status_code == 400:
            return "bad_request", {"error_code": e.status_code, "error_message": f"Bad request: {e}"}
        else:
            return "failed", {"error_code": e.status_code, "error_message": str(e)}
    except Exception as e:
        logger.error(f"Unexpected API error: {e}")
        return "error", {"error_message": str(e)}


def extract_text_from_response(resp) -> str:
    try:
        return resp.choices[0].message.content
    except Exception:
        return str(resp)


def worker(item: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    logger = ctx["logger"]
    case_id = item.get("case_id") or f"unknown:{md5(json.dumps(item, ensure_ascii=False))}"
    prompt = item.get("prompt", "")
    bucket = item.get("_bucket")
    level = item.get("_level")

    out_dir_bucket = ctx["out_root"] / bucket / level
    raw_text_dir = ctx["raw_text_dir"] / bucket / level
    ensure_dir(out_dir_bucket)
    if ctx["save_text"]:
        ensure_dir(raw_text_dir)

    tries = 0
    while tries < ctx["max_retries"]:
        tries += 1
        timeout = TIMEOUT_MAP.get(level, ctx["timeout"])

        logger.info(f"Processing {case_id} (attempt {tries}/{ctx['max_retries']})")

        status, payload = call_api(
            client=ctx["client"],
            model=ctx["model"],
            prompt_text=prompt,
            temperature=ctx["temperature"],
            max_tokens=ctx["max_tokens"],
            timeout=timeout,
            logger=logger
        )

        out_obj = {"case_id": case_id, "bucket": bucket, "level": level, "status": status}
        out_obj.update(payload)

        if status == "success":
            text = extract_text_from_response(payload["response"])
            out_obj["text_len"] = len(text)

            if ctx["save_text"]:
                txt_path = raw_text_dir / f"{sanitize_filename(case_id)}.txt"
                txt_path.write_text(text, encoding="utf-8")
                out_obj["raw_text"] = str(txt_path)

            verilog = extract_verilog_module(text)
            if verilog:
                clean_filename = clean_case_id_for_filename(case_id)
                vpath = out_dir_bucket / f"{clean_filename}.v"
                vpath.write_text(verilog, encoding="utf-8")
                out_obj["verilog_path"] = str(vpath)
                out_obj["verilog_chars"] = len(verilog)
                logger.info(f"✓ Generated Verilog for {case_id}: {len(verilog)} chars")
            else:
                out_obj["verilog_path"] = None
                out_obj["verilog_chars"] = 0
                logger.warning(f"⚠ No Verilog found for {case_id}")

            time.sleep(ctx["sleep"])
            return out_obj


        if status == "auth_error":
            logger.error(f"✗ Authentication failed for {case_id}, stopping retries")
            return out_obj

        if status in ["rate_limited", "timeout", "connection_error"] and tries < ctx["max_retries"]:
            wait = ctx["sleep"] * (2 ** (tries - 1))
            if status == "rate_limited":
                wait = max(wait, 60)
            logger.info(f"↻ Retrying {case_id} in {wait}s (reason: {status})")
            time.sleep(wait)
            continue
        else:
            error_msg = payload.get('error_message', '')[:120]
            logger.error(f"✗ Failed {case_id} after {tries} attempts: {status} - {error_msg}")
            time.sleep(ctx["sleep"])
            return out_obj

    return out_obj

def scan_dataset_files(input_dir: Path, logger: logging.Logger, target_buckets: List[str] = None) -> List[Path]:
    buckets_to_scan = target_buckets if target_buckets else BUCKETS
    found_files = []
    missing_files = []

    for bucket in buckets_to_scan:
        for level_key in LEVEL_MAP.keys():
            expected_file = input_dir / f"dataset_{bucket}_{level_key}.jsonl"
            if expected_file.exists():
                found_files.append(expected_file)
                logger.info(f"✓ Found dataset: {expected_file.name}")
            else:
                missing_files.append(expected_file)
                logger.warning(f"✗ Missing dataset: {expected_file.name}")

    logger.info(f"Dataset scan complete: {len(found_files)} found, {len(missing_files)} missing")

    if missing_files:
        logger.info("Missing files:")
        for mf in missing_files:
            logger.info(f"  - {mf}")

    return found_files


def detect_remaining_work(input_dir: Path, out_root: Path, logger: logging.Logger) -> List[str]:
    remaining_buckets = []

    for bucket in BUCKETS:
        bucket_incomplete = False

        for level_key, level_name in LEVEL_MAP.items():
            input_file = input_dir / f"dataset_{bucket}_{level_key}.jsonl"
            if not input_file.exists():
                logger.debug(f"Input file not found: {input_file.name}")
                continue

            input_count = 0
            for _ in read_jsonl(input_file, logger):
                input_count += 1

            output_dir = out_root / bucket / level_name
            existing_count = 0
            if output_dir.exists():
                existing_count = len(list(output_dir.glob("*.v")))

            logger.info(f"{bucket}-{level_name}: {existing_count}/{input_count} completed")

            if existing_count < input_count:
                bucket_incomplete = True

        if bucket_incomplete:
            remaining_buckets.append(bucket)
            logger.info(f"✓ {bucket} needs processing")
        else:
            logger.info(f"✓ {bucket} is complete")

    return remaining_buckets


def load_all_tasks(dataset_files: List[Path], logger: logging.Logger) -> List[Dict[str, Any]]:
    tasks = []

    for dataset_file in dataset_files:
        filename = dataset_file.name
        parts = filename.replace("dataset_", "").replace(".jsonl", "").split("_")
        if len(parts) >= 2:
            bucket = parts[0]
            level_key = "_".join(parts[1:])
            level_name = LEVEL_MAP.get(level_key, level_key)
        else:
            logger.error(f"Cannot parse bucket/level from filename: {filename}")
            continue

        file_task_count = 0
        for rec in read_jsonl(dataset_file, logger):
            if not isinstance(rec, dict):
                continue
            case_id = rec.get("case_id")
            prompt = rec.get("prompt", "")
            if not (case_id and prompt):
                logger.warning(f"Skipping record with missing case_id or prompt in {filename}")
                continue

            rec["_bucket"] = bucket
            rec["_level"] = level_name
            rec["_source_file"] = str(dataset_file)
            tasks.append(rec)
            file_task_count += 1

        logger.info(f"Loaded {file_task_count} tasks from {filename} ({bucket}-{level_name})")

    logger.info(f"Total tasks loaded: {len(tasks)}")
    return tasks


def filter_tasks_for_resume(tasks: List[Dict[str, Any]], existing_files: Dict[str, Set[str]],
                            logger: logging.Logger) -> Tuple[List[Dict[str, Any]], int]:
    filtered_tasks = []
    skipped_count = 0

    for task in tasks:
        case_id = task.get("case_id")
        bucket = task.get("_bucket")
        level = task.get("_level")

        if should_skip_task(case_id, bucket, level, existing_files, logger):
            skipped_count += 1
            continue

        filtered_tasks.append(task)

    logger.info(f"Resume filtering: {len(tasks)} total -> {len(filtered_tasks)} remaining, {skipped_count} skipped")
    return filtered_tasks, skipped_count

def main():
    ap = argparse.ArgumentParser(description="Generate Verilog (.v) from structured prompts via DeepSeek-compatible API.")
    ap.add_argument("--input-dir", default=str(DEFAULT_INPUT_DIR),
                    help="Directory containing dataset_B{0,1,3}_{Simpletest,Mediumtest,Hardtest}.jsonl")
    ap.add_argument("--out-root", default=str(DEFAULT_OUT_ROOT),
                    help="Output root directory (will create B0/B1/B3/<Level> folders with .v files)")

    ap.add_argument("--base-url", default=DEFAULT_BASE_URL, help="OpenAI SDK base_url (DeepSeek compatible)")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Model name")
    ap.add_argument("--api-key", default=None, help=f"API key (or set env {ENV_API_KEY_NAME})")
    ap.add_argument("--temperature", type=float, default=0.1)
    ap.add_argument("--max_tokens", type=int, default=4096)
    ap.add_argument("--timeout", type=int, default=180)

    ap.add_argument("--buckets", nargs="+", choices=BUCKETS,
                    help="Target buckets to process (e.g., --buckets B3). If omitted, auto-detect remaining work.")
    ap.add_argument("--workers", type=int, default=5, help="Max worker threads")
    ap.add_argument("--sleep", type=float, default=2.0, help="Base sleep seconds between tasks")
    ap.add_argument("--max-retries", type=int, default=3, help="Max retries per task")
    ap.add_argument("--resume", action="store_true", help="Skip items with existing .v output")
    ap.add_argument("--test-only", action="store_true", help="Only test API connectivity")
    ap.add_argument("--debug", action="store_true", help="Enable debug logging")
    ap.add_argument("--list-files", action="store_true", help="List dataset files and completion status, then exit")
    args = ap.parse_args()

    print("=" * 60)
    print("[API MODE] gen_verilog_from_prompts_fixed.py")
    print(">>> Generating Verilog (.v) from LLM API <<<")
    print(f"input_dir       = {Path(args.input_dir).resolve()}")
    print(f"out_root        = {Path(args.out_root).resolve()}")
    print(f"model           = {args.model}")
    print(f"workers         = {args.workers}")
    print(f"max_tokens      = {args.max_tokens}")
    print(f"resume mode     = {args.resume}")
    print(f"target buckets  = {args.buckets or 'auto-detect'}")
    print("=" * 60)

    global api_key
    api_key = args.api_key or api_key
    if not api_key:
        print(f"ERROR: API key is required. Set --api-key or env {ENV_API_KEY_NAME}.")
        return

    input_dir = Path(args.input_dir).resolve()
    out_root = Path(args.out_root).resolve()

    if not input_dir.exists():
        print(f"ERROR: Input directory does not exist: {input_dir}")
        return

    out_root.mkdir(parents=True, exist_ok=True)
    log_dir = out_root / "_logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = setup_logging(log_dir)
    if args.debug:
        logger.setLevel(logging.DEBUG)

    if args.buckets:
        buckets_to_process = args.buckets
        logger.info(f"User specified buckets: {buckets_to_process}")
    else:
        logger.info("Auto-detecting remaining work...")
        buckets_to_process = detect_remaining_work(input_dir, out_root, logger)
        if not buckets_to_process:
            logger.info("All buckets are complete! No work remaining.")
            return
        logger.info(f"Auto-detected remaining buckets: {buckets_to_process}")

    logger.info(f"Scanning dataset files for buckets: {buckets_to_process}")
    dataset_files = scan_dataset_files(input_dir, logger, buckets_to_process)
    if not dataset_files:
        logger.error("No dataset files found!")
        return

    if args.list_files:
        logger.info("=== DATASET STATUS OVERVIEW ===")
        for bucket in BUCKETS:
            logger.info(f"\nBucket {bucket}:")
            for level_key, level_name in LEVEL_MAP.items():
                input_file = input_dir / f"dataset_{bucket}_{level_key}.jsonl"
                if input_file.exists():
                    input_count = sum(1 for _ in read_jsonl(input_file, logger))
                    output_dir = out_root / bucket / level_name
                    existing_count = len(list(output_dir.glob("*.v"))) if output_dir.exists() else 0
                    status = "✓ Complete" if existing_count >= input_count else f"{existing_count}/{input_count}"
                    logger.info(f"  {level_name:8}: {status}")
                else:
                    logger.info(f"  {level_name:8}: Input file missing")
        logger.info("List-files mode, exiting.")
        return

    logger.info("Loading tasks from dataset files...")
    all_tasks = load_all_tasks(dataset_files, logger)
    if not all_tasks:
        logger.error("No tasks loaded from dataset files!")
        return

    if args.resume:
        logger.info("Resume mode enabled - scanning existing files...")
        existing_files = scan_existing_files(out_root, logger)
        tasks, skipped_count = filter_tasks_for_resume(all_tasks, existing_files, logger)
        if not tasks:
            logger.info("All tasks already completed! Nothing to do.")
            return
        logger.info(f"Resume summary: {skipped_count} files already exist, {len(tasks)} tasks to process")
    else:
        tasks = all_tasks
        logger.info(f"Full run mode: {len(tasks)} tasks to process")

    try:
        client = OpenAI(api_key=api_key, base_url=args.base_url)
        logger.info("OpenAI client initialized.")
    except Exception as e:
        logger.error(f"Failed to initialize OpenAI client: {e}")
        return

    logger.info("=" * 50)
    logger.info("PRECHECK: Testing API connection...")
    if not test_api_simple(client, args.model, logger):
        logger.error("API precheck failed. Check API key / balance / model name / network.")
        return
    else:
        logger.info("✓ API precheck passed")

    if args.test_only:
        logger.info("Test-only mode, exiting.")
        return

    logger.info("=" * 50)

    ts = time.strftime("%Y%m%d_%H%M%S")
    out_jsonl = (out_root / "_logs" / f"results_{ts}.jsonl")
    out_csv = (out_root / "_logs" / f"results_{ts}.csv")

    ctx = {
        "client": client,
        "model": args.model,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "timeout": args.timeout,
        "sleep": args.sleep,
        "max_retries": args.max_retries,
        "out_root": out_root,
        "logger": logger,
    }

    fout = out_jsonl.open("w", encoding="utf-8", newline="")
    csv_f = out_csv.open("w", encoding="utf-8", newline="")
    csvw = csv.writer(csv_f)
    csvw.writerow([
        "bucket", "level", "case_id", "status",
        "verilog_path", "verilog_chars", "text_len",
        "error_code", "error_message"
    ])

    wrote = 0
    success_count = 0
    status_counts = Counter()
    lock = threading.Lock()

    start_time = time.time()
    logger.info(f"Starting processing with {args.workers} workers...")

    task_distribution = Counter()
    for task in tasks:
        bucket_level = f"{task.get('_bucket', 'unknown')}_{task.get('_level', 'unknown')}"
        task_distribution[bucket_level] += 1
    logger.info("Task distribution:")
    for bucket_level, count in sorted(task_distribution.items()):
        logger.info(f"  {bucket_level}: {count} tasks")

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(worker, it, ctx) for it in tasks]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                res = fut.result()
            except Exception as e:
                res = {"bucket": None, "level": None, "case_id": None, "status": "exception", "error_message": str(e)}
                logger.error(f"Worker exception: {str(e)}")

            with lock:
                fout.write(json.dumps(res, ensure_ascii=False) + "\n")
                fout.flush()
                csvw.writerow([
                    res.get("bucket", ""), res.get("level", ""), res.get("case_id", ""),
                    res.get("status", ""),
                    res.get("verilog_path", ""), res.get("verilog_chars", ""), res.get("text_len", ""),
                    res.get("error_code", ""), res.get("error_message", "")
                ])
                csv_f.flush()
                wrote += 1

                if res.get("status") == "success":
                    success_count += 1

                status_counts[res.get("status", "<none>")] += 1

            if i <= 10 or i % 20 == 0:
                elapsed = time.time() - start_time
                rate = i / elapsed * 60 if elapsed > 0 else 0
                remaining = len(futs) - i
                eta_min = (remaining / rate) if rate > 0 else 0
                logger.info(f"Progress: {i}/{len(futs)} ({i / len(futs) * 100:.1f}%) | "
                            f"Success: {success_count} | Rate: {rate:.1f}/min | ETA: {eta_min:.1f} min")
            if i % 100 == 0:
                logger.info(f"Status counts: {dict(status_counts)}")

    fout.close()
    csv_f.close()

    elapsed_total = time.time() - start_time
    logger.info("=" * 60)
    logger.info("FINAL RESULTS:")
    logger.info(f"  Total queued: {len(tasks)}")
    logger.info(f"  Completed: {wrote}")
    logger.info(f"  Successful: {success_count}")
    logger.info(f"  Failed: {wrote - success_count}")
    logger.info(f"  Success rate: {success_count / len(tasks) * 100:.1f}%")
    logger.info(f"  Total time: {elapsed_total / 60:.1f} minutes")
    logger.info(f"  Average rate: {len(tasks) / (elapsed_total / 60):.1f} tasks/minute")
    logger.info(f"Status breakdown: {dict(status_counts)}")
    logger.info("Results saved to:")
    logger.info(f"  JSONL: {out_jsonl}")
    logger.info(f"  CSV: {out_csv}")
    logger.info(f"  Verilog files: {out_root}")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()