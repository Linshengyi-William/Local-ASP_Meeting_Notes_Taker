"""
run_eval.py — Regression eval for the note-generation step.

Runs each synthetic case in evals/cases/ through generate_notes() with the
case's own glossary, then scores the output with evals/scoring.py.

    # live run (needs OPENAI_API_KEY; ~3 short calls)
    python evals/run_eval.py --model gpt-5-mini

    # compare models
    python evals/run_eval.py --model gpt-5-mini --model gpt-5

    # re-score saved outputs without calling any API
    python evals/run_eval.py --replay evals/results/<run>/gpt-5-mini

Cases are synthetic and fictional by design: no real meeting data ever goes in
this directory. See evals/README.md for the case format.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scoring import score_case  # noqa: E402

logger = logging.getLogger("eval")

DEFAULT_CASES = Path(__file__).resolve().parent / "cases"


def discover_cases(cases_dir: Path, only: list[str] | None) -> list[Path]:
    cases = sorted(p for p in cases_dir.iterdir() if (p / "expected.json").exists())
    if only:
        wanted = set(only)
        cases = [c for c in cases if c.name in wanted]
        missing = wanted - {c.name for c in cases}
        if missing:
            raise SystemExit(f"Unknown case(s): {', '.join(sorted(missing))}")
    return cases


def load_expected(case_dir: Path) -> dict:
    return json.loads((case_dir / "expected.json").read_text(encoding="utf-8"))


def eval_config(case_dir: Path, provider: str, model: str) -> dict:
    """Committed defaults only — never the user's private config.local.yaml."""
    from utils import load_config

    cfg = load_config(ROOT / "config" / "config.yaml", use_local=False, use_env=False)
    cfg.setdefault("llm", {})
    cfg["llm"]["provider"] = provider
    cfg["llm"]["model"] = model
    cfg["llm"]["context_file"] = str((case_dir / "context.md").resolve())
    return cfg


def run_live(case_dir: Path, provider: str, model: str) -> tuple[dict, float]:
    from generate_notes import generate_notes

    expected = load_expected(case_dir)
    cfg = eval_config(case_dir, provider, model)
    started = time.monotonic()
    notes = generate_notes(
        case_dir / "transcript.txt", cfg, output_dir=None,
        provider=provider, lang=expected.get("lang"),
    )
    return notes, time.monotonic() - started


def render_markdown(model_label: str, rows: list[tuple[str, dict, float | None]]) -> str:
    lines = [
        f"### Eval: `{model_label}`",
        "",
        "| Case | Lang | Result | Names | Action recall | Leaks | Invented owners | Trad. chars | Topics | Time |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r, secs in rows:
        if "error" in r:
            lines.append(f"| {name} | — | ERROR | — | — | — | — | — | — | — |")
            continue
        leaks = sum(r["name_leaks"].values()) + sum(r["term_leaks"].values())
        lines.append(
            f"| {name} | {r['lang']} | {'PASS' if r['passed'] else 'FAIL'} "
            f"| {r['name_recall']:.0%} | {r['action_recall']:.0%} | {leaks} "
            f"| {len(r['invented_owners'])} | {len(r['traditional_chars'])} "
            f"| {r['n_discussion_topics']} | {f'{secs:.0f}s' if secs is not None else '—'} |"
        )
    failures = [(n, r) for n, r, _ in rows if r.get("failures") or "error" in r]
    if failures:
        lines += ["", "<details><summary>Failure details</summary>", ""]
        for n, r in failures:
            for f in r.get("failures") or [r.get("error")]:
                lines.append(f"- **{n}**: {f}")
        lines += ["", "</details>"]
    passed = sum(1 for _, r, _ in rows if r.get("passed"))
    lines += ["", f"**{passed}/{len(rows)} cases passed.**", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Regression eval for meeting-note generation.")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--case", action="append", help="Run only this case (repeatable).")
    parser.add_argument("--provider", default="openai", choices=["openai", "anthropic"])
    parser.add_argument("--model", action="append", help="Model to evaluate (repeatable). Default: gpt-5-mini")
    parser.add_argument("--replay", type=Path, help="Score saved <case>.json outputs from this dir; no API calls.")
    parser.add_argument("--out", type=Path, help="Where to save outputs and scores (default: evals/results/<timestamp>).")
    parser.add_argument("--markdown", type=Path, help="Append a Markdown summary here (e.g. $GITHUB_STEP_SUMMARY).")
    parser.add_argument("--strict", action="store_true", help="Exit 1 if any case fails.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    cases = discover_cases(args.cases, args.case)
    if not cases:
        raise SystemExit(f"No cases found in {args.cases}")

    if args.replay:
        runs = [("replay:" + args.replay.name, None)]
    else:
        runs = [(m, m) for m in (args.model or ["gpt-5-mini"])]

    out_root = args.out or (Path(__file__).resolve().parent / "results" / datetime.now().strftime("%Y%m%d-%H%M%S"))
    all_passed = True
    markdown_parts: list[str] = []

    for label, model in runs:
        rows: list[tuple[str, dict, float | None]] = []
        run_dir = out_root / (model or "replay")
        if model:
            run_dir.mkdir(parents=True, exist_ok=True)

        for case_dir in cases:
            expected = load_expected(case_dir)
            secs: float | None = None
            try:
                if args.replay:
                    notes = json.loads((args.replay / f"{case_dir.name}.json").read_text(encoding="utf-8"))
                else:
                    logger.info("[%s] %s ...", model, case_dir.name)
                    notes, secs = run_live(case_dir, args.provider, model)
                    (run_dir / f"{case_dir.name}.json").write_text(
                        json.dumps(notes, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                result = score_case(notes, expected)
            except Exception as exc:  # one bad case shouldn't hide the others
                logger.error("%s failed: %s", case_dir.name, exc)
                result = {"error": f"{type(exc).__name__}: {exc}", "passed": False}
            rows.append((case_dir.name, result, secs))
            all_passed &= bool(result.get("passed"))

        if model:
            (run_dir / "scores.json").write_text(
                json.dumps({n: r for n, r, _ in rows}, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        md = render_markdown(f"{args.provider}/{model}" if model else label, rows)
        markdown_parts.append(md)
        print(md)

    if args.markdown:
        with open(args.markdown, "a", encoding="utf-8") as f:
            f.write("\n".join(markdown_parts) + "\n")
    if not args.replay:
        print(f"Outputs saved under {out_root}")

    return 1 if (args.strict and not all_passed) else 0


if __name__ == "__main__":
    sys.exit(main())
