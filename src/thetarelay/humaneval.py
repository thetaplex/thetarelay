"""Standard HumanEval / MBPP coding benchmark runner with sandboxed pass@1 execution.

Measures functional accuracy (`pass@1`), wall-clock speedup, and token efficiency across:
1. Local Draft Model alone (`local_pass@1`)
2. Target / Frontier Model Baseline alone (`baseline_pass@1`)
3. ThetaRelay Speculative Drafter + Target Verifier (`speculative_pass@1`)

Also includes a Verifier Guardrail Stress Test where subtly buggy drafts (off-by-one errors,
inverted conditions) are injected to verify that the target model rejects faulty drafts
and recovers `pass@1`!
"""

from __future__ import annotations

import ast
import copy
import gzip
import json
import math
import multiprocessing
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import SpeculativeConfig
from .engine import (
    DraftResult,
    convert_to_openai_draft_payload,
    inject_draft_into_body,
    response_accepted_draft,
)
from .evals import _extract_python_code_blocks, _warmup_openai_model

_HUMANEVAL_SYS_PROMPT = "You are an expert Python programmer. Complete the requested function."


@dataclass
class HumanEvalProblem:
    """A single canonical HumanEval / MBPP problem with executable unit tests."""

    task_id: str
    entry_point: str
    prompt: str
    test_code: str
    canonical_solution: str = ""
    buggy_draft: str = ""


def get_builtin_humaneval_problems() -> list[HumanEvalProblem]:
    """Return 10 canonical HumanEval problems covering algorithms, strings, math, and parsing."""
    return [
        HumanEvalProblem(
            task_id="HumanEval/0",
            entry_point="has_close_elements",
            prompt=(
                "from typing import List\n\n\n"
                "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
                '    """Check if in given list of numbers, are any two numbers closer to each\n'
                "    other than given threshold.\n"
                "    >>> has_close_elements([1.0, 2.0, 3.0], 0.5)\n"
                "    False\n"
                "    >>> has_close_elements([1.0, 2.8, 3.0, 4.0, 5.0, 2.0], 0.3)\n"
                "    True\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.3) == True\n"
                "    assert candidate([1.0, 2.0, 3.9, 4.0, 5.0, 2.2], 0.05) == False\n"
                "    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.95) == True\n"
                "    assert candidate([1.0, 2.0, 5.9, 4.0, 5.0], 0.8) == False\n"
                "    assert candidate([1.0, 2.0, 3.0, 4.0, 5.0, 2.0], 0.1) == True\n"
                "    assert candidate([1.1, 2.2, 3.1, 4.1, 5.1], 1.0) == True\n"
                "    assert candidate([1.1, 2.2, 3.1, 4.1, 5.1], 0.5) == False\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
                "    for idx, elem in enumerate(numbers):\n"
                "        for idx2, elem2 in enumerate(numbers):\n"
                "            if idx != idx2 and abs(elem - elem2) < threshold:\n"
                "                return True\n"
                "    return False\n"
            ),
            buggy_draft=(
                "from typing import List\n\n\n"
                "def has_close_elements(numbers: List[float], threshold: float) -> bool:\n"
                "    # BUG: only checks adjacent elements without sorting!\n"
                "    for i in range(len(numbers) - 1):\n"
                "        if abs(numbers[i] - numbers[i + 1]) < threshold:\n"
                "            return True\n"
                "    return False\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/2",
            entry_point="truncate_number",
            prompt=(
                "def truncate_number(number: float) -> float:\n"
                '    """Given a positive floating point number, it can be decomposed into\n'
                "    an integer part (largest integer smaller than given number) and decimals\n"
                "    (always smaller than 1). Return the decimal part of the number.\n"
                "    >>> truncate_number(3.5)\n"
                "    0.5\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate(3.5) == 0.5\n"
                "    assert abs(candidate(1.33) - 0.33) < 1e-6\n"
                "    assert abs(candidate(123.456) - 0.456) < 1e-6\n"
            ),
            canonical_solution=(
                "def truncate_number(number: float) -> float:\n    return number % 1.0\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/3",
            entry_point="below_zero",
            prompt=(
                "from typing import List\n\n\n"
                "def below_zero(operations: List[int]) -> bool:\n"
                '    """You are given a list of deposit and withdrawal operations on a bank\n'
                "    account that starts with zero balance. Detect if at any point the balance\n"
                "    falls below zero, and return True. Otherwise return False.\n"
                "    >>> below_zero([1, 2, 3])\n"
                "    False\n"
                "    >>> below_zero([1, 2, -4, 5])\n"
                "    True\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([]) == False\n"
                "    assert candidate([1, 2, -3, 1, 2, -3]) == False\n"
                "    assert candidate([1, 2, -4, 5, 6]) == True\n"
                "    assert candidate([1, -1, 2, -2, 5, -5, 4, -4]) == False\n"
                "    assert candidate([1, -1, 2, -2, 5, -5, 4, -5]) == True\n"
                "    assert candidate([1, -2, 2, -2, 5, -5, 4, -4]) == True\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def below_zero(operations: List[int]) -> bool:\n"
                "    balance = 0\n"
                "    for op in operations:\n"
                "        balance += op\n"
                "        if balance < 0:\n"
                "            return True\n"
                "    return False\n"
            ),
            buggy_draft=(
                "from typing import List\n\n\n"
                "def below_zero(operations: List[int]) -> bool:\n"
                "    # BUG: only checks final sum instead of running balance!\n"
                "    return sum(operations) < 0\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/4",
            entry_point="mean_absolute_deviation",
            prompt=(
                "from typing import List\n\n\n"
                "def mean_absolute_deviation(numbers: List[float]) -> float:\n"
                '    """For a given list of input numbers, calculate Mean Absolute Deviation\n'
                "    around the mean of this dataset.\n"
                "    MAD = average | x - x_mean |\n"
                "    >>> mean_absolute_deviation([1.0, 2.0, 3.0, 4.0])\n"
                "    1.0\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert abs(candidate([1.0, 2.0, 3.0]) - 2.0/3.0) < 1e-6\n"
                "    assert abs(candidate([1.0, 2.0, 3.0, 4.0]) - 1.0) < 1e-6\n"
                "    assert abs(candidate([1.0, 2.0, 3.0, 4.0, 5.0]) - 6.0/5.0) < 1e-6\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def mean_absolute_deviation(numbers: List[float]) -> float:\n"
                "    mean = sum(numbers) / len(numbers)\n"
                "    return sum(abs(x - mean) for x in numbers) / len(numbers)\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/5",
            entry_point="intersperse",
            prompt=(
                "from typing import List\n\n\n"
                "def intersperse(numbers: List[int], delimeter: int) -> List[int]:\n"
                '    """Insert a number delimeter between every two consecutive elements\n'
                "    of input list numbers.\n"
                "    >>> intersperse([], 4)\n"
                "    []\n"
                "    >>> intersperse([1, 2, 3], 4)\n"
                "    [1, 4, 2, 4, 3]\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([], 7) == []\n"
                "    assert candidate([5, 6, 3, 2], 8) == [5, 8, 6, 8, 3, 8, 2]\n"
                "    assert candidate([2, 2, 2], 2) == [2, 2, 2, 2, 2]\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def intersperse(numbers: List[int], delimeter: int) -> List[int]:\n"
                "    if not numbers:\n"
                "        return []\n"
                "    res = []\n"
                "    for n in numbers[:-1]:\n"
                "        res.extend([n, delimeter])\n"
                "    res.append(numbers[-1])\n"
                "    return res\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/6",
            entry_point="parse_nested_parens",
            prompt=(
                "from typing import List\n\n\n"
                "def parse_nested_parens(paren_string: str) -> List[int]:\n"
                '    """Input is a string of multiple groups of nested parentheses separated\n'
                "    by spaces. For each group, output the deepest level of nesting.\n"
                "    >>> parse_nested_parens('(()()) ((())) () ((())()())')\n"
                "    [2, 3, 1, 3]\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate('(()()) ((())) () ((())()())') == [2, 3, 1, 3]\n"
                "    assert candidate('() (()) ((())) (((())))') == [1, 2, 3, 4]\n"
                "    assert candidate('(()(())((())))') == [4]\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def parse_nested_parens(paren_string: str) -> List[int]:\n"
                "    res = []\n"
                "    for group in paren_string.split():\n"
                "        depth = max_d = 0\n"
                "        for c in group:\n"
                "            depth += 1 if c == '(' else -1\n"
                "            max_d = max(max_d, depth)\n"
                "        res.append(max_d)\n"
                "    return res\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/7",
            entry_point="filter_by_substring",
            prompt=(
                "from typing import List\n\n\n"
                "def filter_by_substring(strings: List[str], substring: str) -> List[str]:\n"
                '    """Filter an input list of strings only for ones containing substring.\n'
                "    >>> filter_by_substring([], 'a')\n"
                "    []\n"
                "    >>> filter_by_substring(['abc', 'bacd', 'cde', 'array'], 'a')\n"
                "    ['abc', 'bacd', 'array']\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([], 'john') == []\n"
                "    inp1 = ['xxx', 'asd', 'xxy', 'john doe', 'xxxAAA', 'xxx']\n"
                "    assert candidate(inp1, 'xxx') == ['xxx', 'xxxAAA', 'xxx']\n"
                "    inp2 = ['xxx', 'asd', 'aaaxxy', 'john doe', 'xxxAAA', 'xxx']\n"
                "    assert candidate(inp2, 'xx') == ['xxx', 'aaaxxy', 'xxxAAA', 'xxx']\n"
                "    inp3 = ['grunt', 'trumpet', 'prune', 'gruesome']\n"
                "    assert candidate(inp3, 'run') == ['grunt', 'prune']\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def filter_by_substring(strings: List[str], substring: str) -> List[str]:\n"
                "    return [s for s in strings if substring in s]\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/8",
            entry_point="sum_product",
            prompt=(
                "from typing import List, Tuple\n\n\n"
                "def sum_product(numbers: List[int]) -> Tuple[int, int]:\n"
                '    """For a given list of integers, return a tuple of (sum, product).\n'
                "    Empty sum should be 0 and empty product should be 1.\n"
                "    >>> sum_product([])\n"
                "    (0, 1)\n"
                "    >>> sum_product([1, 2, 3, 4])\n"
                "    (10, 24)\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([]) == (0, 1)\n"
                "    assert candidate([1, 1, 1]) == (3, 1)\n"
                "    assert candidate([100, 0]) == (100, 0)\n"
                "    assert candidate([3, 5, 7]) == (3 + 5 + 7, 3 * 5 * 7)\n"
                "    assert candidate([10]) == (10, 10)\n"
            ),
            canonical_solution=(
                "from typing import List, Tuple\n\n\n"
                "def sum_product(numbers: List[int]) -> Tuple[int, int]:\n"
                "    s, p = 0, 1\n"
                "    for n in numbers:\n"
                "        s += n\n"
                "        p *= n\n"
                "    return s, p\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/9",
            entry_point="rolling_max",
            prompt=(
                "from typing import List\n\n\n"
                "def rolling_max(numbers: List[int]) -> List[int]:\n"
                '    """From a given list of integers, generate a list of rolling maximum\n'
                "    element found until given moment in the sequence.\n"
                "    >>> rolling_max([1, 2, 3, 2, 3, 4, 2])\n"
                "    [1, 2, 3, 3, 3, 4, 4]\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate([]) == []\n"
                "    assert candidate([1, 2, 3, 4]) == [1, 2, 3, 4]\n"
                "    assert candidate([4, 3, 2, 1]) == [4, 4, 4, 4]\n"
                "    assert candidate([3, 2, 3, 100, 3]) == [3, 3, 3, 100, 100]\n"
            ),
            canonical_solution=(
                "from typing import List\n\n\n"
                "def rolling_max(numbers: List[int]) -> List[int]:\n"
                "    res = []\n"
                "    cur = None\n"
                "    for n in numbers:\n"
                "        cur = n if cur is None else max(cur, n)\n"
                "        res.append(cur)\n"
                "    return res\n"
            ),
        ),
        HumanEvalProblem(
            task_id="HumanEval/10",
            entry_point="make_palindrome",
            prompt=(
                "def make_palindrome(string: str) -> str:\n"
                '    """Find the shortest palindrome that begins with a supplied string.\n'
                "    >>> make_palindrome('')\n"
                "    ''\n"
                "    >>> make_palindrome('cat')\n"
                "    'catac'\n"
                "    >>> make_palindrome('cata')\n"
                "    'catac'\n"
                '    """\n'
            ),
            test_code=(
                "def check(candidate):\n"
                "    assert candidate('') == ''\n"
                "    assert candidate('x') == 'x'\n"
                "    assert candidate('xyz') == 'xyzyx'\n"
                "    assert candidate('xyx') == 'xyx'\n"
                "    assert candidate('jerry') == 'jerryrrej'\n"
            ),
            canonical_solution=(
                "def make_palindrome(string: str) -> str:\n"
                "    for i in range(len(string)):\n"
                "        sub = string[i:]\n"
                "        if sub == sub[::-1]:\n"
                "            return string + string[:i][::-1]\n"
                "    return ''\n"
            ),
        ),
    ]


def load_humaneval_from_file(path: Path) -> list[HumanEvalProblem]:
    """Load official OpenAI `HumanEval.jsonl` or `HumanEval.jsonl.gz` problems."""
    if path.name.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            raw_text = fh.read()
    else:
        raw_text = path.read_text(encoding="utf-8")

    problems: list[HumanEvalProblem] = []
    for line in raw_text.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        problems.append(
            HumanEvalProblem(
                task_id=str(item["task_id"]),
                entry_point=str(item["entry_point"]),
                prompt=str(item["prompt"]),
                test_code=str(item["test"]),
                canonical_solution=str(item.get("canonical_solution", "")),
            )
        )
    return problems


def _exec_candidate_worker(
    code_str: str,
    prompt_str: str,
    entry_point: str,
    test_code: str,
    result_queue: Any,
) -> None:
    """Child process worker that executes candidate code against canonical HumanEval assertions."""
    try:
        extracted = _extract_python_code_blocks(code_str)
        # Prepend prompt header if the model output only the body or omitted typing imports
        if f"def {entry_point}" not in extracted:
            full_source = prompt_str + "\n" + extracted
        else:
            import_lines = [
                line
                for line in prompt_str.splitlines()
                if line.strip().startswith(("import ", "from "))
            ]
            full_source = "\n".join(import_lines) + "\n\n" + extracted

        namespace: dict[str, Any] = {}
        exec(full_source, namespace)  # noqa: S102
        if entry_point not in namespace:
            result_queue.put((False, f"missing_entry_point:{entry_point}"))
            return
        exec(test_code, namespace)  # noqa: S102
        check_fn = namespace.get("check")
        if not callable(check_fn):
            result_queue.put((False, "missing_check_fn"))
            return
        check_fn(namespace[entry_point])
        result_queue.put((True, "passed"))
    except Exception as exc:
        result_queue.put((False, f"{type(exc).__name__}: {exc}"))


def evaluate_humaneval_functional_pass(
    *,
    code_str: str,
    problem: HumanEvalProblem,
    timeout_seconds: float = 3.0,
) -> tuple[bool, str]:
    """Execute generated Python code against canonical HumanEval `check(candidate)` with timeout."""
    ctx = multiprocessing.get_context("spawn")
    q: Any = ctx.Queue()
    proc = ctx.Process(
        target=_exec_candidate_worker,
        args=(code_str, problem.prompt, problem.entry_point, problem.test_code, q),
    )
    proc.start()
    proc.join(timeout=timeout_seconds)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=1.0)
        return False, "TimeoutError: execution exceeded timeout"
    if not q.empty():
        res: tuple[bool, str] = q.get_nowait()
        return res
    return False, f"ProcessExitedWithCode:{proc.exitcode}"


def mutate_python_code_ast(code_str: str) -> list[tuple[str, str]]:
    """Generate single-point syntactic/semantic AST mutants of valid Python code.

    Used for Verifier Honesty (RQ2) experiments: produces plausible-looking drafts
    that parse cleanly (`ast.parse` succeeds) with a single flipped comparison,
    arithmetic operator, boolean operator, or boundary constant.
    """
    extracted = _extract_python_code_blocks(code_str)
    try:
        base_tree = ast.parse(extracted)
    except SyntaxError:
        return []

    cmp_flips: dict[type[ast.cmpop], tuple[str, type[ast.cmpop]]] = {
        ast.Lt: ("lt_to_lte", ast.LtE),
        ast.LtE: ("lte_to_lt", ast.Lt),
        ast.Gt: ("gt_to_gte", ast.GtE),
        ast.GtE: ("gte_to_gt", ast.Gt),
        ast.Eq: ("eq_to_neq", ast.NotEq),
        ast.NotEq: ("neq_to_eq", ast.Eq),
        ast.In: ("in_to_notin", ast.NotIn),
        ast.NotIn: ("notin_to_in", ast.In),
        ast.Is: ("is_to_isnot", ast.IsNot),
        ast.IsNot: ("isnot_to_is", ast.Is),
    }
    bin_flips: dict[type[ast.operator], tuple[str, type[ast.operator]]] = {
        ast.Add: ("add_to_sub", ast.Sub),
        ast.Sub: ("sub_to_add", ast.Add),
        ast.Mult: ("mult_to_add", ast.Add),
        ast.Mod: ("mod_to_floordiv", ast.FloorDiv),
    }
    bool_flips: dict[type[ast.boolop], tuple[str, type[ast.boolop]]] = {
        ast.And: ("and_to_or", ast.Or),
        ast.Or: ("or_to_and", ast.And),
    }

    mutants: list[tuple[str, str]] = []
    seen_sources: set[str] = {ast.unparse(base_tree)}
    nodes = list(ast.walk(base_tree))

    for idx, node in enumerate(nodes):
        if isinstance(node, ast.Compare) and node.ops:
            op_type = type(node.ops[0])
            if op_type in cmp_flips:
                label, new_op_cls = cmp_flips[op_type]
                tree_copy = copy.deepcopy(base_tree)
                target_node = list(ast.walk(tree_copy))[idx]
                if isinstance(target_node, ast.Compare):
                    target_node.ops[0] = new_op_cls()
                    src = ast.unparse(tree_copy)
                    if src not in seen_sources:
                        seen_sources.add(src)
                        mutants.append((label, src))
        elif isinstance(node, ast.BinOp):
            bin_op_type = type(node.op)
            if bin_op_type in bin_flips:
                label, new_bin_cls = bin_flips[bin_op_type]
                tree_copy = copy.deepcopy(base_tree)
                target_node = list(ast.walk(tree_copy))[idx]
                if isinstance(target_node, ast.BinOp):
                    target_node.op = new_bin_cls()
                    src = ast.unparse(tree_copy)
                    if src not in seen_sources:
                        seen_sources.add(src)
                        mutants.append((label, src))
        elif isinstance(node, ast.AugAssign):
            aug_op_type = type(node.op)
            if aug_op_type in bin_flips:
                label, new_aug_cls = bin_flips[aug_op_type]
                tree_copy = copy.deepcopy(base_tree)
                target_node = list(ast.walk(tree_copy))[idx]
                if isinstance(target_node, ast.AugAssign):
                    target_node.op = new_aug_cls()
                    src = ast.unparse(tree_copy)
                    if src not in seen_sources:
                        seen_sources.add(src)
                        mutants.append((f"aug_{label}", src))
        elif isinstance(node, ast.BoolOp):
            bool_op_type = type(node.op)
            if bool_op_type in bool_flips:
                label, new_bool_cls = bool_flips[bool_op_type]
                tree_copy = copy.deepcopy(base_tree)
                target_node = list(ast.walk(tree_copy))[idx]
                if isinstance(target_node, ast.BoolOp):
                    target_node.op = new_bool_cls()
                    src = ast.unparse(tree_copy)
                    if src not in seen_sources:
                        seen_sources.add(src)
                        mutants.append((label, src))

    return mutants


def compute_paired_mcnemar_and_confusion(task_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute paired McNemar non-inferiority stats and the 2x2 Verifier Confusion Matrix.

    - McNemar 2x2 table: `both_pass` (a), `baseline_only_pass` (b), `speculative_only_pass` (c),
      `both_fail` (d), exact two-sided binomial p-value, and 95% confidence interval for (c - b)/N.
    - Verifier 2x2 confusion matrix:
      - `true_accepts` (draft passes & verifier accepts) -> True Accept Rate (TAR)
      - `false_accepts` (draft fails & verifier accepts) -> False Accept Rate (FAR / Sycophancy)
      - `true_rejects` (draft fails & verifier rejects) -> True Reject Rate (TRR)
      - `false_rejects` (draft passes & verifier rejects) -> False Reject Rate (FRR / Over-strict)
    """
    paired = [r for r in task_rows if "baseline_pass_at_1" in r and "speculative_pass_at_1" in r]
    n = len(paired)
    if n == 0:
        return {}

    a = sum(1 for r in paired if r["baseline_pass_at_1"] and r["speculative_pass_at_1"])
    b = sum(1 for r in paired if r["baseline_pass_at_1"] and not r["speculative_pass_at_1"])
    c = sum(1 for r in paired if not r["baseline_pass_at_1"] and r["speculative_pass_at_1"])
    d = sum(1 for r in paired if not r["baseline_pass_at_1"] and not r["speculative_pass_at_1"])

    # Exact two-sided binomial McNemar test on discordant pairs (b, c)
    discordant = b + c
    if discordant == 0:
        p_val = 1.0
    else:
        k = min(b, c)
        tail = sum(math.comb(discordant, i) for i in range(k + 1)) / (2**discordant)
        p_val = min(1.0, 2.0 * tail)

    # Paired difference (spec - base) and Wald/Agresti-Min 95% CI
    diff = (c - b) / n
    se = math.sqrt(max(0.0, (b + c) - ((c - b) ** 2) / n)) / n
    ci_low = max(-1.0, diff - 1.96 * se)
    ci_high = min(1.0, diff + 1.96 * se)

    # Verifier 2x2 Confusion Matrix (Draft Functional Pass vs Verifier Accepted)
    tp_accept = sum(1 for r in paired if r.get("local_pass_at_1") and r.get("target_accepted"))
    fp_accept = sum(1 for r in paired if not r.get("local_pass_at_1") and r.get("target_accepted"))
    tn_reject = sum(
        1 for r in paired if not r.get("local_pass_at_1") and not r.get("target_accepted")
    )
    fn_reject = sum(1 for r in paired if r.get("local_pass_at_1") and not r.get("target_accepted"))
    recovered_after_reject = sum(
        1
        for r in paired
        if not r.get("local_pass_at_1")
        and not r.get("target_accepted")
        and r.get("speculative_pass_at_1")
    )

    good_drafts = tp_accept + fn_reject
    bad_drafts = fp_accept + tn_reject

    return {
        "mcnemar_paired_test": {
            "n_pairs": n,
            "both_pass_a": a,
            "baseline_only_pass_b": b,
            "speculative_only_pass_c": c,
            "both_fail_d": d,
            "delta_accuracy_pct": round(diff * 100.0, 2),
            "ci_95_low_pct": round(ci_low * 100.0, 2),
            "ci_95_high_pct": round(ci_high * 100.0, 2),
            "exact_p_value": round(p_val, 4),
        },
        "verifier_confusion_matrix": {
            "true_accepts": tp_accept,
            "false_accepts_sycophancy": fp_accept,
            "true_rejects": tn_reject,
            "false_rejects_overstrict": fn_reject,
            "true_accept_rate_pct": (
                round((tp_accept / good_drafts) * 100.0, 1) if good_drafts > 0 else None
            ),
            "false_reject_rate_pct": (
                round((fn_reject / good_drafts) * 100.0, 1) if good_drafts > 0 else None
            ),
            "false_accept_rate_pct": (
                round((fp_accept / bad_drafts) * 100.0, 1) if bad_drafts > 0 else None
            ),
            "post_rejection_recovery_rate_pct": (
                round((recovered_after_reject / tn_reject) * 100.0, 1) if tn_reject > 0 else None
            ),
        },
    }


async def run_humaneval_benchmark(
    config: SpeculativeConfig | None = None,
    problems: list[HumanEvalProblem] | None = None,
    limit: int | None = None,
    target_model: str | None = None,
    target_base_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Run the standard HumanEval coding benchmark comparing:
    - Local Drafter `pass@1`
    - Target Baseline `pass@1` & latency
    - ThetaRelay Speculative (`<ACCEPT/>` verification + rewrite fallback) `pass@1` & latency
    - Buggy-Draft Guardrail Stress Test (verifier rejects subtly buggy drafts and recovers `pass@1`)
    """
    cfg = config or SpeculativeConfig.from_env()
    suite = list(problems or get_builtin_humaneval_problems())
    if limit is not None and limit > 0:
        suite = suite[:limit]

    # Ensure target_api_key can be passed via env (`OPENROUTER_API_KEY`, `OPENAI_API_KEY`, etc.)
    import os

    target_api_key = (
        os.environ.get("THETARELAY_TARGET_API_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or cfg.local_api_key
    )
    draft_map: dict[str, DraftResult] = {}
    task_rows: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=45.0, transport=transport) as client:
        # Phase 1: Warm up Local Drafter and generate all HumanEval completions
        warmup_local_ms = 0.0
        if transport is None:
            warmup_local_ms = await _warmup_openai_model(
                client,
                cfg.local_base_url,
                cfg.local_model,
                cfg.local_api_key,
                cfg.local_disable_thinking,
            )

        for prob in suite:
            req_body = {
                "model": target_model or "claude-sonnet-4-5",
                "system": _HUMANEVAL_SYS_PROMPT,
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            f"Complete the following Python function `{prob.entry_point}`:\n\n"
                            f"```python\n{prob.prompt}```"
                        ),
                    }
                ],
            }
            payload = convert_to_openai_draft_payload(req_body, "anthropic", cfg)
            t0 = time.perf_counter()
            try:
                resp = await client.post(
                    f"{cfg.local_base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {cfg.local_api_key}"},
                    timeout=cfg.local_timeout_seconds,
                )
            except httpx.TimeoutException:
                # Same as the proxy: a draft that is too slow counts as no draft.
                resp = httpx.Response(504)
            draft_ms = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                data = resp.json()
                draft_text = str(
                    (((data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
                )
                draft_tok = int(
                    (data.get("usage") or {}).get("completion_tokens")
                    or max(1, len(draft_text) // 4)
                )
            else:
                draft_text = ""
                draft_tok = 0

            draft_passed, draft_err = evaluate_humaneval_functional_pass(
                code_str=draft_text, problem=prob
            )
            draft_obj = DraftResult(
                text=draft_text,
                latency_ms=draft_ms,
                draft_tokens_est=draft_tok,
            )
            draft_map[prob.task_id] = draft_obj
            task_rows.append(
                {
                    "task_id": prob.task_id,
                    "entry_point": prob.entry_point,
                    "local_draft_ms": round(draft_ms, 1),
                    "local_draft_tokens": draft_tok,
                    "local_pass_at_1": draft_passed,
                    "local_eval_status": draft_err,
                }
            )

        # Phase 2: If target_model is specified, run Target Baseline + Speculative Verification
        # + Buggy-Draft Guardrail Stress Test in a single warm batch!
        target_summary: dict[str, Any] | None = None
        if target_model:
            t_url_base = (target_base_url or cfg.local_base_url).rstrip("/")
            warmup_target_ms = 0.0
            if transport is None:
                warmup_target_ms = await _warmup_openai_model(
                    client,
                    t_url_base,
                    target_model,
                    target_api_key,
                    disable_thinking=True,
                )

            target_cfg = copy.deepcopy(cfg)
            target_cfg.local_model = target_model
            target_cfg.strategy = "accept_swap"
            # The frontier gets the original request: no local drafting instructions, so the
            # baseline answers as it would without thetarelay.
            target_cfg.local_concise_draft_instructions = False

            base_pass_count = 0
            spec_pass_count = 0
            accepted_count = 0
            total_base_ms = 0.0
            total_spec_ms = 0.0
            total_base_out_tok = 0
            total_spec_out_tok = 0

            for prob, row in zip(suite, task_rows, strict=False):
                req_body = {
                    "model": target_model,
                    "system": _HUMANEVAL_SYS_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Complete the following Python function `{prob.entry_point}`:\n\n"
                                f"```python\n{prob.prompt}```"
                            ),
                        }
                    ],
                }
                # 2a. Target Baseline Direct Run
                base_payload = convert_to_openai_draft_payload(req_body, "anthropic", target_cfg)
                t_b0 = time.perf_counter()
                b_resp = await client.post(
                    f"{t_url_base}/chat/completions",
                    json=base_payload,
                    headers={"Authorization": f"Bearer {target_api_key}"},
                    timeout=60.0,
                )
                base_ms = (time.perf_counter() - t_b0) * 1000.0
                b_data = b_resp.json() if b_resp.status_code == 200 else {}
                base_text = str(
                    (((b_data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
                )
                base_tok = int((b_data.get("usage") or {}).get("completion_tokens", 0))
                base_passed, base_err = evaluate_humaneval_functional_pass(
                    code_str=base_text, problem=prob
                )

                # 2b. Speculative Verification Run
                draft_obj = draft_map[prob.task_id]
                ver_cfg = copy.deepcopy(target_cfg)
                injected_body, _ = inject_draft_into_body(req_body, "anthropic", draft_obj, ver_cfg)
                ver_payload = convert_to_openai_draft_payload(injected_body, "anthropic", ver_cfg)
                t_v0 = time.perf_counter()
                v_resp = await client.post(
                    f"{t_url_base}/chat/completions",
                    json=ver_payload,
                    headers={"Authorization": f"Bearer {target_api_key}"},
                    timeout=60.0,
                )
                verify_ms = (time.perf_counter() - t_v0) * 1000.0
                v_data = v_resp.json() if v_resp.status_code == 200 else {}
                ver_text = str(
                    (((v_data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
                )
                ver_tok = int((v_data.get("usage") or {}).get("completion_tokens", 0))
                accepted = response_accepted_draft(ver_text, ver_cfg)

                # Final speculative output is local draft if `<ACCEPT/>`, else target's rewrite!
                final_spec_code = draft_obj.text if accepted else ver_text
                spec_passed, spec_err = evaluate_humaneval_functional_pass(
                    code_str=final_spec_code, problem=prob
                )

                spec_e2e_ms = float(row["local_draft_ms"]) + verify_ms
                total_base_ms += base_ms
                total_spec_ms += spec_e2e_ms
                total_base_out_tok += base_tok
                total_spec_out_tok += ver_tok
                if base_passed:
                    base_pass_count += 1
                if spec_passed:
                    spec_pass_count += 1
                if accepted:
                    accepted_count += 1

                row.update(
                    {
                        "baseline_ms": round(base_ms, 1),
                        "baseline_output_tokens": base_tok,
                        "baseline_pass_at_1": base_passed,
                        "baseline_eval_status": base_err,
                        "target_verify_ms": round(verify_ms, 1),
                        "target_verify_output_tokens": ver_tok,
                        "target_accepted": accepted,
                        "speculative_e2e_ms": round(spec_e2e_ms, 1),
                        "speedup_factor": (
                            round(base_ms / spec_e2e_ms, 2) if spec_e2e_ms > 0 else 1.0
                        ),
                        "speculative_pass_at_1": spec_passed,
                        "speculative_eval_status": spec_err,
                    }
                )

            # 2c. Buggy-Draft Guardrail Stress Test (on problems with `buggy_draft`)
            buggy_problems = [p for p in suite if p.buggy_draft]
            guard_results: list[dict[str, Any]] = []
            for bprob in buggy_problems:
                # Confirm the buggy draft genuinely fails the canonical unit test suite
                bug_passes, _ = evaluate_humaneval_functional_pass(
                    code_str=bprob.buggy_draft, problem=bprob
                )
                req_body = {
                    "model": target_model,
                    "system": _HUMANEVAL_SYS_PROMPT,
                    "messages": [
                        {
                            "role": "user",
                            "content": (
                                f"Complete the following Python function `{bprob.entry_point}`:\n\n"
                                f"```python\n{bprob.prompt}```"
                            ),
                        }
                    ],
                }
                ver_cfg = copy.deepcopy(target_cfg)
                bug_draft_obj = DraftResult(
                    text=f"```python\n{bprob.buggy_draft}```",
                    latency_ms=200.0,
                    draft_tokens_est=40,
                )
                inj_body, _ = inject_draft_into_body(req_body, "anthropic", bug_draft_obj, ver_cfg)
                v_payload = convert_to_openai_draft_payload(inj_body, "anthropic", ver_cfg)
                v_resp = await client.post(
                    f"{t_url_base}/chat/completions",
                    json=v_payload,
                    headers={"Authorization": f"Bearer {target_api_key}"},
                    timeout=60.0,
                )
                v_data = v_resp.json() if v_resp.status_code == 200 else {}
                v_text = str(
                    (((v_data.get("choices") or [{}])[0]).get("message") or {}).get("content") or ""
                )
                accepted_bug = response_accepted_draft(v_text, ver_cfg)
                final_code = bprob.buggy_draft if accepted_bug else v_text
                recovered_pass, _ = evaluate_humaneval_functional_pass(
                    code_str=final_code, problem=bprob
                )
                guard_results.append(
                    {
                        "task_id": bprob.task_id,
                        "buggy_draft_pass_at_1": bug_passes,
                        "verifier_rejected_bug": not accepted_bug,
                        "recovered_pass_at_1": recovered_pass,
                    }
                )

            n = max(1, len(suite))
            paired_stats = compute_paired_mcnemar_and_confusion(task_rows)
            target_summary = {
                "target_model": target_model,
                "warmup_target_ms": warmup_target_ms,
                "baseline_pass_at_1_pct": round((base_pass_count / n) * 100.0, 1),
                "speculative_pass_at_1_pct": round((spec_pass_count / n) * 100.0, 1),
                "acceptance_rate_pct": round((accepted_count / n) * 100.0, 1),
                "baseline_total_ms": round(total_base_ms, 1),
                "speculative_total_ms": round(total_spec_ms, 1),
                "net_latency_saved_ms": round(total_base_ms - total_spec_ms, 1),
                "overall_speedup_factor": (
                    round(total_base_ms / total_spec_ms, 2) if total_spec_ms > 0 else 1.0
                ),
                "baseline_target_output_tokens": total_base_out_tok,
                "speculative_target_output_tokens": total_spec_out_tok,
                "target_output_token_reduction_pct": (
                    round(
                        ((total_base_out_tok - total_spec_out_tok) / total_base_out_tok) * 100.0,
                        1,
                    )
                    if total_base_out_tok > 0
                    else 0.0
                ),
                **paired_stats,
                "buggy_draft_guardrail_tests": guard_results,
            }

    local_pass_count = sum(1 for r in task_rows if r["local_pass_at_1"])
    n_total = max(1, len(suite))
    out: dict[str, Any] = {
        "benchmark": "HumanEval",
        "problems_evaluated": len(suite),
        "local_model": cfg.local_model,
        "warmup_local_ms": warmup_local_ms,
        "local_pass_at_1_pct": round((local_pass_count / n_total) * 100.0, 1),
        "avg_local_draft_ms": round(sum(r["local_draft_ms"] for r in task_rows) / n_total, 1),
        "tasks": task_rows,
    }
    if target_summary:
        out["target_comparison"] = target_summary
    return out
