import json, re, math, csv, argparse, os, sys
from pathlib import Path
from collections import Counter, defaultdict
import statistics
import hashlib
import shutil
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import logging
import fnmatch

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


@dataclass
class AnalysisConfig:
    levels: List[str] = None

    weights: Dict[str, float] = None
    loc_weight: float = 0.08

    q1_threshold: float = 0.25
    q3_threshold: float = 0.75

    log_transform_features: List[str] = None
    normalization_range: Tuple[float, float] = (0.0, 2.0)
    percentile_range: Tuple[float, float] = (0.05, 0.95)

    max_filename_length: int = 180
    batch_size: int = 1000

    def __post_init__(self):
        if self.levels is None:
            self.levels = ["Simple", "Intermediate", "Advanced"]

        if self.weights is None:
            self.weights = {
                "T": 0.20,
                "S": 0.25,
                "C": 0.20,
                "D": 0.15,
                "P": 0.08,
                "I": 0.07,
                "R": 0.03,
                "M": 0.02
            }

        if self.log_transform_features is None:
            self.log_transform_features = [
                "T_instances", "D_total_expr", "L_LOC",
                "I_bitsum", "R_reg_bits"
            ]


class VerilogComplexityAnalyzer:


    def __init__(self, config: AnalysisConfig = None):
        self.config = config or AnalysisConfig()
        self.expr_nodes = {
            "Plus", "Minus", "Times", "Div", "Mod",
            "And", "Or", "Xor", "Xnor", "Unot", "Ulnot", "Uplus", "Uminus",
            "Eq", "NotEq", "Eql", "NotEql", "Lt", "Le", "Gt", "Ge",
            "Concat", "Repeat", "Pointer", "Partselect",
            "Sll", "Srl", "Sla", "Sra", "ShiftLeft", "ShiftRight"
        }

    def strip_verilog_comments(self, text: str) -> str:

        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        text = re.sub(r"//.*?$", "", text, flags=re.M)
        return text

    def parse_verilog_int(self, s: str) -> int:

        s = str(s).strip()
        m = re.fullmatch(r"(?i)(\d+)\s*'([bdho])\s*([0-9a-f_xz]+)", s)
        if m:
            base = m.group(2).lower()
            digits = m.group(3).lower().replace("_", "")
            base_map = {"b": 2, "d": 10, "h": 16, "o": 8}
            b = base_map.get(base, 10)
            digits = re.sub(r"[xz]", "0", digits)
            try:
                return int(digits, b)
            except Exception:
                return 0
        try:
            return int(s, 10)
        except Exception:
            m2 = re.search(r"(-?\d+)", s)
            return int(m2.group(1)) if m2 else 0

    def each_node(self, node):

        if not isinstance(node, dict):
            return
        yield node
        for ch in node.get("children") or []:
            if isinstance(ch, dict):
                yield from self.each_node(ch)

    def eval_expr(self, node, params):

        if not isinstance(node, dict):
            return None
        t = node.get("type")

        if t == "IntConst":
            return self.parse_verilog_int(node.get("attributes", {}).get("value", "0"))

        if t == "Identifier":
            name = (node.get("attributes") or {}).get("name")
            if isinstance(name, str) and name in params:
                return params[name]
            return None

        if t in {"Uplus", "Uminus", "Unot", "Ulnot"}:
            v = self.eval_expr((node.get("children") or [None])[0], params)
            if v is None:
                return None
            if t == "Uplus":  return +v
            if t == "Uminus": return -v
            if t in {"Unot", "Ulnot"}: return 0 if v else 1

        if t in {"Plus", "Minus", "Times", "Div", "Mod", "And", "Or", "Xor",
                 "Xnor", "Eq", "NotEq", "Lt", "Le", "Gt", "Ge"}:
            a, b = (node.get("children") or [None, None])[:2]
            va, vb = self.eval_expr(a, params), self.eval_expr(b, params)
            if va is None or vb is None:
                return None
            try:
                op_map = {
                    "Plus": lambda x, y: x + y,
                    "Minus": lambda x, y: x - y,
                    "Times": lambda x, y: x * y,
                    "Div": lambda x, y: x // y if y != 0 else None,
                    "Mod": lambda x, y: x % y if y != 0 else None,
                    "And": lambda x, y: x & y,
                    "Or": lambda x, y: x | y,
                    "Xor": lambda x, y: x ^ y,
                    "Xnor": lambda x, y: ~(x ^ y),
                    "Eq": lambda x, y: int(x == y),
                    "NotEq": lambda x, y: int(x != y),
                    "Lt": lambda x, y: int(x < y),
                    "Le": lambda x, y: int(x <= y),
                    "Gt": lambda x, y: int(x > y),
                    "Ge": lambda x, y: int(x >= y)
                }
                result = op_map.get(t, lambda x, y: None)(va, vb)
                return result
            except Exception:
                return None

        return None

    def collect_params(self, ast_root):

        params = {}
        for n in self.each_node(ast_root):
            if n.get("type") == "Parameter":
                attr = n.get("attributes") or {}
                name = attr.get("name")
                ch = n.get("children") or []
                v = None
                if ch:
                    rv = ch[0]
                    rvch = (rv.get("children") or []) if isinstance(rv, dict) else []
                    if rvch:
                        v = self.eval_expr(rvch[0], params)
                if isinstance(name, str) and v is not None:
                    params[name] = v
        return params

    def width_from_widthnode(self, width_node, params):

        ch = width_node.get("children") or []
        if len(ch) < 2:
            return 1
        msb = self.eval_expr(ch[0], params)
        lsb = self.eval_expr(ch[1], params)
        if msb is None or lsb is None:
            return 1
        return abs(int(msb) - int(lsb)) + 1

    def extract_ports(self, ast_root, params):

        port_names = []
        bitsum = 0
        port_n = 0
        max_port_width = 0

        for n in self.each_node(ast_root):
            if n.get("type") in {"Input", "Output", "Inout"}:
                port_n += 1
                name = (n.get("attributes") or {}).get("name", "")
                port_names.append(str(name))
                w = 1
                for ch in n.get("children") or []:
                    if isinstance(ch, dict) and ch.get("type") == "Width":
                        w = self.width_from_widthnode(ch, params)
                        break
                w = max(1, int(w))
                bitsum += w
                max_port_width = max(max_port_width, w)

        return port_n, bitsum, port_names, max_port_width

    def detect_protocol_hits(self, port_names):

        proto_hints = {
            "handshake": ["valid", "ready", "req", "ack"],
            "axi": ["awvalid", "wvalid", "bvalid", "arvalid", "rvalid", "awaddr",
                    "araddr", "wdata", "rdata", "wready", "rready"],
            "ahb": ["hready", "hresp", "haddr", "hwrite", "htrans", "hsize", "hburst"],
            "apb": ["psel", "penable", "pwrite", "paddr", "pwdata", "prdata", "pready"],
            "spi": ["sclk", "mosi", "miso", "cs", "ss"],
            "i2c": ["scl", "sda"],
            "uart": ["tx", "rx", "txd", "rxd"]
        }

        s = " ".join(port_names).lower()
        hits = set()
        for k, keys in proto_hints.items():
            if any(t in s for t in keys):
                hits.add(k)
        return sorted(hits)

    def extract_if_depth(self, node):

        max_depth = 0
        total_if_count = 0

        def dfs(n, depth):
            nonlocal max_depth, total_if_count
            if not isinstance(n, dict):
                return
            if n.get("type") == "IfStatement":
                depth += 1
                total_if_count += 1
                max_depth = max(max_depth, depth)
            for ch in n.get("children") or []:
                dfs(ch, depth)

        dfs(node, 0)
        return max_depth, total_if_count

    def count_case_branches(self, ast_root):

        branches = 0
        case_count = 0

        for n in self.each_node(ast_root):
            if n.get("type") in {"CaseStatement", "Case"}:
                case_count += 1
                ch = n.get("children") or []
                if ch:
                    branch_count = max(0, len(ch) - 1)
                    branches += branch_count

        return branches, case_count

    def expression_depth_and_ops(self, ast_root):

        ops = set()
        max_depth = 0
        total_expr_count = 0

        def depth(n):
            nonlocal ops, max_depth, total_expr_count
            if not isinstance(n, dict):
                return 0
            t = n.get("type")
            if t in self.expr_nodes:
                ops.add(t)
                total_expr_count += 1
            ch = n.get("children") or []
            if not ch:
                return 1 if t in {"Identifier", "IntConst"} else 0
            child_depths = [depth(c) for c in ch if isinstance(c, dict)]
            d = 1 + (max(child_depths) if child_depths else 0)
            if t in self.expr_nodes:
                max_depth = max(max_depth, d)
            return d

        for n in self.each_node(ast_root):
            depth(n)
        return max_depth, len(ops), total_expr_count

    def clock_domains_and_async(self, always_nodes):

        clocks = []
        async_resets = 0
        always_ff = 0
        complex_sensitivity = 0

        for a in always_nodes:
            sens = None
            for ch in a.get("children") or []:
                if isinstance(ch, dict) and ch.get("type") == "SensList":
                    sens = ch
                    break

            if sens:
                edges = []
                for s in sens.get("children") or []:
                    if isinstance(s, dict) and s.get("type") == "Sens":
                        ty = (s.get("attributes") or {}).get("type", "")
                        idch = (s.get("children") or [None])[0]
                        sig = idch.get("attributes", {}).get("name") if isinstance(idch, dict) else None
                        if ty in {"posedge", "negedge"} and sig:
                            edges.append((ty, sig))

                if edges:
                    always_ff += 1
                    clk_names = [sig for ty, sig in edges if ty in {"posedge", "negedge"}]
                    if clk_names:
                        clocks.append(clk_names[0])
                    if len(edges) >= 2:
                        async_resets += 1
                    if len(edges) >= 3:
                        complex_sensitivity += 1

        return len(set(clocks)), async_resets, always_ff, complex_sensitivity

    def detect_fsms(self, ast_root):

        has_case = any(n.get("type") in {"CaseStatement", "Case"} for n in self.each_node(ast_root))

        reg_names = [(n.get("attributes") or {}).get("name", "").lower()
                     for n in self.each_node(ast_root) if n.get("type") in {"Reg"}]

        state_vars = [x for x in reg_names if
                      x == "state" or x.endswith("_state") or
                      x.startswith("state_") or "state" in x]

        states = 0
        if has_case and state_vars:
            for n in self.each_node(ast_root):
                if n.get("type") in {"CaseStatement", "Case"}:
                    ch = n.get("children") or []
                    if ch:
                        states = max(states, len(ch) - 1)

        fsm_count = 1 if (has_case and state_vars) else 0
        return fsm_count, max(states, len(state_vars) * 2 if state_vars else 0)

    def analyze_register_complexity(self, ast_root):

        reg_count = 0
        reg_bits = 0
        params = self.collect_params(ast_root)

        for n in self.each_node(ast_root):
            if n.get("type") == "Reg":
                reg_count += 1
                width = 1
                for ch in n.get("children") or []:
                    if isinstance(ch, dict) and ch.get("type") == "Width":
                        width = max(1, self.width_from_widthnode(ch, params))
                        break
                reg_bits += width

        return reg_count, reg_bits

    def detect_memory_structures(self, ast_root):

        memory_count = 0
        memory_complexity = 0

        for n in self.each_node(ast_root):
            node_type = n.get("type", "")
            if any(mem_type in node_type for mem_type in ["Memory", "Ram", "Rom"]):
                memory_count += 1
                memory_complexity += 5

        return memory_count, memory_complexity

    def topology_meta(self, ast_root):

        modules = [n for n in self.each_node(ast_root) if n.get("type") == "ModuleDef"]
        instances = [n for n in self.each_node(ast_root) if n.get("type") in {"Instance", "InstanceList"}]


        if len(instances) == 0:
            depth = 1
        elif len(instances) <= 3:
            depth = 2
        elif len(instances) <= 10:
            depth = 3
        else:
            depth = min(5, 2 + len(instances) // 10)

        max_fout = min(10, len(instances))
        max_fin = len(instances)
        width = max(1, int(math.log2(max(1, len(instances)))))

        return len(modules), depth, len(instances), max_fout, max_fin, width

    def extract_params_gens(self, ast_root):

        params = [n for n in self.each_node(ast_root) if n.get("type") == "Parameter"]
        gens = [n for n in self.each_node(ast_root) if n.get("type") in {"Generate", "Genvar"}]

        param_complexity = 0
        for p in params:
            param_complexity += 1
            name = (p.get("attributes") or {}).get("name", "").lower()
            if any(hint in name for hint in ["width", "depth", "size", "len"]):
                param_complexity += 2

        return len(params), len(gens), param_complexity

    def compute_loc_from_source(self, ast_root):

        code = ast_root.get("source_code") or ""
        if not code:
            node_count = sum(1 for _ in self.each_node(ast_root))
            return max(10, node_count // 3)

        text = self.strip_verilog_comments(code)
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return len(lines)

    def collect_features_from_ast(self, ast_root, json_path: Path):

        try:
            param_map = self.collect_params(ast_root)

            port_n, port_bits, port_names, max_port_width = self.extract_ports(ast_root, param_map)
            proto_hits = self.detect_protocol_hits(port_names)

            always_nodes = [n for n in self.each_node(ast_root) if n.get("type") == "Always"]
            clk_domains, async_resets, always_ff, complex_sens = self.clock_domains_and_async(always_nodes)

            total_if_depth = 0
            total_if_count = 0
            for a in always_nodes:
                depth, count = self.extract_if_depth(a)
                total_if_depth = max(total_if_depth, depth)
                total_if_count += count

            case_branches, case_count = self.count_case_branches(ast_root)
            fsm_n, fsm_states = self.detect_fsms(ast_root)

            expr_depth, ops_var, total_expr = self.expression_depth_and_ops(ast_root)
            reg_count, reg_bits = self.analyze_register_complexity(ast_root)
            pipe_stages = min(reg_count // 4, 10)

            memory_count, memory_complexity = self.detect_memory_structures(ast_root)

            params_n, gen_n, param_complexity = self.extract_params_gens(ast_root)

            modules_n, hier_depth, instances_n, max_fout, max_fin, hier_width = self.topology_meta(ast_root)

            features = {
                "T_modules": modules_n,
                "T_depth": hier_depth,
                "T_instances": instances_n,
                "T_max_fanout": max_fout,
                "T_max_fanin": max_fin,
                "T_hier_width": hier_width,

                "S_always_ff": always_ff,
                "S_clk_domains": clk_domains,
                "S_async_resets": async_resets,
                "S_complex_sens": complex_sens,

                "C_if_depth": total_if_depth,
                "C_case_branches": case_branches,
                "C_fsms": fsm_n,
                "C_states": fsm_states,
                "C_total_if": total_if_count,
                "C_case_count": case_count,

                "D_expr_depth": expr_depth,
                "D_ops_variety": ops_var,
                "D_pipeline_stages": pipe_stages,
                "D_total_expr": total_expr,

                "P_params": params_n,
                "P_generate": gen_n,
                "P_complexity": param_complexity,

                "I_ports": port_n,
                "I_bitsum": port_bits,
                "I_protocol_hits": len(proto_hits),
                "I_max_port_width": max_port_width,

                "R_reg_count": reg_count,
                "R_reg_bits": reg_bits,
                "R_memory_count": memory_count,
                "R_memory_complexity": memory_complexity,

                "M_node_count": sum(1 for _ in self.each_node(ast_root)),

                "L_LOC": int(self.compute_loc_from_source(ast_root))
            }

            extras = {
                "protocols": proto_hits,
                "port_names": port_names,
                "verilog_path": str(json_path)
            }

            return features, extras

        except Exception as e:
            logger.error(f"特征提取失败 {json_path}: {e}")
            default_features = {f"{prefix}_{suffix}": 0
                                for prefix in ["T", "S", "C", "D", "P", "I", "R", "M"]
                                for suffix in ["count", "depth", "complexity"]}
            default_features["L_LOC"] = 10
            return default_features, {"protocols": [], "port_names": [], "verilog_path": str(json_path)}

    def robust_percentile_bounds(self, vals):
        if not vals:
            return 0.0, 1.0
        vs = sorted(vals)
        n = len(vs)

        if n <= 3:
            return float(vs[0]), float(vs[-1]) + 1.0

        p_low, p_high = self.config.percentile_range
        i_low = max(0, int(p_low * n))
        i_high = min(n - 1, int(p_high * n))

        p_low_val = vs[i_low]
        p_high_val = vs[i_high]

        if p_high_val <= p_low_val:
            p_high_val = p_low_val + max(1.0, p_low_val * 0.5)

        return float(p_low_val), float(p_high_val)

    def normalize_features(self, all_feat_dicts, include_loc_in_score=False):
        keys = set()
        for f in all_feat_dicts:
            keys.update(f.keys())
        if not include_loc_in_score and "L_LOC" in keys:
            keys.remove("L_LOC")

        bounds = {}
        for k in keys:
            vals = [float(f.get(k, 0)) for f in all_feat_dicts]
            if not vals or all(v == 0 for v in vals):
                bounds[k] = (0.0, 1.0)
                continue

            if k in self.config.log_transform_features:
                vals = [math.log10(max(1, v)) for v in vals]

            bounds[k] = self.robust_percentile_bounds(vals)

        normed = []
        eps = 1e-9

        for f in all_feat_dicts:
            z = {}
            for k in keys:
                x = float(f.get(k, 0))

                if k in self.config.log_transform_features:
                    x = math.log10(max(1, x))

                p_low, p_high = bounds[k]
                denom = (p_high - p_low) if (p_high - p_low) > eps else 1.0

                normalized = (x - p_low) / denom
                z[k] = max(self.config.normalization_range[0],
                           min(self.config.normalization_range[1], normalized))

            if include_loc_in_score and "L_LOC" in f:
                loc_val = math.log10(max(1, float(f.get("L_LOC", 0))))
                if "L_LOC" in bounds:
                    loc_bounds = bounds["L_LOC"]
                    loc_p_low, loc_p_high = loc_bounds
                    denom = (loc_p_high - loc_p_low) if (loc_p_high - loc_p_low) > eps else 1.0
                    z["L_LOC"] = max(self.config.normalization_range[0],
                                     min(self.config.normalization_range[1],
                                         (loc_val - loc_p_low) / denom))

            normed.append(z)

        return normed, bounds

    def group_avg_from_normed(self, z, group_prefix):
        if group_prefix == "L":
            return z.get("L_LOC", 0.0)

        ks = [k for k in z if k.startswith(group_prefix + "_")]
        if not ks:
            return 0.0

        weight_maps = {
            "T": {"T_instances": 0.4, "T_depth": 0.3, "T_modules": 0.15,
                  "T_max_fanout": 0.1, "T_max_fanin": 0.05},
            "S": {"S_always_ff": 0.3, "S_clk_domains": 0.3, "S_async_resets": 0.2,
                  "S_complex_sens": 0.2},
            "C": {"C_fsms": 0.3, "C_states": 0.25, "C_if_depth": 0.2,
                  "C_case_branches": 0.15, "C_total_if": 0.1},
            "D": {"D_expr_depth": 0.3, "D_ops_variety": 0.25, "D_total_expr": 0.25,
                  "D_pipeline_stages": 0.2}
        }

        weights = weight_maps.get(group_prefix, {k: 1.0 for k in ks})

        total_weight = 0.0
        weighted_sum = 0.0

        for k in ks:
            weight = weights.get(k, 1.0)
            weighted_sum += z[k] * weight
            total_weight += weight

        return weighted_sum / total_weight if total_weight > 0 else 0.0

    def assign_levels_quantile(self, scores):
        if not scores:
            return [], {"q1": 0, "q3": 0, "mode": "quantile"}

        sorted_scores = sorted(scores)
        n = len(sorted_scores)

        q1_idx = max(0, int(self.config.q1_threshold * (n - 1)))
        q3_idx = min(n - 1, int(self.config.q3_threshold * (n - 1)))
        q1 = sorted_scores[q1_idx]
        q3 = sorted_scores[q3_idx]

        levels = []
        for s in scores:
            if s <= q1:
                levels.append("Simple")
            elif s <= q3:
                levels.append("Intermediate")
            else:
                levels.append("Advanced")

        return levels, {"q1": q1, "q3": q3, "mode": "quantile"}

    def calculate_stats(self, values):
        if not values:
            return {"count": 0, "mean": 0, "min": 0, "max": 0, "median": 0, "std": 0}

        return {
            "count": len(values),
            "mean": statistics.mean(values),
            "min": min(values),
            "max": max(values),
            "median": statistics.median(values),
            "std": statistics.stdev(values) if len(values) > 1 else 0
        }

    def generate_difficulty_statistics(self, summary_data):
        difficulty_groups = {lvl: [] for lvl in self.config.levels}

        for item in summary_data:
            level = item["level"]
            if level in difficulty_groups:
                difficulty_groups[level].append(item)

        stats_report = {}
        for level, items in difficulty_groups.items():
            if not items:
                stats_report[level] = {"sample size": 0}
                continue

            metric_extractors = {
                "loc_values": lambda x: x["features"]["L_LOC"],
                "module_values": lambda x: x["features"]["T_modules"],
                "depth_values": lambda x: x["features"]["T_depth"],
                "always_values": lambda x: x["features"]["S_always_ff"],
                "fsm_values": lambda x: x["features"]["C_fsms"],
                "port_values": lambda x: x["features"]["I_ports"],
                "bitsum_values": lambda x: x["features"]["I_bitsum"],
                "instance_values": lambda x: x["features"]["T_instances"],
                "reg_values": lambda x: x["features"]["R_reg_count"],
                "expr_values": lambda x: x["features"]["D_total_expr"],
                "score_values": lambda x: x["score"]
            }

            metrics = {}
            for key, extractor in metric_extractors.items():
                try:
                    metrics[key] = [extractor(item) for item in items]
                except KeyError as e:
                    logger.warning(f"Lack of feature {e} at difficulty level {level}")
                    metrics[key] = [0] * len(items)

            stats_report[level] = {
                "sample size": len(items),
                "Score statistics": self.calculate_stats(metrics["score_values"]),
                "lines of code": self.calculate_stats(metrics["loc_values"]),
                "number of modules": self.calculate_stats(metrics["module_values"]),
                "Hierarchical depth": self.calculate_stats(metrics["depth_values"]),
                "Always block count": self.calculate_stats(metrics["always_values"]),
                "FSM quantity": self.calculate_stats(metrics["fsm_values"]),
                "Port": self.calculate_stats(metrics["port_values"]),
                "Overall width": self.calculate_stats(metrics["bitsum_values"]),
                "Number of instances": self.calculate_stats(metrics["instance_values"]),
                "Number of registers": self.calculate_stats(metrics["reg_values"]),
                "Number of expressions": self.calculate_stats(metrics["expr_values"])
            }
        return stats_report

    def print_difficulty_report(self, stats_report):
        print("\n" + "=" * 80)
        print(" VERILOG Complexity Analysis and Statistics Report")
        print("=" * 80)


        total_samples = sum(stats["sample size"] for stats in stats_report.values())
        print(f"\n Overall statistics:")
        print(f"Total sample size: {total_samples}")

        for level in self.config.levels:
            if level in stats_report and stats_report[level]["sample size"] > 0:
                count = stats_report[level]["sample size"]
                percentage = (count / total_samples * 100) if total_samples > 0 else 0
                print(f"{level}: {count} samples ({percentage:.1f}%)")

        for level in self.config.levels:
            if level not in stats_report or stats_report[level]["sample size"] == 0:
                continue

            stats = stats_report[level]
            print(f"\n {level} Difficulty Level Statistics:")
            print("-" * 50)

            print(f"sample size: {stats['sample size']}")
            score_stats = stats["Score statistics"]
            print(f"Rating range: {score_stats['min']:.1f} - {score_stats['max']:.1f}")
            print(f"Average rating: {score_stats['mean']:.1f} ± {score_stats['std']:.1f}")
            print(f"Median rating: {score_stats['median']:.1f}")

            key_metrics = [
                ("lines of code", "LOC"),
                ("number of modules", "modules"),
                ("Hierarchical depth", "depth"),
                ("Always block count", "always blocks"),
                ("FSM quantity", "FSMs"),
                ("Port", "ports"),
                ("Overall width", "total bits"),
                ("Number of instances", "instances"),
                ("Number of registers", "registers"),
                ("Number of expressions", "expressions")
            ]

            print("\nkey metrics:")
            for metric_name, _ in key_metrics:
                if metric_name in stats:
                    metric_stats = stats[metric_name]
                    if metric_stats["count"] > 0:
                        print(f"  {metric_name}: {metric_stats['mean']:.1f} ± {metric_stats['std']:.1f} "
                              f"[{metric_stats['min']} - {metric_stats['max']}], "
                              f"median: {metric_stats['median']:.1f}")

    def save_detailed_statistics(self, stats_report, output_dir):
        output_path = Path(output_dir) / "difficulty_statistics.json"

        serializable_stats = {}
        for level, stats in stats_report.items():
            serializable_stats[level] = {}
            for key, value in stats.items():
                if isinstance(value, dict):
                    serializable_stats[level][key] = {k: float(v) if isinstance(v, (int, float)) else v
                                                      for k, v in value.items()}
                else:
                    serializable_stats[level][key] = int(value) if isinstance(value, int) else value

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(serializable_stats, f, ensure_ascii=False, indent=2)

        print(f"\n📁 Detailed statistics have been saved to: {output_path}")

    def generate_summary_table(self, stats_report):
        print(f"\n📋 Summary Table of Complexity Levels:")
        print("-" * 120)

        headers = ["difficulty level", "sample size", "Rating range", "Average LOC", "LOC scope", "Average module", "Module Scope", "Average level",
                   "Hierarchy area "]
        print(" | ".join(f"{h:>10}" for h in headers))
        print("-" * 120)

        for level in self.config.levels:
            if level not in stats_report or stats_report[level]["sample size"] == 0:
                continue

            stats = stats_report[level]
            row = [
                level,
                str(stats['sample size']),
                f"{stats['Score statistics']['min']:.0f}-{stats['Score statistics']['max']:.0f}",
                f"{stats['lines of code']['mean']:.0f}",
                f"{stats['lines of code']['min']}-{stats['lines of code']['max']}",
                f"{stats['number of modules']['mean']:.1f}",
                f"{stats['number of modules']['min']}-{stats['number of modules']['max']}",
                f"{stats['Hierarchical depth']['mean']:.1f}",
                f"{stats['Hierarchical depth']['min']}-{stats['Hierarchical depth']['max']}"
            ]
            print(" | ".join(f"{cell:>10}" for cell in row))

    def safe_filename(self, path: Path, max_name_len=None):
        if max_name_len is None:
            max_name_len = self.config.max_filename_length

        name = path.name
        if len(name) > max_name_len:
            stem = path.stem[:max(20, max_name_len - 40)]
            suffix = path.suffix
            hash_part = hashlib.md5(str(path).encode("utf-8", "ignore")).hexdigest()[:10]
            name = f"{stem}_{hash_part}{suffix}"
        return name

    def organize_files_by_difficulty(self, paths, levels, output_dir):

        level_dirs = {}
        for level in self.config.levels:
            level_dir = Path(output_dir) / level
            level_dir.mkdir(parents=True, exist_ok=True)
            level_dirs[level] = level_dir

        copy_counts = {level: 0 for level in self.config.levels}

        for i, path in enumerate(paths):
            level = levels[i]
            if level not in level_dirs:
                continue

            target_dir = level_dirs[level]
            target_file = target_dir / self.safe_filename(path)

            try:
                shutil.copy2(path, target_file)
                copy_counts[level] += 1
            except Exception as e:
                logger.warning(f"Unable to copy file {path}: {e}")

                try:
                    shorter = target_dir / (hashlib.md5(str(path).encode("utf-8", "ignore")).hexdigest() + path.suffix)
                    shutil.copy2(path, shorter)
                    copy_counts[level] += 1
                except Exception as e2:
                    logger.error(f"The backup replication plan also failed {path}: {e2}")

        print(f"\n📁 The files have been classified by difficulty level:")
        for level in self.config.levels:
            print(f"  {level}: {copy_counts[level]} file -> {level_dirs[level]}")

    def run_analysis(self, in_dir, out_dir, pattern="*_ast.json",
                     use_loc_in_score=False, organize_files=True):

        inp = Path(in_dir)
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)


        log_file = out / f"analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        logger.info(f"Start analysis: Enter directory={in_dir}, Output Directory={out_dir}, pattern={pattern}")


        name_pat = os.path.basename(pattern)
        files = sorted(
            p for p in inp.iterdir()
            if p.is_file() and fnmatch.fnmatch(p.name, name_pat)
        )

        print(f"🔍 Scan the file (first layer only) and find {len (files)} matching files...")

        feats, extras, paths = [], [], []

        print(f"🔍 Scan the file and find {len (files)} matching files...")

        for i, p in enumerate(files):
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as f:
                    data = json.load(f)

                f, e = self.collect_features_from_ast(data, p)
                feats.append(f)
                extras.append(e)
                paths.append(p)

                if (i + 1) % self.config.batch_size == 0:
                    print(f"[INFO] Scanned {i+1} files...", flush=True)

            except Exception as ex:
                logger.warning(f"skip file {p}: {ex}")

        if not feats:
            logger.error("No available JSON file was found. Please check the input directory and mode parameters.")
            return None

        logger.info(f"Successfully processed {len (paths)} files")
        print(f"[INFO] Total number of files: {len(paths)}", flush=True)


        print("🔄 In feature normalization...")
        normed, bounds = self.normalize_features(feats, include_loc_in_score=use_loc_in_score)

        print("📊 Calculation complexity rating...")
        weights = dict(self.config.weights)
        if use_loc_in_score:
            weights["L"] = self.config.loc_weight

        groups_list = ["T", "S", "C", "D", "P", "I", "R", "M"] + (["L"] if use_loc_in_score else [])
        scores01, scores100, group_scores = [], [], []

        for z in normed:
            g = {pref: self.group_avg_from_normed(z, pref) for pref in groups_list}
            score01 = sum(weights.get(k, 0.0) * g.get(k, 0.0) for k in g)


            score01_boosted = math.sqrt(max(0.0, score01)) * 0.7 + score01 * 0.3
            score100 = 100.0 * score01_boosted

            scores01.append(score01)
            scores100.append(score100)
            group_scores.append(g)


        print("🎯 Complexity grading...")
        levels, thresholds = self.assign_levels_quantile(scores100)


        level_counts = Counter(levels)
        logger.info(f"Classification statistics: {dict(level_counts)}")
        print(f"[INFO] Classification statistics: "
              f"Simple={level_counts.get('Simple', 0)}, "
              f"Intermediate={level_counts.get('Intermediate', 0)}, "
              f"Advanced={level_counts.get('Advanced', 0)}")
        print(f"[INFO] quantile: Q1={thresholds.get('q1'):.2f}, Q3={thresholds.get('q3'):.2f}")
        print(f"[INFO] score range: {min(scores100):.2f} - {max(scores100):.2f}")

        summary = []
        for i, p in enumerate(paths):
            summary.append({
                "path": str(p),
                "score01": float(scores01[i]),
                "score": float(scores100[i]),
                "level": levels[i],
                "features": feats[i],
                "groups": group_scores[i],
                "protocols": extras[i]["protocols"],
                "port_names": extras[i]["port_names"],
                "verilog_path": extras[i]["verilog_path"]
            })

        print("📈 Generate statistical reports...")
        stats_report = self.generate_difficulty_statistics(summary)
        self.print_difficulty_report(stats_report)
        self.generate_summary_table(stats_report)

        print("💾 Save analysis results...")

        (out / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        keys_feat = sorted(set().union(*[f.keys() for f in feats]))
        with open(out / "summary.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["path", "score01", "score", "level"] + keys_feat)
            for i, p in enumerate(paths):
                row = [
                          str(p),
                          f"{scores01[i]:.6f}",
                          f"{scores100[i]:.2f}",
                          levels[i]
                      ] + [feats[i].get(k, 0) for k in keys_feat]
                w.writerow(row)

        (out / "thresholds.json").write_text(
            json.dumps(thresholds, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        (out / "weights.json").write_text(
            json.dumps(weights, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        feature_stats = {}
        for key in keys_feat:
            vals = [f.get(key, 0) for f in feats]
            if vals:
                feature_stats[key] = {
                    "min": min(vals),
                    "max": max(vals),
                    "avg": sum(vals) / len(vals),
                    "median": sorted(vals)[len(vals) // 2],
                    "non_zero": sum(1 for v in vals if v > 0),
                    "std": statistics.stdev(vals) if len(vals) > 1 else 0
                }

        (out / "feature_stats.json").write_text(
            json.dumps(feature_stats, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        self.save_detailed_statistics(stats_report, out)

        if organize_files:
            print("📁 Classify files by difficulty level...")
            self.organize_files_by_difficulty(paths, levels, out)

        logger.info(f"Analysis completed, results saved to {out}")
        print(f"\n✅ [Completed] Processed {len (paths)} files. Save results to {out}", flush=True)

        return stats_report


def main():
    parser = argparse.ArgumentParser(description="Verilog complexity analysis tool")
    parser.add_argument("--input", "-i", required=True, help="Enter directory path")
    parser.add_argument("--output", "-o", required=True, help="Output directory path")
    parser.add_argument("--pattern", "-p", default="*_ast.json", help="File matching mode")
    parser.add_argument("--use-loc", action="store_true", help="Include lines of code in the rating")
    parser.add_argument("--no-organize", action="store_true", help="Files not classified by difficulty level")
    parser.add_argument("--config", "-c", help="Configuration file path (JSON format)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    args = parser.parse_args()


    logging.getLogger().setLevel(getattr(logging, args.log_level))


    config = AnalysisConfig()
    if args.config and os.path.exists(args.config):
        try:
            with open(args.config, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

                for key, value in config_data.items():
                    if hasattr(config, key):
                        setattr(config, key, value)
            logger.info(f"load profile: {args.config}")
        except Exception as e:
            logger.warning(f"Unable to load configuration file {args.config}: {e}")


    analyzer = VerilogComplexityAnalyzer(config)

    try:
        stats_report = analyzer.run_analysis(
            in_dir=args.input,
            out_dir=args.output,
            pattern=args.pattern,
            use_loc_in_score=args.use_loc,
            organize_files=not args.no_organize
        )

        if stats_report:
            print("\n🎉 Analysis completed successfully!")
            return 0
        else:
            print("\n❌ Analysis failed!")
            return 1

    except KeyboardInterrupt:
        print("\n⚠️ User Interruption Analysis")
        return 130
    except Exception as e:
        logger.error(f"An error occurred during the analysis process: {e}")
        print(f"\n❌ analysis failed: {e}")
        return 1


if __name__ == "__main__":
    if len(sys.argv) > 1:
        exit_code = main()
        sys.exit(exit_code)
    else:
        config = AnalysisConfig()
        analyzer = VerilogComplexityAnalyzer(config)

        stats = analyzer.run_analysis(
            in_dir=r"......",
            out_dir=r"......",
            pattern="*.json",
            use_loc_in_score=True
        )