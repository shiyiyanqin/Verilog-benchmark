import json
import os
import time
import requests
from typing import Dict, List, Any, Optional
from datetime import datetime
import hashlib
from pathlib import Path
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from queue import Queue

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DeepSeekClient:

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        self._lock = threading.Lock()
        self._last_request_time = 0
        self._min_interval = 1.5

    def generate(self, prompt: str, temperature: float = 0.3, max_tokens: int = 800) -> str:

        with self._lock:
            current_time = time.time()
            time_since_last_request = current_time - self._last_request_time
            if time_since_last_request < self._min_interval:
                time.sleep(self._min_interval - time_since_last_request)
            self._last_request_time = time.time()

        payload = {
            "model": "deepseek-chat",
            "messages": [
                {
                    "role": "system",
                    "content": "You are an expert hardware engineer specializing in Verilog and digital circuit design. You analyze AST structures and provide accurate, technical descriptions."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            response = requests.post(
                self.base_url,
                headers=self.headers,
                json=payload,
                timeout=200
            )
            response.raise_for_status()

            result = response.json()
            return result['choices'][0]['message']['content']

        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None
        except KeyError as e:
            logger.error(f"Unexpected API response format: {e}")
            return None


class VerilogASTProcessor:

    def __init__(self, api_key: str, input_dir: str, output_dir: str):
        self.client = DeepSeekClient(api_key)
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.jsonl_output_dir = Path(output_dir) / "1"
        self.cache_file = self.output_dir / "cache.json"
        self.cache = self._load_cache()
        self.cache_lock = threading.Lock()
        self.results_queue = Queue()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_output_dir.mkdir(parents=True, exist_ok=True)

    def get_optimized_prompt(self, ast_data: Dict) -> str:
        module_info = self._extract_module_info(ast_data)

        prompt = f"""Analyze this Verilog module AST and generate a comprehensive technical description.

Module: {module_info['name']}
Type indication: {module_info['type_hint']}
Complexity: {module_info['complexity']}

AST Structure:
{json.dumps(ast_data, indent=2)}

Generate a description with these exact sections:

1. Overview (1 sentence): State the module name and primary function.

2. Functionality (2-3 sentences): Explain what the module does and how it operates.

3. Interface:
   - Inputs: List each input port with [bit width] and purpose
   - Outputs: List each output port with [bit width] and purpose

4. Implementation:
   - Logic type (combinational/sequential)
   - Key operations or algorithms
   - Special signals (clock, reset, enable) if present

5. Technical Characteristics:
   - Bit widths and data paths
   - Timing considerations
   - Resource implications

Use precise hardware terminology. Be specific about signal names, operations, and bit widths."""

        return prompt

    def _extract_module_info(self, ast_data: Dict) -> Dict:
        info = {
            "name": "unknown",
            "type_hint": "unknown",
            "complexity": "medium",
            "port_count": 0,
            "has_always": False,
            "has_clock": False
        }

        def traverse(node):
            if isinstance(node, dict):
                node_type = node.get("type", "")

                if node_type == "ModuleDef":
                    info["name"] = node.get("attributes", {}).get("name", "unknown")
                elif node_type in ["Input", "Output"]:
                    info["port_count"] += 1
                    port_name = node.get("attributes", {}).get("name", "").lower()
                    if "clk" in port_name or "clock" in port_name:
                        info["has_clock"] = True
                elif node_type == "Always":
                    info["has_always"] = True

                for child in node.get("children", []):
                    traverse(child)

        traverse(ast_data)

        name_lower = info["name"].lower()
        if "mux" in name_lower or "sel" in name_lower:
            info["type_hint"] = "multiplexer"
        elif "add" in name_lower:
            info["type_hint"] = "adder"
        elif "count" in name_lower:
            info["type_hint"] = "counter"
        elif "complement" in name_lower or "2s" in name_lower:
            info["type_hint"] = "arithmetic (2's complement)"
        elif info["has_clock"]:
            info["type_hint"] = "sequential circuit"
        elif info["has_always"]:
            info["type_hint"] = "complex combinational or sequential"
        else:
            info["type_hint"] = "combinational circuit"

        if info["port_count"] < 5 and not info["has_always"]:
            info["complexity"] = "simple"
        elif info["port_count"] > 10 or (info["has_always"] and info["has_clock"]):
            info["complexity"] = "complex"

        return info

    def process_single_file(self, ast_file: Path) -> Dict[str, Any]:
        thread_name = threading.current_thread().name
        logger.info(f"[{thread_name}] Processing: {ast_file.name}")

        file_hash = self._get_file_hash(ast_file)
        with self.cache_lock:
            if file_hash in self.cache:
                logger.info(f"[{thread_name}] Using cached result for: {ast_file.name}")
                return self.cache[file_hash]

        try:
            base_name = ast_file.stem.replace("_ast", "")

            with open(ast_file, 'r', encoding='utf-8') as f:
                ast_data = json.load(f)


            verilog_source_code = ast_data.get("source_code", None)
            source_file_name = ast_data.get("source_file", None)
            source_file_path = ast_data.get("source_path", None)

            if not verilog_source_code:
                logger.warning(f"[{thread_name}] No source code found in AST for: {ast_file.name}")

            prompt = self.get_optimized_prompt(ast_data)

            description = self.client.generate(prompt)

            if description:
                module_info = self._extract_module_info(ast_data)
                result = {
                    "id": base_name,
                    "source_file": ast_file.name,
                    "module_name": module_info["name"],
                    "description": "Analyze the AST and generate a technical description of the Verilog module",
                    "input": ast_data,
                    "output": description,
                    "verilog_source_code": verilog_source_code,
                    "metadata": {
                        "ast_file": str(ast_file),
                        "original_verilog_file": source_file_name,
                        "model": "deepseek-chat",
                        "prompt_length": len(prompt),
                        "has_source_code": verilog_source_code is not None,
                        "type_hint": module_info["type_hint"],
                    }
                }

                with self.cache_lock:
                    self.cache[file_hash] = result
                    self._save_cache()

                output_file = self.output_dir / f"{base_name}_singal_dataset.json"
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)

                logger.info(f"[{thread_name}] Saved individual result to: {output_file.name}")

                return result
            else:
                logger.error(f"[{thread_name}] Failed to generate description for: {ast_file.name}")
                return None

        except json.JSONDecodeError as e:
            logger.error(f"[{thread_name}] Invalid JSON in file {ast_file.name}: {e}")
            return None
        except Exception as e:
            logger.error(f"[{thread_name}] Error processing {ast_file.name}: {e}")
            return None

    def process_all_files_parallel(self, max_files: Optional[int] = None, max_workers: int = 5):
        ast_files = list(self.input_dir.glob("*_ast.json"))

        if not ast_files:
            logger.warning(f"No AST JSON files found in {self.input_dir}")
            return

        logger.info(f"Found {len(ast_files)} AST files to process")

        if max_files:
            ast_files = ast_files[:max_files]
            logger.info(f"Processing only first {max_files} files")

        results = []
        failed_count = 0

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_file = {
                executor.submit(self.process_single_file, ast_file): ast_file
                for ast_file in ast_files
            }

            for i, future in enumerate(as_completed(future_to_file), 1):
                ast_file = future_to_file[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        self.results_queue.put(result)
                    else:
                        failed_count += 1

                    logger.info(f"Progress: {i}/{len(ast_files)} files processed")

                except Exception as e:
                    logger.error(f"Exception processing {ast_file.name}: {e}")
                    failed_count += 1

        self._save_complete_dataset(results)
        self._save_jsonl_dataset(results)

        logger.info("Processing complete!")
        logger.info(f"Successfully processed: {len(results)} files")
        logger.info(f"Failed: {failed_count} files")
        logger.info("Output artifacts: individual JSONs and JSONL dataset (paths omitted from logs).")

    def _save_complete_dataset(self, results: List[Dict]):
        dataset = {
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "model": "deepseek-chat",
            "task": "verilog_ast_to_description",
            "total_samples": len(results),
            "samples": results
        }

        dataset_file = self.output_dir / "complete_dataset.json"
        with open(dataset_file, 'w', encoding='utf-8') as f:
            json.dump(dataset, f, ensure_ascii=False, indent=2)

        logger.info(f"Complete dataset saved to: {dataset_file}")

    def _save_jsonl_dataset(self, results: List[Dict]):
        jsonl_file = self.jsonl_output_dir / "full_dataset.jsonl"
        with open(jsonl_file, 'w', encoding='utf-8') as f:
            for result in results:
                jsonl_entry = {
                    "id": result["id"],
                    "module_name": result["module_name"],
                    "description": result["description"],
                    "input": result["input"],
                    "output": result["output"],
                    "verilog_source_code": result["verilog_source_code"],
                    "metadata": result["metadata"]
                }
                f.write(json.dumps(jsonl_entry, ensure_ascii=False) + '\n')

        logger.info(f"JSONL dataset saved to: {jsonl_file}")

        metadata_file = self.jsonl_output_dir / "dataset_metadata.json"
        metadata = {
            "created_at": datetime.now().isoformat(),
            "total_samples": len(results),
            "task": "verilog_ast_to_description",
            "data_file": "full_dataset.jsonl",
            "statistics": {
                "with_source_code": sum(1 for r in results if r.get("verilog_source_code")),
                "without_source_code": sum(1 for r in results if not r.get("verilog_source_code")),
                "module_types": self._count_module_types(results)
            }
        }

        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

        logger.info(f"Metadata saved to: {metadata_file}")

    def _count_module_types(self, results: List[Dict]) -> Dict[str, int]:
        type_counts = {}
        for result in results:
            module_type = result.get("metadata", {}).get("type_hint", "unknown")
            type_counts[module_type] = type_counts.get(module_type, 0) + 1
        return type_counts

    def _get_file_hash(self, file_path: Path) -> str:
        with open(file_path, 'rb') as f:
            return hashlib.md5(f.read()).hexdigest()

    def _load_cache(self) -> Dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load cache: {e}")
                return {}
        return {}

    def _save_cache(self):
        try:
            with open(self.cache_file, 'w') as f:
                json.dump(self.cache, f)
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")


def main():
    API_KEY = "xxxxxxxxxxx"
    INPUT_DIR = r"xxxxxxxxxxx"
    OUTPUT_DIR = r"xxxxxxxxxxx"

    MAX_WORKERS = 5

    processor = VerilogASTProcessor(
        api_key=API_KEY,
        input_dir=INPUT_DIR,
        output_dir=OUTPUT_DIR
    )

    processor.process_all_files_parallel(max_workers=MAX_WORKERS)

if __name__ == "__main__":
    main()