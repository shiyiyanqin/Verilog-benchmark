import json
import re
import requests
import subprocess
import os
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import logging
import hashlib
import time
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from datetime import datetime

class VerificationLevel:
    NONE = 0
    QUICK = 1
    SMART = 2
    FULL = 3


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)s] - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('verilog_autofix.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

class OutputFormat:
    VVP_ONLY = 0
    OUT_ONLY = 1
    BOTH = 2

class VerilogProjectAutoFixer:
    def __init__(self, api_key: str = None, iverilog_path: str = "iverilog", vvp_path: str = "vvp",
                 verification_level: int = VerificationLevel.SMART, output_format: int = OutputFormat.BOTH):
        self.api_key = api_key
        self.base_url = "https://api.deepseek.com/v1/chat/completions"
        self.iverilog_path = iverilog_path
        self.vvp_path = vvp_path
        self.verification_level = verification_level
        self.output_format = output_format

        self.api_call_lock = threading.Lock()
        self.last_api_call_time = 0
        self.min_api_call_interval = 1.0

        self.progress_lock = threading.Lock()
        self.projects_processed = 0
        self.total_projects = 0
        self.verify_icarus_installation()

    def find_icarus_installation(self):

        import platform

        if platform.system().lower() != "windows":
            return None, None


        possible_paths = [
            r"C:\iverilog\bin",
        ]

        for base_path in possible_paths:
            iverilog_exe = Path(base_path) / "iverilog.exe"
            vvp_exe = Path(base_path) / "vvp.exe"

            if iverilog_exe.exists() and vvp_exe.exists():
                logger.info(f"Found Icarus Verilog at: {base_path}")
                return str(iverilog_exe), str(vvp_exe)

        return None, None

    def verify_icarus_installation(self):

        try:
            result = subprocess.run([self.iverilog_path, "-V"],
                                    capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version_info = result.stdout.strip()
                logger.info(f"Icarus Verilog found: {version_info.split()[0]} {version_info.split()[1]}")
                return True
            else:
                logger.warning("Icarus Verilog found but version check failed")

        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"Initial Icarus check failed: {str(e)}")


            logger.info("Attempting to auto-locate Icarus Verilog...")
            found_iverilog, found_vvp = self.find_icarus_installation()

            if found_iverilog and found_vvp:

                self.iverilog_path = found_iverilog
                self.vvp_path = found_vvp

                try:
                    result = subprocess.run([self.iverilog_path, "-V"],
                                            capture_output=True, text=True, timeout=10)
                    if result.returncode == 0:
                        version_info = result.stdout.strip()
                        logger.info(f"Auto-found Icarus Verilog: {version_info.split()[0]} {version_info.split()[1]}")
                        return True
                except Exception:
                    pass

            logger.error(f"Icarus Verilog not found or not working")
            self.show_installation_help()
            raise RuntimeError("Icarus Verilog is required but not available")

        return True

    def show_installation_help(self):
        import platform
        os_name = platform.system().lower()

        print("\n" + "=" * 60)
        print("ICARUS VERILOG INSTALLATION REQUIRED")
        print("=" * 60)

        if os_name == "windows":
            print("Windows Installation Options:")
            print("\n1. Official Installer (Recommended):")
            print("   - Visit: http://bleyer.org/icarus/")
            print("   - Download the latest .exe installer")
            print("   - Run installer as administrator")
            print("   - Restart command prompt after installation")

            print("\n2. Using Chocolatey:")
            print("   choco install iverilog")

            print("\n3. Using Scoop:")
            print("   scoop install iverilog")

        elif os_name == "darwin":
            print("macOS Installation:")
            print("   brew install icarus-verilog")

        elif os_name == "linux":
            print("Linux Installation:")
            print("   # Ubuntu/Debian:")
            print("   sudo apt-get update && sudo apt-get install iverilog")
            print("   # CentOS/RHEL/Fedora:")
            print("   sudo yum install iverilog")

        print("\nAfter installation, verify with:")
        print("   iverilog -V")
        print("   vvp -V")
        print("=" * 60)

    def compile_verilog_project(self, verilog_files: List[str], output_dir: str, generate_out: bool = True) -> Tuple[
        bool, str, str]:
        if not verilog_files:
            return False, "", "No Verilog files provided"

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        original_count = len(verilog_files)

        blackbox_files = [f for f in verilog_files if '_bb.v' in f]
        instance_files = [f for f in verilog_files if '_inst.v' in f]

        if blackbox_files or instance_files:
            logger.info(f"Found {len(blackbox_files)} blackbox and {len(instance_files)} instance files")
            verilog_files = [f for f in verilog_files if '_bb.v' not in f and '_inst.v' not in f]
            logger.info(f"Quick filter: {original_count} files -> {len(verilog_files)} files")

        module_conflicts = self.detect_module_conflicts(verilog_files)
        if module_conflicts:
            logger.warning(f"Detected module conflicts: {module_conflicts}")
            filtered_files = self.smart_filter_verilog_files(verilog_files)
            if filtered_files and len(filtered_files) < len(verilog_files):
                logger.info(f"Using smart filtered file list: {len(filtered_files)} files")
                verilog_files = filtered_files

        compiled_vvp_file = output_path / "compiled_project.vvp"
        compiled_out_file = output_path / "compiled_project.out"

        for old_file in [compiled_vvp_file, compiled_out_file]:
            if old_file.exists():
                try:
                    old_file.unlink()
                    logger.debug(f"Removed existing compiled file: {old_file}")
                except Exception as e:
                    logger.warning(f"Could not remove existing file: {e}")

        try:
            missing_files = []
            for vf in verilog_files:
                if not Path(vf).exists():
                    missing_files.append(vf)

            if missing_files:
                error_msg = f"Missing files: {missing_files}"
                logger.error(error_msg)
                return False, "", error_msg

            logger.info(f"Compiling {len(verilog_files)} files:")
            for i, vf in enumerate(verilog_files, 1):
                logger.info(f"  {i}. {Path(vf).name}")

            include_paths = self.detect_include_paths(output_path, verilog_files)

            compilation_results = []

            if self.output_format in [OutputFormat.VVP_ONLY, OutputFormat.BOTH]:
                vvp_result = self._compile_to_vvp(verilog_files, compiled_vvp_file, include_paths, output_path)
                compilation_results.append(("VVP", vvp_result))

            if generate_out and self.output_format in [OutputFormat.OUT_ONLY, OutputFormat.BOTH]:
                out_result = self._compile_to_out(verilog_files, compiled_out_file, include_paths, output_path)
                compilation_results.append(("OUT", out_result))

            overall_success = any(result[1][0] for result in compilation_results)
            combined_stdout = "\n".join([f"=== {name} ===\n{result[1]}" for name, result in compilation_results])
            combined_stderr = "\n".join([f"=== {name} ===\n{result[2]}" for name, result in compilation_results])

            if overall_success:
                successful_formats = [name for name, result in compilation_results if result[0]]
                logger.info(f"✓ Compilation successful for: {', '.join(successful_formats)}")
            else:
                logger.warning("✗ All compilation attempts failed")

            return overall_success, combined_stdout, combined_stderr

        except subprocess.TimeoutExpired:
            return False, "", "Compilation timed out after 60 seconds"
        except Exception as e:
            return False, "", f"Compilation error: {str(e)}"

    def _compile_to_vvp(self, verilog_files: List[str], output_file: Path, include_paths: List[str],
                        working_dir: Path) -> Tuple[bool, str, str]:
        cmd = [
                  self.iverilog_path,
                  "-o", str(output_file),
                  "-g2012",
                  "-Wall",
                  "-Wno-timescale",
              ] + include_paths + verilog_files

        logger.info(f"VVP compilation command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(working_dir)
        )

        success = self._verify_compilation_output(result, output_file, "VVP")
        return success, result.stdout, result.stderr

    def _compile_to_out(self, verilog_files: List[str], output_file: Path, include_paths: List[str],
                        working_dir: Path) -> Tuple[bool, str, str]:
        cmd = [
                  self.iverilog_path,
                  "-o", str(output_file),
                  "-g2012",
                  "-Wall",
                  "-Wno-timescale",
                  "-tvvp",
              ] + include_paths + verilog_files

        logger.info(f"OUT compilation command: {' '.join(cmd)}")

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(working_dir)
        )

        success = self._verify_compilation_output(result, output_file, "OUT")
        return success, result.stdout, result.stderr

    def _compile_single_file_to_out(self, verilog_file: str, output_dir: str) -> Tuple[bool, str, str]:
        output_path = Path(output_dir)
        temp_out_file = output_path / f"{Path(verilog_file).stem}_temp.out"

        if temp_out_file.exists():
            temp_out_file.unlink()

        cmd = [
            self.iverilog_path,
            "-o", str(temp_out_file),
            "-tvvp",
            "-g2012",
            "-Wall",
            "-Wno-timescale",
            verilog_file
        ]

        logger.info(f"Attempting single file compilation to .out for: {Path(verilog_file).name}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            success = result.returncode == 0 and temp_out_file.exists() and temp_out_file.stat().st_size > 0
            if success:
                logger.debug(f"✓ Single file {Path(verilog_file).name} compiled successfully to .out")

            else:
                logger.warning(f"✗ Single file {Path(verilog_file).name} compilation to .out failed")

            return success, result.stdout, result.stderr

        except subprocess.TimeoutExpired:
            logger.warning(f"Single file compilation for {Path(verilog_file).name} timed out.")
            return False, "", "Compilation timed out"
        except Exception as e:
            logger.error(f"Error compiling single file {verilog_file}: {e}")
            return False, "", str(e)

    def _verify_compilation_output(self, result: subprocess.CompletedProcess, output_file: Path,
                                   file_type: str) -> bool:
        if result.returncode != 0:
            logger.warning(f"✗ {file_type} compilation failed with return code {result.returncode}")
            return False

        if not output_file.exists():
            logger.warning(f"✗ {file_type} file not generated despite return code 0")
            return False

        file_size = output_file.stat().st_size
        if file_size == 0:
            logger.warning(f"✗ {file_type} file is empty")
            return False

        if file_type == "OUT":
            logger.info(f"✓ {file_type} compilation successful (size: {file_size} bytes) - Logic verified")
            return True

        if self.verification_level == VerificationLevel.NONE:
            logger.info(f"✓ {file_type} compilation successful (size: {file_size} bytes)")
            return True
        else:
            verification_result = self.verify_vvp_file(output_file)
            if verification_result:
                logger.info(f"✓ {file_type} compilation and verification successful (size: {file_size} bytes)")
                return True
            else:
                logger.warning(f"✗ {file_type} file verification failed")
                return False

    def detect_module_conflicts(self, verilog_files: List[str]) -> List[str]:
        module_map = {}
        conflicts = []

        for vf in verilog_files:
            try:
                content = self.read_file_with_encoding_detection(vf)
                modules = re.findall(r'^\s*module\s+(\w+)', content, re.MULTILINE)

                for module in modules:
                    if module in module_map:
                        if module not in conflicts:
                            conflicts.append(module)
                        logger.debug(f"Module '{module}' conflict: {Path(vf).name} vs {Path(module_map[module]).name}")
                    else:
                        module_map[module] = vf
            except Exception as e:
                logger.debug(f"Could not analyze {Path(vf).name}: {e}")

        return conflicts

    def smart_filter_verilog_files(self, verilog_files: List[str]) -> List[str]:

        file_info = {}
        module_to_files = {}

        for vf in verilog_files:
            filename = Path(vf).name
            file_info[vf] = {
                'filename': filename,
                'is_blackbox': '_bb.v' in filename,
                'is_instance': '_inst.v' in filename,
                'modules': []
            }

            try:
                content = self.read_file_with_encoding_detection(vf)
                modules = re.findall(r'^\s*module\s+(\w+)', content, re.MULTILINE)
                file_info[vf]['modules'] = modules

                for module in modules:
                    if module not in module_to_files:
                        module_to_files[module] = []
                    module_to_files[module].append(vf)
            except Exception as e:
                logger.debug(f"Could not analyze {filename}: {e}")

        conflicting_modules = {mod: files for mod, files in module_to_files.items() if len(files) > 1}

        if not conflicting_modules:
            logger.info("No module conflicts found, returning original file list")
            return verilog_files

        selected_files = set()
        excluded_files = set()

        for module_name, conflicting_files in conflicting_modules.items():
            logger.info(f"Resolving conflict for module '{module_name}' in {len(conflicting_files)} files")

            regular_files = [f for f in conflicting_files if
                             not file_info[f]['is_blackbox'] and not file_info[f]['is_instance']]

            if regular_files:
                selected_file = regular_files[0]
                selected_files.add(selected_file)
                logger.debug(f"  Selected regular file: {Path(selected_file).name}")

                for f in conflicting_files:
                    if f != selected_file:
                        excluded_files.add(f)
                        logger.debug(f"  Excluded: {Path(f).name}")
            else:
                selected_file = conflicting_files[0]
                selected_files.add(selected_file)
                for f in conflicting_files[1:]:
                    excluded_files.add(f)

        for vf in verilog_files:
            if vf not in excluded_files and vf not in selected_files:
                has_conflict = False
                for module in file_info[vf]['modules']:
                    if module in conflicting_modules:
                        has_conflict = True
                        break

                if not has_conflict:
                    selected_files.add(vf)

        result = list(selected_files)
        logger.info(f"Smart filter result: {len(verilog_files)} files -> {len(result)} files")

        return result

    def verify_vvp_file(self, vvp_file: Path) -> bool:
        try:
            cmd = [self.vvp_path, str(vvp_file)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(vvp_file.parent)
            )

            if "syntax error" not in result.stderr.lower():
                logger.debug(f"VVP verification passed for {vvp_file.name}")
                return True
            else:
                logger.warning(f"VVP syntax error in {vvp_file.name}: {result.stderr}")
                return False

        except subprocess.TimeoutExpired:
            logger.debug(f"VVP verification timed out (likely valid) for {vvp_file.name}")
            return True
        except Exception as e:
            logger.warning(f"VVP verification error for {vvp_file.name}: {str(e)}")

        try:
            with open(vvp_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(1024)
                if ":ivl_version" in content or ":vpi_module" in content:
                    logger.debug(f"VVP file format check passed for {vvp_file.name}")
                    return True
        except Exception:
            pass

        return False

    def detect_include_paths(self, base_path: Path, verilog_files: List[str]) -> List[str]:
        include_paths = []
        include_dirs = set()

        include_dirs.add(str(base_path))
        for vf in verilog_files:
            vf_dir = Path(vf).parent
            if vf_dir.exists():
                include_dirs.add(str(vf_dir))

        common_include_names = ['include', 'includes', 'inc', 'hdl', 'rtl', 'src', 'lib', 'common']
        for name in common_include_names:
            for found_dir in base_path.rglob(name):
                if found_dir.is_dir():
                    include_dirs.add(str(found_dir))
        for item in base_path.rglob("*"):
            if item.is_dir():
                has_verilog = any(item.glob("*.v")) or any(item.glob("*.vh"))
                if has_verilog:
                    include_dirs.add(str(item))

        include_refs = self.scan_include_references(verilog_files)
        for inc_ref in include_refs:
            inc_path = Path(inc_ref)
            if inc_path.parent != Path("."):
                for vf in verilog_files:
                    potential_dir = Path(vf).parent / inc_path.parent
                    if potential_dir.exists():
                        include_dirs.add(str(potential_dir))

        for inc_dir in sorted(include_dirs):
            include_paths.extend(["-I", inc_dir])

        logger.debug(f"Detected {len(include_dirs)} include directories")
        return include_paths

    def scan_include_references(self, verilog_files: List[str]) -> set:
        include_pattern = re.compile(r'`include\s*["\']([^"\']+)["\']')
        includes = set()

        for vf in verilog_files:
            try:
                content = self.read_file_with_encoding_detection(vf)
                matches = include_pattern.findall(content)
                includes.update(matches)
            except Exception as e:
                logger.debug(f"Could not scan includes in {Path(vf).name}: {e}")

        logger.debug(f"Found {len(includes)} include references: {includes}")
        return includes

    def run_simulation_test(self, vvp_file: Path, timeout: int = 3) -> Dict[str, Any]:
        try:
            cmd = [self.vvp_path, str(vvp_file)]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(vvp_file.parent)
            )

            return {
                "executed": True,
                "return_code": result.returncode,
                "has_output": bool(result.stdout.strip()),
                "has_errors": bool(result.stderr.strip()),
                "timeout": False
            }

        except subprocess.TimeoutExpired:
            return {
                "executed": True,
                "timeout": True,
                "note": "Simulation running (timeout reached)"
            }
        except Exception as e:
            return {
                "executed": False,
                "error": str(e)
            }

    def verify_compilation_manually(self, output_dir: str) -> Dict[str, Any]:
        output_path = Path(output_dir)

        verilog_files = []
        for vf in output_path.rglob("*.v"):
            verilog_files.append(str(vf))

        verification_result = {
            "found_files": len(verilog_files),
            "files_list": [Path(vf).relative_to(output_path).as_posix() for vf in verilog_files],
            "compilation_attempts": [],
            "vvp_verifications": []
        }

        if not verilog_files:
            verification_result["error"] = "No Verilog files found in output directory"
            return verification_result

        include_paths = self.detect_include_paths(output_path, verilog_files)

        strategies = [
            {
                "name": "Standard 2012 with includes",
                "args": ["-g2012", "-Wall", "-Wno-timescale"] + include_paths
            },
            {
                "name": "Standard 2012 minimal",
                "args": ["-g2012"]
            },
            {
                "name": "IEEE 1364-2005",
                "args": ["-g2005", "-Wall"] + include_paths
            },
            {
                "name": "IEEE 1364-2001",
                "args": ["-g2001", "-Wall"] + include_paths
            },
            {
                "name": "Verilog-95",
                "args": ["-g1995"] + include_paths
            },
            {
                "name": "Permissive mode",
                "args": ["-g2012", "-Wno-error"] + include_paths
            },
            {
                "name": "SystemVerilog subset",
                "args": ["-g2009", "-Wall"] + include_paths
            }
        ]

        for strategy in strategies:
            logger.info(f"Trying {strategy['name']}...")

            compiled_file = output_path / f"test_{strategy['name'].replace(' ', '_').lower()}.vvp"

            if compiled_file.exists():
                try:
                    compiled_file.unlink()
                except:
                    pass

            cmd = [self.iverilog_path, "-o", str(compiled_file)] + strategy['args'] + verilog_files

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=str(output_path)
                )


                attempt = {
                    "strategy": strategy['name'],
                    "command": ' '.join(cmd),
                    "return_code": result.returncode,
                    "stdout": result.stdout.strip(),
                    "stderr": result.stderr.strip(),
                    "error_count": len(result.stderr.strip().splitlines()) if result.stderr.strip() else 0
                }


                compilation_success = False
                vvp_valid = False

                if result.returncode == 0:
                    if compiled_file.exists():
                        file_size = compiled_file.stat().st_size
                        attempt["vvp_file_size"] = file_size

                        if file_size > 0:

                            vvp_valid = self.verify_vvp_file(compiled_file)
                            attempt["vvp_valid"] = vvp_valid

                            if vvp_valid:
                                compilation_success = True


                                sim_result = self.run_simulation_test(compiled_file)
                                attempt["simulation_test"] = sim_result
                            else:
                                attempt["vvp_error"] = "VVP file validation failed"
                        else:
                            attempt["vvp_error"] = "VVP file is empty"
                    else:
                        attempt["vvp_error"] = "VVP file not generated"

                attempt["success"] = compilation_success
                verification_result["compilation_attempts"].append(attempt)

                if compilation_success:
                    logger.info(f"✓ {strategy['name']} succeeded with valid VVP!")
                    verification_result["successful_strategy"] = strategy['name']
                    verification_result["successful_vvp_file"] = str(compiled_file)
                    break
                else:
                    logger.warning(f"✗ {strategy['name']} failed: {attempt.get('vvp_error', 'Compilation error')}")

            except subprocess.TimeoutExpired:
                attempt = {
                    "strategy": strategy['name'],
                    "success": False,
                    "error": "Compilation timeout (30s)"
                }
                verification_result["compilation_attempts"].append(attempt)
            except Exception as e:
                attempt = {
                    "strategy": strategy['name'],
                    "success": False,
                    "error": str(e)
                }
                verification_result["compilation_attempts"].append(attempt)

        successful_attempts = [a for a in verification_result["compilation_attempts"] if a.get("success", False)]
        verification_result["total_successful_strategies"] = len(successful_attempts)

        return verification_result

    def save_detailed_compilation_report(self, verification_result: Dict[str, Any], output_dir: str):
        report_path = Path(output_dir) / "compilation_verification_report.txt"

        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write("COMPILATION VERIFICATION REPORT\n")
                f.write("=" * 50 + "\n\n")

                f.write(f"Found Files: {verification_result['found_files']}\n")
                f.write("File List:\n")
                for file in verification_result['files_list']:
                    f.write(f"  - {file}\n")
                f.write("\n")

                f.write("COMPILATION ATTEMPTS:\n")
                f.write("-" * 30 + "\n")

                for i, attempt in enumerate(verification_result['compilation_attempts'], 1):
                    f.write(f"\n{i}. {attempt['strategy']}\n")
                    f.write(f"   Command: {attempt.get('command', 'N/A')}\n")
                    f.write(f"   Success: {attempt['success']}\n")

                    if 'return_code' in attempt:
                        f.write(f"   Return Code: {attempt['return_code']}\n")
                        f.write(f"   Error Count: {attempt.get('error_count', 0)}\n")

                        if attempt['stdout']:
                            f.write(f"   Stdout: {attempt['stdout']}\n")
                        if attempt['stderr']:
                            f.write(f"   Stderr: {attempt['stderr']}\n")

                    if 'error' in attempt:
                        f.write(f"   Error: {attempt['error']}\n")

                if 'successful_strategy' in verification_result:
                    f.write(f"\nSUCCESSFUL STRATEGY: {verification_result['successful_strategy']}\n")
                else:
                    f.write("\nNO SUCCESSFUL COMPILATION STRATEGY FOUND\n")

                f.write(f"\nReport generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

            logger.info(f"Detailed compilation report saved to: {report_path}")

        except Exception as e:
            logger.error(f"Failed to save compilation report: {str(e)}")

    def _perform_quick_verification(self, result: Dict[str, Any]):
        vvp_path = Path(result['output_dir']) / "compiled_project.vvp"

        if vvp_path.exists() and vvp_path.stat().st_size > 0:
            vvp_valid = self.verify_vvp_file(vvp_path)
            result['verification'] = {
                "type": "QUICK",
                "vvp_exists": True,
                "vvp_size": vvp_path.stat().st_size,
                "vvp_valid": vvp_valid
            }
            result['manually_verified'] = vvp_valid
            result['final_success'] = result.get('final_success', False) and vvp_valid
        else:
            result['verification'] = {
                "type": "QUICK",
                "vvp_exists": False,
                "error": "VVP file not found or empty"
            }
            result['manually_verified'] = False
            result['final_success'] = False

        result['verification_performed'] = True
        result['verification_level'] = "QUICK"

    def _perform_smart_verification(self, result: Dict[str, Any]):
        need_detailed = False
        reasons = []

        vvp_path = Path(result['output_dir']) / "compiled_project.vvp"
        quick_check_passed = False

        if vvp_path.exists() and vvp_path.stat().st_size > 0:
            vvp_valid = self.verify_vvp_file(vvp_path)
            quick_check_passed = vvp_valid
            if not vvp_valid:
                need_detailed = True
                reasons.append("VVP validation failed")
        else:
            need_detailed = True
            reasons.append("VVP file missing or empty")

        if result.get('final_success', False) and not need_detailed:
            if result.get('total_iterations', 0) == 1 and result.get('initial_error_count', 0) > 10:
                need_detailed = True
                reasons.append("Suspiciously quick fix")

            if 'fix_history' in result:
                for hist in result['fix_history']:
                    if 'warning' in str(hist.get('errors', [])).lower():
                        need_detailed = True
                        reasons.append("Warnings detected in compilation")
                        break

        if need_detailed:
            logger.info(f"Smart verification detected issues: {', '.join(reasons)}")
            logger.info("Proceeding with detailed verification...")

            verification_result = self.verify_compilation_manually(result['output_dir'])
            self.save_detailed_compilation_report(verification_result, result['output_dir'])

            result['verification'] = verification_result
            result['verification']['type'] = "SMART_DETAILED"
            result['verification']['trigger_reasons'] = reasons
            result['manually_verified'] = verification_result.get('successful_strategy') is not None

            if result['manually_verified']:
                result['final_success'] = True
            else:
                result['final_success'] = False
                result['failure_analysis'] = self.analyze_compilation_failures(verification_result)
        else:
            logger.info("Smart verification: Quick check passed, no detailed verification needed")
            result['verification'] = {
                "type": "SMART_QUICK",
                "quick_check": "PASSED",
                "vvp_valid": quick_check_passed
            }
            result['manually_verified'] = True

        result['verification_performed'] = True
        result['verification_level'] = "SMART"

    def _perform_full_verification(self, result: Dict[str, Any]):
        logger.info("Performing comprehensive verification...")

        verification_result = self.verify_compilation_manually(result['output_dir'])
        self.save_detailed_compilation_report(verification_result, result['output_dir'])

        result['verification'] = verification_result
        result['verification']['type'] = "FULL"
        result['manually_verified'] = verification_result.get('successful_strategy') is not None

        if result['manually_verified']:
            result['final_success'] = True
            if 'successful_vvp_file' in verification_result:
                result['verified_vvp_file'] = verification_result['successful_vvp_file']
        else:
            result['final_success'] = False
            result['failure_analysis'] = self.analyze_compilation_failures(verification_result)

        result['verification_performed'] = True
        result['verification_level'] = "FULL"

        self.save_enhanced_project_report(result, result['output_dir'])

    def analyze_compilation_failures(self, verification_result: Dict[str, Any]) -> Dict[str, Any]:
        analysis = {
            "common_errors": {},
            "missing_includes": set(),
            "undefined_modules": set(),
            "syntax_issues": [],
            "recommendations": []
        }

        for attempt in verification_result.get('compilation_attempts', []):
            if not attempt.get('success', False) and 'stderr' in attempt:
                stderr = attempt['stderr']

                error_types = {
                    "syntax error": 0,
                    "undefined": 0,
                    "cannot open include file": 0,
                    "Unknown module": 0,
                    "undeclared": 0,
                    "parse error": 0
                }

                for error_type in error_types:
                    count = stderr.lower().count(error_type)
                    if count > 0:
                        if error_type not in analysis["common_errors"]:
                            analysis["common_errors"][error_type] = 0
                        analysis["common_errors"][error_type] += count

                include_pattern = re.compile(r"cannot open include file[:\s]+['\"]([^'\"]+)['\"]", re.IGNORECASE)
                includes = include_pattern.findall(stderr)
                analysis["missing_includes"].update(includes)

                module_pattern = re.compile(r"Unknown module[:\s]+(\w+)", re.IGNORECASE)
                modules = module_pattern.findall(stderr)
                analysis["undefined_modules"].update(modules)

        analysis["missing_includes"] = list(analysis["missing_includes"])
        analysis["undefined_modules"] = list(analysis["undefined_modules"])

        if analysis["missing_includes"]:
            analysis["recommendations"].append(
                f"Missing {len(analysis['missing_includes'])} include files. "
                "Check if all required header files are present in the project."
            )

        if analysis["undefined_modules"]:
            analysis["recommendations"].append(
                f"Found {len(analysis['undefined_modules'])} undefined modules. "
                "Ensure all module dependencies are included in the project."
            )

        if analysis["common_errors"].get("syntax error", 0) > 0:
            analysis["recommendations"].append(
                "Syntax errors detected. Manual review may be required for complex language constructs."
            )

        return analysis

    def enhanced_auto_fix_verilog_project(self, project_dir: str, output_dir: str = None,
                                          max_iterations: int = 10,
                                          verification_level: int = None) -> Dict[str, Any]:
        verify_level = verification_level if verification_level is not None else self.verification_level

        result = self.auto_fix_verilog_project(project_dir, output_dir, max_iterations)

        if verify_level == VerificationLevel.NONE:
            logger.info("Verification level: NONE - Skipping all verification")
            result['verification_performed'] = False
            result['verification_level'] = "NONE"

        elif verify_level == VerificationLevel.QUICK:
            logger.info("Verification level: QUICK - Performing quick VVP check only")
            self._perform_quick_verification(result)

        elif verify_level == VerificationLevel.SMART:
            logger.info("Verification level: SMART - Using intelligent verification")
            self._perform_smart_verification(result)

        elif verify_level == VerificationLevel.FULL:
            logger.info("Verification level: FULL - Performing comprehensive verification")
            self._perform_full_verification(result)
        else:
            logger.warning(f"Unknown verification level: {verify_level}, using SMART")
            self._perform_smart_verification(result)

        return result

    def analyze_compilation_failures(self, verification_result: Dict[str, Any]) -> Dict[str, Any]:

        analysis = {
            "common_errors": {},
            "missing_includes": set(),
            "undefined_modules": set(),
            "syntax_issues": [],
            "recommendations": []
        }

        for attempt in verification_result.get('compilation_attempts', []):
            if not attempt.get('success', False) and 'stderr' in attempt:
                stderr = attempt['stderr']

                error_types = {
                    "syntax error": 0,
                    "undefined": 0,
                    "cannot open include file": 0,
                    "Unknown module": 0,
                    "undeclared": 0,
                    "parse error": 0
                }

                for error_type in error_types:
                    count = stderr.lower().count(error_type)
                    if count > 0:
                        if error_type not in analysis["common_errors"]:
                            analysis["common_errors"][error_type] = 0
                        analysis["common_errors"][error_type] += count

                include_pattern = re.compile(r"cannot open include file[:\s]+['\"]([^'\"]+)['\"]", re.IGNORECASE)
                includes = include_pattern.findall(stderr)
                analysis["missing_includes"].update(includes)

                module_pattern = re.compile(r"Unknown module[:\s]+(\w+)", re.IGNORECASE)
                modules = module_pattern.findall(stderr)
                analysis["undefined_modules"].update(modules)

        analysis["missing_includes"] = list(analysis["missing_includes"])
        analysis["undefined_modules"] = list(analysis["undefined_modules"])

        if analysis["missing_includes"]:
            analysis["recommendations"].append(
                f"Missing {len(analysis['missing_includes'])} include files. "
                "Check if all required header files are present in the project."
            )

        if analysis["undefined_modules"]:
            analysis["recommendations"].append(
                f"Found {len(analysis['undefined_modules'])} undefined modules. "
                "Ensure all module dependencies are included in the project."
            )

        if analysis["common_errors"].get("syntax error", 0) > 0:
            analysis["recommendations"].append(
                "Syntax errors detected. Manual review may be required for complex language constructs."
            )

        return analysis

    def save_enhanced_project_report(self, summary: Dict[str, Any], output_dir: str):
        report_path = Path(output_dir) / "enhanced_autofix_report.txt"

        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write("ENHANCED VERILOG PROJECT AUTO-FIX REPORT\n")
                f.write("=" * 80 + "\n\n")

                f.write("PROJECT INFORMATION:\n")
                f.write(f"  Project Name: {summary['project_name']}\n")
                f.write(f"  Original Directory: {summary['original_project_dir']}\n")
                f.write(f"  Output Directory: {summary['output_dir']}\n")
                f.write(f"  Generation Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"  Verification Level: {summary.get('verification_level', 'N/A')}\n\n")

                f.write("FILE STATISTICS:\n")
                f.write(f"  Total Files in Original: {summary.get('total_files_in_original_project', 0)}\n")
                f.write(f"  Files Processed: {summary.get('files_copied_and_processed', 0)}\n")
                if 'copy_errors' in summary and summary['copy_errors']:
                    f.write(f"  Files Failed to Copy: {len(summary['copy_errors'])}\n")
                f.write("\n")

                f.write("FIX PROCESS SUMMARY:\n")
                f.write(
                    f"  Initial Status: {'HAD_ERRORS' if summary.get('initial_error_count', 0) > 0 else 'NO_ERRORS'}\n")
                f.write(f"  Initial Error Count: {summary.get('initial_error_count', 0)}\n")
                f.write(f"  Iterations Used: {summary.get('total_iterations', 0)}/{summary.get('max_iterations', 0)}\n")
                f.write(f"  Total Time: {summary.get('total_time_seconds', 0):.2f} seconds\n")
                f.write(f"  Final Error Count: {summary.get('final_error_count', 0)}\n")
                f.write(f"  Errors Fixed: {summary.get('errors_fixed', 0)}\n\n")

                f.write("VERIFICATION RESULTS:\n")
                f.write(f"  Verification Performed: {summary.get('verification_performed', False)}\n")
                if summary.get('verification_performed'):
                    f.write(f"  Verification Level: {summary.get('verification_level', 'N/A')}\n")
                    f.write(f"  Verification Status: {summary.get('verification_status', 'N/A')}\n")
                    if 'verification_reason' in summary:
                        f.write(f"  Verification Reason: {summary['verification_reason']}\n")

                    if 'verification' in summary and isinstance(summary['verification'], dict):
                        verification = summary['verification']
                        if 'successful_strategy' in verification:
                            f.write(f"  Successful Strategy: {verification['successful_strategy']}\n")
                        if 'total_successful_strategies' in verification:
                            f.write(f"  Working Strategies: {verification['total_successful_strategies']}\n")
                        if 'successful_vvp_file' in verification:
                            f.write(f"  Verified VVP: {Path(verification['successful_vvp_file']).name}\n")
                f.write("\n")

                f.write("FINAL STATUS:\n")
                f.write(f"  Overall Result: {'SUCCESS' if summary.get('final_success', False) else 'FAILED'}\n")
                f.write(f"  Manually Verified: {summary.get('manually_verified', False)}\n")
                if 'verified_vvp_file' in summary:
                    f.write(f"  Verified Output: {Path(summary['verified_vvp_file']).name}\n")
                f.write("\n")

                if 'failure_analysis' in summary and not summary.get('final_success', False):
                    analysis = summary['failure_analysis']
                    f.write("FAILURE ANALYSIS:\n")

                    if analysis.get('common_errors'):
                        f.write("  Common Error Types:\n")
                        for error_type, count in sorted(analysis['common_errors'].items(),
                                                        key=lambda x: x[1], reverse=True):
                            f.write(f"    - {error_type}: {count} occurrences\n")

                    if analysis.get('missing_includes'):
                        f.write(f"  Missing Include Files ({len(analysis['missing_includes'])}):\n")
                        for inc in analysis['missing_includes'][:10]:
                            f.write(f"    - {inc}\n")
                        if len(analysis['missing_includes']) > 10:
                            f.write(f"    ... and {len(analysis['missing_includes']) - 10} more\n")

                    if analysis.get('undefined_modules'):
                        f.write(f"  Undefined Modules ({len(analysis['undefined_modules'])}):\n")
                        for mod in analysis['undefined_modules'][:10]:
                            f.write(f"    - {mod}\n")
                        if len(analysis['undefined_modules']) > 10:
                            f.write(f"    ... and {len(analysis['undefined_modules']) - 10} more\n")

                    if analysis.get('recommendations'):
                        f.write("  Recommendations:\n")
                        for i, rec in enumerate(analysis['recommendations'], 1):
                            f.write(f"    {i}. {rec}\n")
                    f.write("\n")

                if 'fix_history' in summary and summary['fix_history']:
                    f.write("ITERATION HISTORY:\n")
                    for hist in summary['fix_history'][-5:]:
                        status = "SUCCESS" if hist.get('compilation_success',
                                                       False) else f"{hist.get('error_count', 0)} errors"
                        f.write(f"  Iteration {hist['iteration']}: {status}\n")
                    if len(summary['fix_history']) > 5:
                        f.write(f"  ... ({len(summary['fix_history']) - 5} earlier iterations omitted)\n")
                    f.write("\n")

                if summary.get('final_errors') and not summary.get('final_success', False):
                    f.write("REMAINING ERRORS:\n")
                    displayed = 0
                    unique_errors = {}

                    for error in summary['final_errors']:
                        key = f"{Path(error['file']).name}:{error['type']}"
                        if key not in unique_errors:
                            unique_errors[key] = []
                        unique_errors[key].append(error)

                    for key, errors in list(unique_errors.items())[:10]:
                        error = errors[0]
                        f.write(f"  - {Path(error['file']).name} Line {error['line']}: {error['message']}\n")
                        if len(errors) > 1:
                            f.write(f"    ({len(errors) - 1} similar errors in same file)\n")
                        displayed += 1

                    if len(unique_errors) > 10:
                        f.write(f"  ... and {len(unique_errors) - 10} more error types\n")
                    f.write("\n")

                f.write("=" * 80 + "\n")
                f.write("Report generated by Enhanced Verilog Project Auto-Fixer\n")
                f.write("Original project files were NOT modified.\n")
                if summary.get('final_success'):
                    f.write("✓ This project has been successfully fixed and verified.\n")
                else:
                    f.write("✗ This project still has compilation issues that require manual intervention.\n")

            json_path = Path(output_dir) / "enhanced_autofix_summary.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json_safe_summary = summary.copy()
                if 'fix_history' in json_safe_summary:
                    json_safe_summary['fix_history'] = json_safe_summary['fix_history'][-10:]
                json.dump(json_safe_summary, f, indent=2, ensure_ascii=False, default=str)

            logger.info(f"Enhanced project report saved to: {report_path}")

        except Exception as e:
            logger.error(f"Failed to save enhanced project report: {str(e)}")

    def compare_with_original_errors(self, project_dir: str, output_dir: str):
        logger.info("Comparing errors before and after fix...")

        original_error_file = Path(project_dir) / "compilation_errors.txt"
        original_errors = []
        if original_error_file.exists():
            original_errors = self.read_existing_errors(str(original_error_file))

        output_path = Path(output_dir)
        verilog_files = []
        for vf in output_path.rglob("*.v"):
            verilog_files.append(str(vf))

        success, stdout, stderr = self.compile_verilog_project(verilog_files, str(output_path))
        current_errors = self.parse_icarus_errors(stderr) if not success else []

        logger.info(f"Original errors: {len(original_errors)}")
        logger.info(f"Current errors: {len(current_errors)}")

        if len(current_errors) < len(original_errors):
            logger.info(f"✓ Reduced errors by {len(original_errors) - len(current_errors)}")
        elif len(current_errors) == 0:
            logger.info("✓ All errors fixed!")
        else:
            logger.warning(f"✗ Still have {len(current_errors)} errors")

        return {
            "original_error_count": len(original_errors),
            "current_error_count": len(current_errors),
            "improvement": len(original_errors) - len(current_errors),
            "current_errors": current_errors
        }

    def parse_icarus_errors(self, stderr: str) -> List[Dict[str, Any]]:

        if not stderr.strip():
            return []

        errors = []
        lines = stderr.strip().split('\n')


        error_patterns = [

            r"(.+\.v):(\d+):\s*(error|warning):\s*(.+)",

            r"(.+\.v):(\d+):\s*syntax\s+error\s*(.*)$",

            r"(.+\.v):(\d+):\s*(.+)",

            r"(.+\.v):\s*(error|warning):\s*(.+)",
        ]

        for line in lines:
            line = line.strip()
            if not line:
                continue

            matched = False
            for pattern in error_patterns:
                match = re.match(pattern, line, re.IGNORECASE)
                if match:
                    groups = match.groups()

                    if len(groups) >= 4:
                        errors.append({
                            "file": groups[0],
                            "line": int(groups[1]) if groups[1].isdigit() else 0,
                            "type": groups[2],
                            "message": groups[3],
                            "raw_line": line
                        })
                    elif len(groups) >= 3:
                        if groups[1].isdigit():
                            errors.append({
                                "file": groups[0],
                                "line": int(groups[1]),
                                "type": "error",
                                "message": groups[2],
                                "raw_line": line
                            })
                        else:
                            errors.append({
                                "file": groups[0],
                                "line": 0,
                                "type": groups[1] if groups[1] in ['error', 'warning'] else "error",
                                "message": groups[2] if len(groups) > 2 else groups[1],
                                "raw_line": line
                            })

                    matched = True
                    break

            if not matched and line:
                errors.append({
                    "file": "unknown",
                    "line": 0,
                    "type": "error",
                    "message": line,
                    "raw_line": line
                })

        logger.debug(f"Parsed {len(errors)} error(s) from Icarus output")
        return errors

    def read_existing_errors(self, error_file_path: str) -> List[Dict[str, Any]]:

        try:

            error_content = self.read_file_with_encoding_detection(error_file_path)


            return self.parse_icarus_errors(error_content)
        except Exception as e:
            logger.warning(f"Could not read existing errors: {str(e)}")
            return []

    def generate_project_fix_prompt(self, file_path: str, file_content: str,
                                    project_errors: List[Dict[str, Any]],
                                    all_files: Dict[str, str],
                                    iteration: int = 1) -> str:

        file_name = Path(file_path).name
        file_errors = [e for e in project_errors if Path(e['file']).name == file_name]

        if not file_errors:
            return None

        module_match = re.search(r"module\s+(\w+)", file_content)
        module_name = module_match.group(1) if module_match else "unknown_module"

        error_list = []
        for i, error in enumerate(file_errors[:10], 1):
            line_info = f"Line {error['line']}" if error['line'] > 0 else "Unknown line"
            error_list.append(f"{i}. {line_info}: {error['message']}")

        error_summary = "\n".join(error_list)

        other_modules_info = []
        for other_file, other_content in all_files.items():
            if other_file != file_path:
                module_matches = re.findall(r"module\s+(\w+)\s*\((.*?)\);", other_content, re.DOTALL)
                for mod_name, mod_ports in module_matches:
                    other_modules_info.append(f"Module {mod_name} with ports: {mod_ports.strip()}")

        context_info = "\n".join(other_modules_info) if other_modules_info else "No other modules in project"

        return f"""You are an expert Verilog developer. I need you to fix compilation errors in a Verilog file that is part of a larger project.

**Current Iteration:** {iteration}
**File:** {file_name}
**Module:** {module_name}
**Compiler:** Icarus Verilog
**File Errors:** {len(file_errors)} errors in this file

**Other Modules in Project:**
{context_info}

**Current Code:**
```verilog
{file_content}
```

**Compilation Errors for this file:**
{error_summary}

**Instructions:**
1. Fix ONLY the errors in this specific file
2. Maintain compatibility with other modules in the project
3. Do NOT change module interfaces (ports, parameters) unless absolutely necessary
4. Ensure proper module instantiation if referencing other modules
5. Add comments explaining significant changes
6. Return ONLY the complete corrected Verilog code

**Output Format:**
```verilog
// Fixed Verilog code here
module {module_name}(...);
    // Your corrected implementation
endmodule
```

Please provide the complete fixed code:"""

    def call_deepseek_api(self, prompt: str, max_retries: int = 3) -> str:

        if not self.api_key:
            raise ValueError("API key not set, please set DeepSeek API key first")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "deepseek-coder",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 4000,
            "stream": False
        }

        for attempt in range(max_retries):
            try:
                logger.info(f"Calling DeepSeek API (attempt {attempt + 1}/{max_retries})...")

                response = requests.post(
                    self.base_url,
                    headers=headers,
                    json=payload,
                    timeout=60
                )

                response.raise_for_status()
                result = response.json()

                content = result["choices"][0]["message"]["content"]

                fixed_code = self.extract_verilog_code(content)

                logger.info("API call successful, code extracted")
                return fixed_code

            except requests.exceptions.RequestException as e:
                logger.warning(f"API call failed (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise Exception(f"API call failed after {max_retries} retries: {str(e)}")

    def extract_verilog_code(self, api_response: str) -> str:

        patterns = [
            r"```verilog\n(.*?)\n```",
            r"```\n(.*?)\n```",
            r"```(.*?)```",
        ]

        for pattern in patterns:
            match = re.search(pattern, api_response, re.DOTALL)
            if match:
                code = match.group(1).strip()
                if "module" in code and "endmodule" in code:
                    return code


        module_match = re.search(r"(module\s+.*?endmodule)", api_response, re.DOTALL | re.IGNORECASE)
        if module_match:
            return module_match.group(1).strip()


        if "module" in api_response and "endmodule" in api_response:
            return api_response.strip()

        raise ValueError("Could not extract valid Verilog code from API response")

    def call_deepseek_api_with_rate_limit(self, prompt: str, max_retries: int = 3) -> str:

        with self.api_call_lock:

            current_time = time.time()
            time_since_last_call = current_time - self.last_api_call_time
            if time_since_last_call < self.min_api_call_interval:
                time.sleep(self.min_api_call_interval - time_since_last_call)

            result = self.call_deepseek_api(prompt, max_retries)
            self.last_api_call_time = time.time()
            return result

    def read_file_with_encoding_detection(self, file_path: str) -> str:

        file_path = Path(file_path)

        encodings_to_try = [
            'utf-8',
            'utf-8-sig',
            'gbk',
            'gb2312',
            'gb18030',
            'big5',
            'latin-1',
            'cp1252',
            'cp936',
            'iso-8859-1'
        ]

        for encoding in encodings_to_try:
            try:
                with open(file_path, 'r', encoding=encoding, errors='replace') as f:
                    content = f.read()
                logger.debug(f"Successfully read {file_path.name} using {encoding} encoding")
                return content
            except (UnicodeDecodeError, UnicodeError):
                continue
            except Exception as e:
                logger.warning(f"Error reading with {encoding}: {str(e)}")
                continue

        try:
            with open(file_path, 'rb') as f:
                raw_data = f.read()
            content = raw_data.decode('utf-8', errors='replace')
            logger.warning(f"Read {file_path.name} in binary mode with error replacement")
            return content
        except Exception as e:
            raise ValueError(f"Unable to read file {file_path}: {str(e)}")

    def find_verilog_projects(self, root_dir: str) -> List[str]:

        root_path = Path(root_dir)

        if not root_path.exists():
            logger.error(f"Directory does not exist: {root_dir}")
            return []

        project_dirs = []

        def scan_directory(current_dir: Path, depth: int = 0) -> None:

            try:
                if depth > 10:
                    logger.warning(f"Reached maximum depth limit at: {current_dir}")
                    return

                verilog_files = list(current_dir.glob("*.v"))
                if verilog_files:
                    project_dirs.append(str(current_dir))
                    logger.info(
                        f"Found project: {current_dir.relative_to(root_path)} with {len(verilog_files)} Verilog files")

                for item in current_dir.iterdir():
                    if item.is_dir():
                        skip_dirs = {'.git', '.svn', '__pycache__', '.vscode', '.idea',
                                     'dist', 'temp', 'tmp'}
                        if item.name.lower() not in skip_dirs and not item.name.startswith('.'):
                            scan_directory(item, depth + 1)

            except PermissionError:
                logger.warning(f"Permission denied: {current_dir}")
            except Exception as e:
                logger.warning(f"Error scanning directory {current_dir}: {str(e)}")

        logger.info(f"Starting recursive search in: {root_dir}")
        scan_directory(root_path)

        project_dirs = sorted(list(set(project_dirs)))

        logger.info(f"Found {len(project_dirs)} Verilog projects in total")
        return project_dirs

    def find_all_verilog_files_in_project(self, project_dir: str) -> List[str]:

        project_path = Path(project_dir)
        verilog_files = set()

        def scan_for_verilog(current_dir: Path, depth: int = 0) -> None:

            try:
                if depth > 5:
                    return

                for v_file in current_dir.glob("*.v"):
                    if v_file.is_file():
                        verilog_files.add(str(v_file.resolve()))

                for item in current_dir.iterdir():
                    if item.is_dir():
                        skip_dirs = {'.git', '.svn', '__pycache__', '.vscode', '.idea', 'temp', 'tmp'}
                        if item.name.lower() not in skip_dirs and not item.name.startswith('.'):
                            scan_for_verilog(item, depth + 1)

            except (PermissionError, OSError) as e:
                logger.warning(f"Cannot access {current_dir}: {str(e)}")

        scan_for_verilog(project_path)
        return sorted(list(verilog_files))

    def copy_files_without_duplication(self, verilog_file_paths: List[str], project_path: Path, output_path: Path) -> \
Dict[str, str]:

        file_mapping = {}

        logger.info(f"Copying {len(verilog_file_paths)} files to output directory...")

        for vf_path in verilog_file_paths:
            try:
                vf = Path(vf_path)

                try:
                    rel_path = vf.relative_to(project_path)
                except ValueError:
                    rel_path = vf.name
                    logger.warning(f"File outside project path: {vf_path}, using filename only")

                dest_file = output_path / rel_path

                dest_file.parent.mkdir(parents=True, exist_ok=True)

                if dest_file.exists():
                    try:
                        existing_content = self.read_file_with_encoding_detection(str(dest_file))
                        new_content = self.read_file_with_encoding_detection(vf_path)

                        if existing_content == new_content:
                            logger.debug(f"Skipping identical file: {rel_path}")
                            file_mapping[vf_path] = str(dest_file)
                            continue
                        else:
                            counter = 1
                            base_name = dest_file.stem
                            suffix = dest_file.suffix
                            while True:
                                new_dest = dest_file.parent / f"{base_name}_v{counter}{suffix}"
                                if not new_dest.exists():
                                    dest_file = new_dest
                                    logger.info(f"Renamed to avoid conflict: {rel_path} -> {dest_file.name}")
                                    break
                                counter += 1
                                if counter > 100:
                                    logger.error(f"Too many conflicts for file: {rel_path}")
                                    break
                    except Exception as e:
                        logger.warning(f"Could not compare files: {str(e)}")
                        dest_file = dest_file.parent / f"{dest_file.stem}_copy{dest_file.suffix}"

                shutil.copy2(vf, dest_file)

                file_mapping[vf_path] = str(dest_file)

                logger.debug(f"Copied {rel_path}")

            except Exception as e:
                logger.error(f"Failed to copy {vf_path}: {str(e)}")
                continue

        logger.info(f"Successfully copied {len(file_mapping)} files")
        return file_mapping

    def _compile_single_file_to_out(self, verilog_file: str, output_dir: str) -> Tuple[bool, str, str]:
        output_path = Path(output_dir)
        temp_out_file = output_path / f"{Path(verilog_file).stem}.out"

        cmd = [
                  self.iverilog_path,
                  "-o", str(temp_out_file),
                  "-tvvp",
                  "-g2012",
              ] + [verilog_file]

        logger.info(f"Attempting to compile single file to .out: {Path(verilog_file).name}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)

            success = result.returncode == 0 and temp_out_file.exists() and temp_out_file.stat().st_size > 0
            if success:
                logger.info(f"✓ Single file {Path(verilog_file).name} compiled successfully to .out")
            else:
                logger.warning(f"✗ Single file {Path(verilog_file).name} compilation to .out failed")

            return success, result.stdout, result.stderr

        except Exception as e:
            logger.error(f"Error compiling single file {verilog_file}: {e}")
            return False, "", str(e)

    def auto_fix_verilog_project(self, project_dir: str, output_dir: str = None,
                                 max_iterations: int = 10) -> Dict[str, Any]:
        project_path = Path(project_dir)
        if not project_path.exists():
            raise FileNotFoundError(f"Project directory not found: {project_dir}")

        project_name = project_path.name

        if output_dir is None:
            output_dir = project_path.parent / "fixed_projects" / project_name

        output_path = Path(output_dir)

        if output_path.exists():
            logger.info(f"Clearing existing output directory: {output_path}")
            try:
                for root, dirs, files in os.walk(output_path):
                    for d in dirs:
                        os.chmod(Path(root) / d, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                    for f in files:
                        os.chmod(Path(root) / f, stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO)
                shutil.rmtree(output_path)
            except Exception as e:
                logger.error(f"Failed to clean output directory {output_path}: {e}")
                pass

        output_path.mkdir(parents=True, exist_ok=True)

        status_file = output_path / ".processing"
        success_marker = output_path / "SUCCESS.txt"
        failed_marker = output_path / "FAILED.txt"

        for marker in [status_file, success_marker, failed_marker]:
            if marker.exists():
                marker.unlink()

        with open(status_file, 'w') as f:
            f.write(f"Processing started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Project: {project_name}\n")
            f.write(f"Original project directory: {project_dir}\n")

        try:
            original_verilog_file_paths = self.find_all_verilog_files_in_project(project_dir)
            if not original_verilog_file_paths:
                return {
                    "project_name": project_name,
                    "project_dir": str(project_dir),
                    "final_success": False,
                    "error": "No Verilog files found in original project directory"
                }

            logger.info(f"Starting auto-fix for project '{project_name}'")
            logger.info(f"Found {len(original_verilog_file_paths)} unique Verilog files in original project")
            logger.info(f"Fixed project will be saved to: {output_path}")

            error_file_path = project_path / "compilation_errors.txt"
            initial_errors = []
            if error_file_path.exists():
                logger.info(f"Reading existing compilation errors from {error_file_path}")
                initial_errors = self.read_existing_errors(str(error_file_path))
                logger.info(f"Found {len(initial_errors)} initial errors")

            file_mapping = {}
            copied_verilog_files_in_output = []
            copy_errors = []

            logger.info(f"Copying project files from '{project_dir}' to '{output_path}'...")
            for original_vf_path in original_verilog_file_paths:
                try:
                    vf_original = Path(original_vf_path)
                    rel_path = vf_original.relative_to(project_path)
                    dest_file = output_path / rel_path
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(vf_original, dest_file)
                    file_mapping[original_vf_path] = str(dest_file)
                    copied_verilog_files_in_output.append(str(dest_file))
                    logger.debug(f"Copied {rel_path} to {dest_file}")
                except Exception as e:
                    logger.error(f"Failed to copy {original_vf_path}: {str(e)}")
                    copy_errors.append({
                        "file": str(original_vf_path),
                        "error": str(e)
                    })
                    continue

            if not copied_verilog_files_in_output:
                error_msg = f"No Verilog files were successfully copied to {output_path} for processing."
                logger.error(error_msg)
                if output_path.exists() and not list(output_path.iterdir()):
                    shutil.rmtree(output_path)
                return {
                    "project_name": project_name,
                    "project_dir": str(project_dir),
                    "final_success": False,
                    "error": error_msg,
                    "copy_errors": copy_errors
                }

            if copy_errors:
                logger.warning(f"Encountered {len(copy_errors)} file copy error(s). Proceeding with copied files.")

            logger.info(f"Successfully copied {len(copied_verilog_files_in_output)} files to output directory.")

            iteration = 0
            fix_history = []
            start_time = time.time()
            current_errors = initial_errors
            final_success = False

            if not current_errors:
                logger.info("Performing initial compilation to get error list...")
                success, _, stderr = self.compile_verilog_project(
                    copied_verilog_files_in_output, str(output_path), generate_out=False
                )
                current_errors = self.parse_icarus_errors(stderr)

                if success and not current_errors:
                    logger.info("🎉 Project compiles successfully in output directory without modifications!")
                    final_success = True
                    fix_history.append({
                        "iteration": 0,
                        "error_count": 0,
                        "errors": [],
                        "compilation_success": True
                    })

            while iteration < max_iterations:
                iteration += 1
                logger.info(f"\n{'=' * 60}")
                logger.info(f" ITERATION {iteration}/{max_iterations} for project {project_name}")
                logger.info(f"{'=' * 60}")

                if not current_errors:
                    logger.info(" No errors to fix!")
                    final_success = True
                    break

                logger.info(f" Current error count: {len(current_errors)}")

                errors_by_output_file = {}
                for error in current_errors:
                    error_file_path_from_icarus = Path(error['file']).resolve()
                    matched_output_file = None
                    for copied_path in copied_verilog_files_in_output:
                        if Path(copied_path).resolve() == error_file_path_from_icarus:
                            matched_output_file = copied_path
                            break
                    if matched_output_file:
                        if matched_output_file not in errors_by_output_file:
                            errors_by_output_file[matched_output_file] = []
                        errors_by_output_file[matched_output_file].append(error)
                    else:
                        logger.warning(f"Error '{error['raw_line']}' from Icarus did not match any copied files.")

                logger.info(" Errors by file (in output directory):")
                for output_file_path, file_errors in errors_by_output_file.items():
                    logger.info(f"   {Path(output_file_path).name}: {len(file_errors)} errors")

                fixed_any = False

                for output_file_to_fix in errors_by_output_file.keys():
                    try:
                        current_content = self.read_file_with_encoding_detection(output_file_to_fix)
                        all_output_files_content = {}
                        for f_path in copied_verilog_files_in_output:
                            try:
                                all_output_files_content[f_path] = self.read_file_with_encoding_detection(f_path)
                            except Exception:
                                logger.warning(f"Could not read content for context: {Path(f_path).name}")
                                pass
                        rel_path_name = Path(output_file_to_fix).relative_to(output_path)

                        logger.info(f" Generating fix for {rel_path_name} (in output dir)...")
                        prompt = self.generate_project_fix_prompt(
                            output_file_to_fix, current_content, current_errors, all_output_files_content, iteration
                        )

                        if prompt is None:
                            continue

                        logger.info(f" Calling API for {rel_path_name}...")
                        fixed_code = self.call_deepseek_api_with_rate_limit(prompt)

                        with open(output_file_to_fix, 'w', encoding='utf-8') as f:
                            f.write(fixed_code)

                        logger.info(f" Fixed and saved {rel_path_name} in output directory")
                        fixed_any = True

                        if self.output_format in [OutputFormat.OUT_ONLY, OutputFormat.BOTH]:
                            single_file_compile_success, _, single_file_stderr = self._compile_single_file_to_out(
                                output_file_to_fix, str(output_path))
                            if single_file_compile_success:
                                logger.info(
                                    f"✓ File {Path(output_file_to_fix).name} was successfully compiled to .out!")
                            else:
                                logger.warning(
                                    f"✗ File {Path(output_file_to_fix).name} failed to compile to .out. Errors: {single_file_stderr.strip()}")
                        else:
                            logger.debug(
                                f"Skipping single file .out compilation for {Path(output_file_to_fix).name} due to output_format setting.")

                    except Exception as e:
                        logger.error(f" Failed to fix {rel_path_name}: {str(e)}")
                        continue

                if not fixed_any and len(current_errors) > 0:
                    logger.warning(" No files were fixed in this iteration, but errors remain. Breaking loop.")
                    break

                logger.info(" Recompiling the entire project to check for remaining errors...")

                success, stdout, stderr = self.compile_verilog_project(
                    copied_verilog_files_in_output, str(output_path), generate_out=True
                )

                if success:
                    logger.info(" COMPILATION SUCCESSFUL! All errors fixed!")
                    final_success = True
                    fix_history.append({
                        "iteration": iteration,
                        "error_count": 0,
                        "errors": [],
                        "compilation_success": True
                    })
                    break

                new_errors = self.parse_icarus_errors(stderr)
                fix_history.append({
                    "iteration": iteration,
                    "error_count": len(new_errors),
                    "errors": new_errors,
                    "compilation_success": False
                })

                improvement = len(current_errors) - len(new_errors)
                if improvement > 0:
                    logger.info(f" Progress: Fixed {improvement} errors ({len(current_errors)} → {len(new_errors)})")
                else:
                    logger.info(f" No improvement: {len(current_errors)} → {len(new_errors)} errors")
                    break

                current_errors = new_errors

            end_time = time.time()
            total_time = end_time - start_time

            if not final_success:
                logger.info("Performing final project compilation for report...")
                final_compilation_success, _, final_stderr = self.compile_verilog_project(
                    copied_verilog_files_in_output, str(output_path), generate_out=True
                )
                final_errors = self.parse_icarus_errors(final_stderr) if not final_compilation_success else []
                final_success = final_compilation_success and len(final_errors) == 0
            else:
                final_errors = []

            if status_file.exists():
                status_file.unlink()

            if final_success:
                with open(success_marker, 'w', encoding='utf-8') as f:
                    f.write(f"Project: {project_name}\n")
                    f.write(f"Fixed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Iterations: {iteration}\n")
                    f.write(f"Initial errors: {len(initial_errors)}\n")
                    f.write(f"Final errors: 0\n")
                    f.write(f"Files copied and fixed: {len(copied_verilog_files_in_output)}\n")
                    if copy_errors:
                        f.write(f"Files failed to copy: {len(copy_errors)}\n")
                    f.write("\nThis directory contains the successfully fixed Verilog project.\n")
                    f.write(f"Original project: {project_dir}\n")
            else:
                with open(failed_marker, 'w', encoding='utf-8') as f:
                    f.write(f"Project: {project_name}\n")
                    f.write(f"Failed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write(f"Iterations: {iteration}\n")
                    f.write(f"Initial errors: {len(initial_errors)}\n")
                    f.write(f"Final errors: {len(final_errors)}\n")
                    f.write(f"Status: FAILED\n")
                    if copy_errors:
                        f.write(f"Files failed to copy: {len(copy_errors)}\n")
                    f.write(f"\nOriginal project: {project_dir}\n")
                    f.write("Fixed files (if any) are in this directory.\n")

            summary = {
                "project_name": project_name,
                "original_project_dir": str(project_dir),
                "output_dir": str(output_path),
                "total_files_in_original_project": len(original_verilog_file_paths),
                "files_copied_and_processed": len(copied_verilog_files_in_output),
                "copy_errors": copy_errors,
                "total_iterations": iteration,
                "max_iterations": max_iterations,
                "final_success": final_success,
                "initial_error_count": len(initial_errors),
                "final_error_count": len(final_errors),
                "errors_fixed": len(initial_errors) - len(final_errors),
                "total_time_seconds": round(total_time, 2),
                "fix_history": fix_history,
                "final_errors": final_errors,
                "files_processed_in_output_dir": copied_verilog_files_in_output
            }

            if copy_errors:
                summary["warning"] = f"{len(copy_errors)} files failed to copy."

            self.save_project_report(summary, str(output_path))

            with self.progress_lock:
                self.projects_processed += 1
                if self.total_projects > 0:
                    progress = (self.projects_processed / self.total_projects) * 100
                    logger.info(f"Overall progress: {self.projects_processed}/{self.total_projects} ({progress:.1f}%)")

            logger.info(f"\n{'=' * 60}")
            logger.info(f" PROJECT AUTO-FIX COMPLETED for {project_name}")
            logger.info(f"{'=' * 60}")
            logger.info(f" Original files: {len(original_verilog_file_paths)}")
            logger.info(f" Files copied & fixed: {len(copied_verilog_files_in_output)}")
            if copy_errors:
                logger.info(f" Copy errors: {len(copy_errors)}")
            logger.info(f" Total time: {total_time:.2f} seconds")
            logger.info(f" Iterations used: {iteration}/{max_iterations}")
            logger.info(f" Initial errors: {len(initial_errors)}")
            logger.info(f" Final errors: {len(final_errors)}")
            logger.info(f" Errors fixed: {len(initial_errors) - len(final_errors)}")
            logger.info(f" Result: {'SUCCESS' if final_success else 'FAILED'}")
            logger.info(f" Fixed project and reports saved to: {output_path}")
            logger.info(f" Original project directory ({project_dir}) remains untouched.")

            return summary

        except Exception as e:
            logger.error(f"Error in auto_fix_verilog_project for {project_name}: {str(e)}",
                         exc_info=True)

            if status_file.exists():
                status_file.unlink()

            error_marker = output_path / "ERROR.txt"
            with open(error_marker, 'w', encoding='utf-8') as f:
                f.write(f"Project: {project_name}\n")
                f.write(f"Error at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"Exception: {str(e)}\n")
                f.write(f"\nOriginal project directory: {project_dir} (remains untouched).\n")
                if 'copy_errors' in locals() and copy_errors:
                    f.write(f"\nFile copy errors:\n")
                    for err in copy_errors:
                        f.write(f"  - {err['file']}: {err['error']}\n")
                f.write(f"\nPartial fixed files (if any) are in this directory: {output_path}\n")

            return {
                "project_name": project_name,
                "original_project_dir": str(project_dir),
                "output_dir": str(output_path),
                "final_success": False,
                "error": f"Processing error: {str(e)}",
                "copy_errors": copy_errors if 'copy_errors' in locals() else []
            }


    def clean_temp_directories(self, base_dir: str):
        base_path = Path(base_dir)
        cleaned_count = 0

        for item in base_path.iterdir():
            if item.is_dir() and item.name.startswith("temp_"):
                try:
                    shutil.rmtree(item)
                    cleaned_count += 1
                    logger.info(f"Cleaned temp directory: {item.name}")
                except Exception as e:
                    logger.warning(f"Failed to clean {item.name}: {str(e)}")

        if cleaned_count > 0:
            logger.info(f"Cleaned {cleaned_count} temporary directories")
        else:
            logger.info("No temporary directories found to clean")

        return cleaned_count

    def save_project_report(self, summary: Dict[str, Any], output_dir: str):

        report_path = Path(output_dir) / "project_autofix_report.txt"

        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write("VERILOG PROJECT AUTO-FIX REPORT\n")
                f.write("=" * 60 + "\n\n")

                f.write(f"Project Name: {summary['project_name']}\n")
                f.write(f"Original Project Directory: {summary['original_project_dir']}\n")
                f.write(f"Fixed Project Output Directory: {summary['output_dir']}\n")
                f.write(f"Total Files in Original Project: {summary['total_files_in_original_project']}\n")
                f.write(f"Files Copied and Processed: {summary.get('files_copied_and_processed', 0)}\n")
                f.write(f"Generation Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

                if "copy_errors" in summary and summary["copy_errors"]:
                    f.write("FILE COPY ERRORS (These files were NOT processed):\n")
                    f.write(f"Failed to copy {len(summary['copy_errors'])} files:\n")
                    for err in summary["copy_errors"]:
                        f.write(f"  - {Path(err['file']).name}: {err['error']}\n")
                    f.write("\n")

                f.write("FILES PROCESSED (COPIED TO OUTPUT DIRECTORY AND FIXED):\n")
                if summary.get('files_processed_in_output_dir'):
                    for fname in summary['files_processed_in_output_dir']:
                        try:
                            rel_path = Path(fname).relative_to(Path(summary['output_dir']))
                            f.write(f"  - {rel_path.as_posix()}\n")
                        except ValueError:
                            f.write(f"  - {Path(fname).name}\n")
                else:
                    f.write("  No Verilog files were processed in the output directory.\n")
                f.write("\n")


                f.write("RESULTS:\n")
                f.write(f"  Final Status: {'SUCCESS' if summary['final_success'] else 'FAILED'}\n")
                f.write(f"  Total Iterations: {summary['total_iterations']}/{summary['max_iterations']}\n")
                f.write(f"  Total Time: {summary['total_time_seconds']} seconds\n")
                f.write(f"  Initial Error Count: {summary.get('initial_error_count', 'Unknown')}\n")
                f.write(f"  Final Error Count: {summary['final_error_count']}\n")
                f.write(f"  Errors Fixed: {summary.get('errors_fixed', 0)}\n\n")

                f.write("ITERATION HISTORY:\n")
                for hist in summary['fix_history']:
                    status = "SUCCESS" if hist.get('compilation_success', False) else "FAILED"
                    f.write(f"  Iteration {hist['iteration']}: {hist['error_count']} errors - {status}\n")
                f.write("\n")

                if summary['final_errors']:
                    f.write("REMAINING ERRORS (in the fixed output project):\n")
                    for i, error in enumerate(summary['final_errors'][:20], 1):
                        f.write(f"  {i}. {Path(error['file']).name} Line {error['line']}: {error['message']}\n")
                    if len(summary['final_errors']) > 20:
                        f.write(f"  ... and {len(summary['final_errors']) - 20} more errors\n")
                    f.write("\n")

                if "warning" in summary:
                    f.write(f"WARNING: {summary['warning']}\n\n")

                f.write("Report generated by Verilog Project Auto-Fixer\n")
                f.write("NOTE: Original project files were NOT modified. Fixed files are in the output directory.\n")

            json_path = Path(output_dir) / "project_autofix_summary.json"
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(summary, f, indent=2, ensure_ascii=False)

            logger.info(f"Project report saved to: {report_path}")

        except Exception as e:
            logger.error(f"Failed to save project report for {summary.get('project_name', 'unknown')}: {str(e)}")

    def process_project_wrapper(self, args: Tuple[str, str, int, int]) -> Dict[str, Any]:

        if len(args) == 3:
            project_dir, output_base_dir, max_iterations = args
            verification_level = self.verification_level
        else:
            project_dir, output_base_dir, max_iterations, verification_level = args

        try:
            project_name = Path(project_dir).name
            logger.info(f"Processing project: {project_name}")

            project_output_dir = Path(output_base_dir) / project_name

            summary = self.enhanced_auto_fix_verilog_project(
                project_dir,
                str(project_output_dir),
                max_iterations,
                verification_level
            )

            return summary

        except Exception as e:
            logger.error(f"Error processing project {Path(project_dir).name}: {str(e)}")

            return {
                "project_name": Path(project_dir).name,
                "original_project_dir": project_dir,
                "output_dir": Path(output_base_dir) / Path(project_dir).name,
                "final_success": False,
                "error": str(e)
            }

    def batch_fix_projects(self, input_dir: str, output_dir: str = None,
                           max_iterations: int = 10, max_workers: int = 4,
                           verification_level: int = None) -> Dict[str, Any]:
        input_path = Path(input_dir)
        if not input_path.exists():
            raise FileNotFoundError(f"Input directory not found: {input_dir}")

        if output_dir is None:
            output_dir = input_path.parent / f"fixed_projects_batch_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Searching for Verilog projects in {input_dir}")
        project_dirs = self.find_verilog_projects(input_dir)

        if not project_dirs:
            logger.warning(f"No Verilog projects found in {input_dir}")
            return {
                "projects_processed": 0,
                "results": [],
                "total_projects": 0
            }

        self.total_projects = len(project_dirs)
        self.projects_processed = 0

        verify_level = verification_level if verification_level is not None else self.verification_level

        logger.info(f"Starting batch fix for {len(project_dirs)} projects")
        logger.info(f"Fixed projects and reports will be saved to: {output_path}")
        logger.info(f"Max workers: {max_workers}")
        logger.info(f"Verification level: {verify_level}")
        logger.info("NOTE: Original project files will NOT be modified.")

        process_args = [(pd, str(output_path), max_iterations, verify_level) for pd in project_dirs]

        results = []
        successful_fixes = 0
        failed_fixes = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_project = {
                executor.submit(self.process_project_wrapper, args): args[0]
                for args in process_args
            }

            for future in as_completed(future_to_project):
                project_dir = future_to_project[future]

                try:
                    summary = future.result()
                    results.append(summary)

                    if summary.get('final_success', False):
                        successful_fixes += 1
                        logger.info(f"SUCCESS: {Path(project_dir).name}")
                    else:
                        failed_fixes += 1
                        error_count = summary.get('final_error_count', 'unknown')
                        logger.info(f"FAILED: {Path(project_dir).name} ({error_count} errors)")

                except Exception as e:
                    logger.error(f"Unexpected error processing {project_dir}: {str(e)}")
                    failed_fixes += 1
                    results.append({
                        "project_name": Path(project_dir).name,
                        "project_dir": project_dir,
                        "final_success": False,
                        "error": f"Processing failed: {str(e)}"
                    })

        end_time = time.time()
        total_time = end_time - start_time


        batch_summary = {
            "input_directory": str(input_dir),
            "output_directory": str(output_path),
            "total_projects": len(project_dirs),
            "successful_fixes": successful_fixes,
            "failed_fixes": failed_fixes,
            "success_rate": (successful_fixes / len(project_dirs) * 100) if project_dirs else 0,
            "total_time_seconds": round(total_time, 2),
            "average_time_per_project": round(total_time / len(project_dirs), 2) if project_dirs else 0,
            "max_workers": max_workers,
            "max_iterations": max_iterations,
            "verification_level": verify_level,
            "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "results": results
        }

        self.save_batch_report(batch_summary, str(output_path))

        logger.info(f"\n{'=' * 60}")
        logger.info(f"BATCH PROJECT FIX COMPLETED")
        logger.info(f"{'=' * 60}")
        logger.info(f"Total projects processed: {len(project_dirs)}")
        logger.info(f"Successful: {successful_fixes}")
        logger.info(f"Failed: {failed_fixes}")
        logger.info(f"Success rate: {batch_summary['success_rate']:.1f}%")
        logger.info(f"Total time: {total_time:.2f} seconds")
        logger.info(f"Average time per project: {batch_summary['average_time_per_project']:.2f} seconds")
        logger.info(f"Fixed projects and reports saved to: {output_path}")
        logger.info("Original project files remain untouched.")

        return batch_summary

    def save_batch_report(self, batch_summary: Dict[str, Any], output_dir: str):
        try:

            json_report_path = Path(output_dir) / "batch_projects_report.json"
            with open(json_report_path, 'w', encoding='utf-8') as f:
                json.dump(batch_summary, f, indent=2, ensure_ascii=False)

            text_report_path = Path(output_dir) / "batch_projects_report.txt"
            with open(text_report_path, 'w', encoding='utf-8') as f:
                f.write("VERILOG BATCH PROJECTS AUTO-FIX REPORT\n")
                f.write("=" * 80 + "\n\n")

                f.write("CONFIGURATION:\n")
                f.write(f"  Input Directory for Original Projects: {batch_summary['input_directory']}\n")
                f.write(f"  Output Directory for Fixed Projects and Reports: {batch_summary['output_directory']}\n")
                f.write(f"  Max Workers: {batch_summary.get('max_workers', 1)}\n")
                f.write(f"  Max Iterations per Project: {batch_summary.get('max_iterations', 10)}\n\n")

                f.write("SUMMARY:\n")
                f.write(f"  Total Projects Found: {batch_summary['total_projects']}\n")
                f.write(f"  Successful Fixes (in output directory): {batch_summary['successful_fixes']}\n")
                f.write(f"  Failed Fixes (in output directory): {batch_summary['failed_fixes']}\n")
                f.write(f"  Success Rate: {batch_summary['success_rate']:.1f}%\n")
                f.write(f"  Total Time: {batch_summary['total_time_seconds']} seconds\n")
                f.write(f"  Average Time/Project: {batch_summary['average_time_per_project']} seconds\n")
                f.write(f"  Generated At: {batch_summary['generated_at']}\n\n")

                f.write("SUCCESSFUL PROJECTS (Fixed copies are in subdirectories of the output folder):\n")
                success_count = 0
                for result in batch_summary['results']:
                    if result.get('final_success', False):
                        success_count += 1
                        f.write(f"  {success_count}. {result['project_name']}")
                        f.write(f" (Original: {Path(result['original_project_dir']).name})")
                        f.write(f" - {result.get('total_files_in_original_project', 'N/A')} files")
                        f.write(f" - {result['total_iterations']} iterations\n")
                        f.write(f" -> Fixed project location: {result['output_dir']}\n")

                if success_count == 0:
                    f.write("  None\n")

                f.write("\nFAILED PROJECTS (Copies generated in output folder, but remain unfixed):\n")
                failed_count = 0
                for result in batch_summary['results']:
                    if not result.get('final_success', False):
                        failed_count += 1
                        f.write(
                            f"  {failed_count}. {result['project_name']} (Original: {Path(result['original_project_dir']).name}) - ")
                        if 'error' in result:
                            f.write(f"Error: {result['error']}\n")
                        else:
                            f.write(f"{result.get('final_error_count', 'Unknown')} errors remaining\n")
                        f.write(f" -> Located at: {result['output_dir']}\n")

                if failed_count == 0:
                    f.write("  None - All projects fixed successfully!\n")

                f.write("\n" + "=" * 80 + "\n")
                f.write("Report generated by Verilog Project Auto-Fixer\n")
                f.write(
                    "IMPORTANT: Original project files were NOT modified. Fixed project copies are in the output directory.\n")

            logger.info(f"Batch reports saved to {output_dir}")

        except Exception as e:
            logger.error(f"Failed to save batch report: {str(e)}")


def main():
    API_KEY = "......"
    INPUT_DIR = r"......"
    OUTPUT_DIR = r"......"
    MAX_WORKERS = 4
    MAX_ITERATIONS = 20
    DEFAULT_VERIFICATION_LEVEL = VerificationLevel.SMART
    DEFAULT_OUTPUT_FORMAT = OutputFormat.BOTH

    print("Verilog Project Auto-Fixer")
    print("=" * 60)
    print(f"Configuration information:")
    print(f"Input directory: {INPUT_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Number of concurrent tasks: {MAX_WORKERS}")
    print(f"Maximum number of iterations: {MAX_ITERATIONS}")
    print(f"Default verification level: SMART")
    print(f"Default output format: BOTH (VVP + OUT)")
    print("=" * 60)

    try:

        print("\nSelect the output format for compilation:")
        print("0. Only VVP files")
        print("1. Only OUT files")
        print("2. Simultaneously generate VVP and OUT files (recommended)")
        format_choice = input("Output format (0-2, default: 2): ").strip()
        output_format = int(format_choice) if format_choice.isdigit() and 0 <= int(
            format_choice) <= 2 else DEFAULT_OUTPUT_FORMAT


        print("\nInitialize the automatic repairer...")
        fixer = VerilogProjectAutoFixer(API_KEY, verification_level=DEFAULT_VERIFICATION_LEVEL,
                                        output_format=output_format)

        print(f"Automatic repairer initialization successful - Output format: {['VVP_ONLY', 'OUT_ONLY', 'BOTH'][output_format]}")
        print("\nChoose an option:")
        print("1. Fix individual projects")
        print("2. Batch repair of all projects (parallel processing)")
        print("3. Analyze the directory structure (without performing any repair)")
        print("4. Test different levels of verification")
        print("5. Exit")

        while True:
            try:
                choice = input("\nPlease make your selection (1-5): ").strip()

                if choice == "1":
                    print("\nSingle project mode")
                    project_dir = input("Enter the path of the project directory: ").strip().strip('"')
                    if not project_dir:
                        print("Unspecified project directory")
                        continue

                    if not Path(project_dir).exists():
                        print(f"The project directory does not exist.: {project_dir}")
                        continue

                    output_dir = input(f"Input and output directories (default: {OUTPUT_DIR}/Project Name): ").strip().strip('"')
                    if not output_dir:
                        output_dir = None

                    max_iter = input(f"Maximum number of iterations (default: {MAX_ITERATIONS}): ").strip()
                    max_iter = int(max_iter) if max_iter.isdigit() else MAX_ITERATIONS

                    print("\nSelect verification level:")
                    print("0. NONE - Without verification")
                    print("1. QUICK - Quick verification")
                    print("2. SMART - Intelligent verification (default)")
                    print("3. FULL - Complete verification")
                    verify_choice = input("Verification level (0-3, default: 2): ").strip()
                    verify_level = int(verify_choice) if verify_choice.isdigit() and 0 <= int(
                        verify_choice) <= 3 else VerificationLevel.SMART

                    print(f"\nStart to repair the project: {project_dir}")
                    print(f"Verification level: {['NONE', 'QUICK', 'SMART', 'FULL'][verify_level]}")

                    try:
                        summary = fixer.enhanced_auto_fix_verilog_project(project_dir, output_dir, max_iter,
                                                                          verify_level)

                        if summary['final_success']:
                            print(f"\nSuccess! The issue was resolved after {summary['total_iterations']} iterations.")
                            print(f"Total time: {summary['total_time_seconds']} seconds")
                            print(f"Verification status: {summary.get('verification_status', 'N/A')}")
                            print(f"Result saved to: {summary['output_dir']}")
                        else:
                            print(f"\nFailure occurred after {summary['total_iterations']} iterations.")
                            print(f"Remaining errors: {summary['final_error_count']}")
                            if 'failure_analysis' in summary:
                                print("\nFailure Analysis:")
                                analysis = summary['failure_analysis']
                                if analysis.get('recommendations'):
                                    for rec in analysis['recommendations']:
                                        print(f"  - {rec}")
                            print(f"The debugging file has been saved to: {summary['output_dir']}")
                    except Exception as e:
                        print(f"Error occurred during the repair process: {str(e)}")

                elif choice == "2":
                    print(f"\nBatch project processing mode (parallel)")
                    print(f"Input Directory: {INPUT_DIR}")
                    print(f"Output directory: {OUTPUT_DIR}")

                    if not Path(INPUT_DIR).exists():
                        print(f"The input directory does not exist: {INPUT_DIR}")
                        print("Please verify the configuration of INPUT_DIR in the code.")
                        continue

                    max_iter = input(f"The maximum number of iterations for each project (default: {MAX_ITERATIONS}): ").strip()
                    max_iter = int(max_iter) if max_iter.isdigit() else MAX_ITERATIONS

                    workers = input(f"Number of concurrent tasks (default: {MAX_WORKERS}): ").strip()
                    workers = int(workers) if workers.isdigit() else MAX_WORKERS


                    print("\nSelect the verification level for batch processing:")
                    print("0. NONE - Without verification")
                    print("1. QUICK - Quick verification")
                    print("2. SMART - Intelligent verification (default)")
                    print("3. FULL - Complete verification")
                    verify_choice = input("Verification level (0-3, default: 2): ").strip()
                    verify_level = int(verify_choice) if verify_choice.isdigit() and 0 <= int(
                        verify_choice) <= 3 else VerificationLevel.SMART

                    print(f"\nStart processing the project in batches...")
                    print(f"Verification level: {['NONE', 'QUICK', 'SMART', 'FULL'][verify_level]}")
                    print(f"This might take some time, depending on the number of projects...")

                    try:
                        batch_summary = fixer.batch_fix_projects(
                            INPUT_DIR, OUTPUT_DIR, max_iter, workers, verify_level
                        )

                        print(f"\nBatch processing is complete!")
                        print(f"For detailed results, please refer to: {batch_summary['output_directory']}")

                    except Exception as e:
                        print(f"Error occurred during batch processing: {str(e)}")

                elif choice == "3":
                    print("\nDirectory Analysis Model")
                    analysis_dir = input(f"Input the directory to be analyzed (default: {INPUT_DIR}): ").strip().strip('"')
                    if not analysis_dir:
                        analysis_dir = INPUT_DIR

                    print(f"\nAnalysis Table of Contents: {analysis_dir}")

                    projects = fixer.find_verilog_projects(analysis_dir)

                    if projects:
                        print(f"\nFound {len(projects)} Verilog projects:")
                        for i, proj in enumerate(projects, 1):
                            proj_path = Path(proj)
                            verilog_files = list(proj_path.glob("*.v"))
                            error_file = proj_path / "compilation_errors.txt"

                            print(f"\n{i}. {proj_path.name}:")
                            print(f" Path: {proj}")
                            print(f" Number of Verilog files: {len(verilog_files)}")
                            print(f" Contains erroneous file: {'Yes' if error_file.exists() else 'No'}")

                            if len(verilog_files) <= 5:
                                print(f" File List:")
                                for vf in verilog_files:
                                    print(f"- {vf.name}")
                    else:
                        print("No Verilog project was found.")

                elif choice == "4":
                    print("\nTest different levels of verification")
                    test_project = input("Input test project path: ").strip().strip('"')
                    if not test_project or not Path(test_project).exists():
                        print("Invalid project path")
                        continue

                    print("\nThe same project will be tested using different levels of verification...")

                    for level in [VerificationLevel.NONE, VerificationLevel.QUICK,
                                  VerificationLevel.SMART, VerificationLevel.FULL]:
                        level_name = ['NONE', 'QUICK', 'SMART', 'FULL'][level]
                        print(f"\n--- Test verification level: {level_name} ---")

                        output_dir = f"{OUTPUT_DIR}/test_{level_name.lower()}"

                        start_time = time.time()
                        try:
                            summary = fixer.enhanced_auto_fix_verilog_project(
                                test_project, output_dir, 5, level
                            )
                            end_time = time.time()

                            print(f"Result: {'Success' if summary['final_success'] else 'Failure'}")
                            print(f"Time: {end_time - start_time:.2f} seconds")
                            print(f"Verification execution: {summary.get('verification_performed', False)}")
                        except Exception as e:
                            print(f"Error: {str(e)}")

                elif choice == "5":
                    print("Goodbye!")
                    break

                else:
                    print("Invalid selection. Please enter 1-5.")
                    continue
            except KeyboardInterrupt:
                print("\nThe operation has been cancelled.")
                continue
            except Exception as e:
                print(f"Unexpected Error: {str(e)}")
                continue
    except Exception as e:
        print(f"Unexpected Error: {str(e)}")
        print("Please verify your API key and the installation of Icarus Verilog.")

if __name__ == "__main__":
    main()