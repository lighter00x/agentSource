"""Field comparison of real legacy silver backups against exported P6 deliveries."""

import argparse
import json
from pathlib import Path

from drugpaper.evals.silver import evaluate, report_markdown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--references", type=Path, default=Path("tests/fixtures/silver"))
    parser.add_argument("--predictions", type=Path, default=Path("samples/outputs/phase-6/silver"))
    parser.add_argument("--output", type=Path, default=Path("samples/outputs/phase-6"))
    args = parser.parse_args()
    result = evaluate(args.references, args.predictions)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "silver_report.md").write_text(report_markdown(result))
    (args.output / "silver_report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))
    if not result["complete"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
