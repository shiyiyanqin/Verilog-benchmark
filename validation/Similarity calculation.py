import os
import json
import re
import time
import asyncio
import aiohttp
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass
import logging
import csv
import argparse

DEFAULT_INPUT_DIR = Path(r"......")
DEFAULT_OUT_ROOT = Path(r"......")

DEFAULT_V_DIR = DEFAULT_INPUT_DIR
DEFAULT_JSON_DIR = DEFAULT_INPUT_DIR

DEFAULT_OUTPUT = (DEFAULT_OUT_ROOT / "Mediumtest_results.csv").as_posix()

DEFAULT_API_KEY = "......"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class ComparisonResult:
    v_file: str
    json_file: str
    similarity: int
    confidence: float
    reason: str
    error: str = ""


class DeepSeekVerilogComparator:

    def __init__(self, api_key: str, base_url: str = "https://api.deepseek.com/v1"):
        self.api_key = api_key
        self.base_url = base_url
        self.max_retries = 3
        self.retry_delay = 1
        self.max_tokens_per_request = 6000

        self.comparison_prompt = """You are an expert digital circuit design verification engineer. Compare the **functional similarity** of the following two Verilog modules. Focus on whether they implement the same input-output behavior and functionality, ignoring differences in:

1. Code style and formatting
2. Comments and documentation
3. Variable naming conventions
4. Implementation details (e.g., state machine encoding, counter usage)
5. Parameter values (if they don't affect core functionality)
6. Presence of additional modules or code in one file

Consider the modules functionally similar if:
- They have the same primary inputs and outputs
- They implement the same core functionality
- They behave identically for the same inputs
- Minor implementation differences don't affect overall behavior

File 1:
```verilog
{v_content}

```

File 2:
```verilog
{json_content}
```

Please output strictly in JSON format:
{{
  "similarity": 0,
  "confidence": 0.95,
  "reason": "specific reason"
}}

Return only the above JSON, do not output any additional text."""

    def extract_module_from_content(self, content: str, module_name: str) -> Optional[str]:
        pattern = rf"module\s+{module_name}\s*(?:#\([^)]*\))?\s*\([^;]*\);?(.*?)endmodule"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        if match:
            return f"module {module_name}{match.group(0).split('module ' + module_name, 1)[1]}"

        pattern = rf"module\s+{module_name}\b.*?endmodule"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)

        return match.group(0) if match else None

    def read_file_content(self, file_path: str) -> Tuple[str, str]:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            if not content:
                return "", "File is empty"
            return content, ""
        except UnicodeDecodeError:
            try:
                with open(file_path, 'r', encoding='gbk') as f:
                    content = f.read().strip()
                return content, ""
            except Exception as e:
                return "", f"Encoding error: {str(e)}"
        except FileNotFoundError:
            return "", "File not found"
        except Exception as e:
            return "", f"File read error: {str(e)}"

    def truncate_content(self, content: str, max_length: int = 2000) -> str:
        if len(content) <= max_length:
            return content
        lines = content.split('\n')
        truncated_lines, current_length = [], 0
        for line in lines:
            if current_length + len(line) > max_length:
                truncated_lines.append("// ... content truncated ...")
                break
            truncated_lines.append(line)
            current_length += len(line) + 1
        return '\n'.join(truncated_lines)

    async def call_deepseek(self, session: aiohttp.ClientSession, v_content: str, json_content: str) -> Dict:
        v_content = self.truncate_content(v_content)
        json_content = self.truncate_content(json_content)
        prompt = self.comparison_prompt.format(v_content=v_content, json_content=json_content)

        payload = {
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.1,
            "top_p": 0.9,
            "stream": False
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        for attempt in range(self.max_retries):
            try:
                async with session.post(
                        f"{self.base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                        timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        result = await response.json()
                        content = result['choices'][0]['message']['content'].strip()
                        try:
                            if '```json' in content:
                                json_start = content.find('```json') + 7
                                json_end = content.find('```', json_start)
                                content = content[json_start:json_end].strip()
                            elif '{' in content and '}' in content:
                                start = content.find('{')
                                end = content.rfind('}') + 1
                                content = content[start:end]
                            parsed = json.loads(content)
                            return {
                                'success': True,
                                'similarity': int(parsed.get('similarity', 0)),
                                'confidence': float(parsed.get('confidence', 0.0)),
                                'reason': str(parsed.get('reason', ''))
                            }
                        except json.JSONDecodeError:
                            logger.warning(f"JSON parsing failed: {content}")
                            return {'success': False, 'error': "JSON parsing failed", 'raw_response': content}
                    else:
                        error_text = await response.text()
                        logger.warning(f"API call failed ({response.status}): {error_text}")
                        if response.status == 429:
                            await asyncio.sleep(self.retry_delay * (2 ** attempt))
                            continue
                        elif response.status == 401:
                            return {'success': False, 'error': 'Invalid API key'}
                        else:
                            return {'success': False, 'error': f'API error: {response.status}'}
            except asyncio.TimeoutError:
                logger.warning(f"Request timeout, retrying {attempt + 1}/{self.max_retries}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
            except Exception as e:
                logger.error(f"Request exception: {str(e)}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)

        return {'success': False, 'error': 'Max retries exceeded'}

    async def compare_single_pair(self, session: aiohttp.ClientSession, v_file: str,
                                  json_file: str) -> ComparisonResult:
        v_content, v_error = self.read_file_content(v_file)
        if v_error:
            return ComparisonResult(v_file, json_file, 0, 1.0, "V file read failed", v_error)
        json_content, json_error = self.read_file_content(json_file)
        if json_error:
            return ComparisonResult(v_file, json_file, 0, 1.0, "JSON file read failed", json_error)
        v_module_match = re.search(r"module\s+(\w+)\s*\(?", v_content)
        if not v_module_match:
            return ComparisonResult(v_file, json_file, 0, 1.0, "No module found in V file", "No module definition")
        v_module_name = v_module_match.group(1)
        extracted_module = self.extract_module_from_content(json_content, v_module_name)
        if extracted_module:
            logger.info(f"Extracted module '{v_module_name}' from JSON file for comparison")
            json_content = extracted_module
        result = await self.call_deepseek(session, v_content, json_content)
        if result['success']:
            return ComparisonResult(v_file, json_file, result['similarity'], result['confidence'], result['reason'])
        else:
            return ComparisonResult(v_file, json_file, 0, 0.0, "API call failed", result['error'])

    def find_file_pairs(self, v_directory: str, json_directory: str) -> List[Tuple[str, str]]:
        v_dir, json_dir = Path(v_directory), Path(json_directory)
        pairs, v_files = [], list(v_dir.glob("*.v"))
        for v_file in v_files:
            base_name = v_file.stem
            if base_name.startswith("Simpletest_"):
                base_name = base_name[len("Simpletest_"):]
            json_name = base_name + ".json"
            json_file = json_dir / json_name
            if json_file.exists():
                pairs.append((str(v_file), str(json_file)))
            else:
                logger.warning(f"⚠️ WARNING: JSON file not found: {json_file}")
        logger.info(f"Found {len(pairs)} file pairs")
        return pairs

    async def batch_compare(self, v_directory: str, json_directory: str, output_file: str,
                            max_concurrent: int = 5, ground_truth_file: str = None) -> None:
        file_pairs = self.find_file_pairs(v_directory, json_directory)
        if not file_pairs:
            logger.error("No file pairs found")
            return
        results, semaphore = [], asyncio.Semaphore(max_concurrent)

        async def compare_with_semaphore(session, v_file, json_file):
            async with semaphore:
                result = await self.compare_single_pair(session, v_file, json_file)
                logger.info(f"Completed: {Path(v_file).name} vs {Path(json_file).name} -> {result.similarity}")
                return result

        async with aiohttp.ClientSession() as session:
            tasks = [compare_with_semaphore(session, v, j) for v, j in file_pairs]
            for i, task in enumerate(asyncio.as_completed(tasks), 1):
                result = await task
                results.append(result)
                if i % 10 == 0:
                    logger.info(f"Progress: {i}/{len(tasks)}")

        self.save_results(results, output_file)
        if ground_truth_file and os.path.exists(ground_truth_file):
            accuracy = self.calculate_accuracy(results, ground_truth_file)
            logger.info(f"Accuracy: {accuracy:.2%}")
        self.print_statistics(results)

    def save_results(self, results: List[ComparisonResult], output_file: str) -> None:
        out_path = Path(output_file).parent
        out_path.mkdir(parents=True, exist_ok=True)
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['v_file', 'json_file', 'similarity', 'confidence', 'reason', 'error'])
            for r in results:
                writer.writerow([
                    Path(r.v_file).name,
                    Path(r.json_file).name,
                    r.similarity,
                    r.confidence,
                    r.reason,
                    r.error
                ])
        logger.info(f"Results saved to: {output_file}")

    def calculate_accuracy(self, results: List[ComparisonResult], ground_truth_file: str) -> float:
        try:
            ground_truth = {}
            with open(ground_truth_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ground_truth[(row['v_file'], row['json_file'])] = int(row['similarity'])
            correct, total = 0, 0
            for r in results:
                key = (Path(r.v_file).name, Path(r.json_file).name)
                if key in ground_truth:
                    total += 1
                    if r.similarity == ground_truth[key]:
                        correct += 1
            return correct / total if total > 0 else 0.0
        except Exception as e:
            logger.error(f"Error calculating accuracy: {e}")
            return 0.0

    def print_statistics(self, results: List[ComparisonResult]) -> None:
        total = len(results)
        similar = sum(1 for r in results if r.similarity == 1)
        not_similar = total - similar
        errors = sum(1 for r in results if r.error)
        logger.info("=== Statistics ===")
        logger.info(f"Total file pairs: {total}")
        logger.info(f"Similar (1): {similar} ({similar / total * 100:.1f}%)")
        logger.info(f"Not similar (0): {not_similar} ({not_similar / total * 100:.1f}%)")
        logger.info(f"Processing errors: {errors}")
        if total > 0:
            valid_confidences = [r.confidence for r in results if r.confidence > 0]
            if valid_confidences:
                avg_confidence = sum(valid_confidences) / len(valid_confidences)
                logger.info(f"Average confidence: {avg_confidence:.2f}")


def main():
    parser = argparse.ArgumentParser(
        description='Batch comparison tool for Verilog functional similarity based on DeepSeek',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument('--api-key',
                        default=DEFAULT_API_KEY,
                        help='DeepSeek API key（Default values have been set in the code.）')
    parser.add_argument('--v-dir',
                        default=DEFAULT_V_DIR.as_posix(),
                        help=f'Directory containing .v files (default: {DEFAULT_V_DIR})')
    parser.add_argument('--json-dir',
                        default=DEFAULT_JSON_DIR.as_posix(),
                        help=f'Directory containing .json files (default: {DEFAULT_JSON_DIR})')
    parser.add_argument('--output',
                        default=DEFAULT_OUTPUT,
                        help=f'Output CSV file (default: {DEFAULT_OUTPUT})')
    parser.add_argument('--ground-truth',
                        help='Ground truth CSV file for accuracy calculation')
    parser.add_argument('--max-concurrent', type=int, default=5, help='Maximum concurrent requests')

    args = parser.parse_args()

    if not args.api_key or args.api_key == "sk-REPLACE_WITH_YOUR_KEY":
        raise RuntimeError("Missing API key. Please replace with real. DeepSeek API Key")

    async def run_comparison():
        comparator = DeepSeekVerilogComparator(args.api_key)

        try:
            logger.info("Starting batch comparison...")
            await comparator.batch_compare(
                args.v_dir,
                args.json_dir,
                args.output,
                args.max_concurrent,
                args.ground_truth
            )
            logger.info("Batch comparison completed!")

        except KeyboardInterrupt:
            logger.info("User interrupted")
        except Exception as e:
            logger.error(f"Program exception: {str(e)}")

    asyncio.run(run_comparison())


if __name__ == "__main__":
    main()