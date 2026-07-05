from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from app.services.calibration_lab_service import analyze_votes  # noqa: E402


def _table(title: str, rows: list[tuple[str, Any]]) -> list[str]:
    lines = [f"## {title}", "", "| item | value |", "| --- | --- |"]
    for key, value in rows:
        lines.append(f"| {key} | {value} |")
    lines.append("")
    return lines


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Calibration Lab Summary",
        "",
        f"- votes: {report['voteCount']}",
        (
            "- auto-score correlation with human winner: "
            f"{report['autoScoreCorrelationWithHumanWinner']}"
        ),
        "",
    ]

    lines.extend(
        _table(
            "Win Rate By Prompt Variant",
            [
                (
                    key,
                    f"{value['wins']} / {value['seen']} ({value['winRate']})",
                )
                for key, value in report["winRateByPromptVariant"].items()
            ],
        )
    )
    lines.extend(
        _table(
            "Win Rate By Task Type",
            [
                (
                    key,
                    f"{value['wins']} / {value['seen']} ({value['winRate']})",
                )
                for key, value in report["winRateByTaskType"].items()
            ],
        )
    )
    lines.extend(_table("Positive Tags", report["positiveTagFrequency"]))
    lines.extend(_table("Negative Tags", report["negativeTagFrequency"]))

    lines.append("## Suggested Prompt Changes")
    lines.append("")
    if report["suggestedPromptChanges"]:
        lines.extend(f"- {item}" for item in report["suggestedPromptChanges"])
    else:
        lines.append("- Not enough signal yet.")
    lines.append("")

    lines.append("## Top Winning Candidates")
    lines.append("")
    for candidate in report["topWinningCandidates"]:
        lines.append(
            f"- {candidate.get('candidateId')} / {candidate.get('promptVariant')} / "
            f"score {candidate.get('autoScore')}"
        )
    if not report["topWinningCandidates"]:
        lines.append("- None")
    lines.append("")

    lines.append("## Top Rejected Candidates")
    lines.append("")
    for candidate in report["topRejectedCandidates"]:
        lines.append(
            f"- {candidate.get('candidateId')} / {candidate.get('promptVariant')} / "
            f"score {candidate.get('autoScore')}"
        )
    if not report["topRejectedCandidates"]:
        lines.append("- None")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze Calibration Lab JSONL votes.")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()

    report = analyze_votes()
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
