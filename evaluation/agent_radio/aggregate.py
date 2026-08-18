"""Turn run records into a comparison table that refuses to be over-read.

Reporting a number is the easy half. The gates the ADR set -- twenty tasks or
five repeats per type, five samples before a p95 means anything -- are stated
in the table itself, because a threshold that lives only in a document is one
a tired reader approves past.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import quantiles

from agent_radio.fixture import Fixture, Record, is_stale, rubric_is_fully_reported

BASELINE_MODE = "legacy"
MINIMUM_REPEATS_PER_TYPE = 5
MINIMUM_TASKS = 20
P95_MINIMUM_SAMPLES = 5


@dataclass(frozen=True)
class Cell:
    mode: str
    samples: int
    success_rate: float
    critical_defects: int
    rework: int
    cost_tokens: int
    p50_ms: int
    p95_ms: int | None


@dataclass(frozen=True)
class Report:
    rows: dict[str, tuple[Cell, ...]]
    stale_dropped: int
    unreported_dropped: int
    warnings: tuple[str, ...]


def build_report(
    fixtures: Mapping[str, Fixture],
    records: Sequence[Record],
) -> Report:
    fresh = [record for record in records if not is_stale(record, fixtures)]
    stale_dropped = len(records) - len(fresh)
    # A record that answers fewer items than its rubric defines is not evidence:
    # omitting the item you know failed produces something indistinguishable
    # from a full pass. Counted apart from stale, because "someone changed the
    # definition" and "someone scored only part of it" are different problems
    # and reporting them as one hides both.
    live = [
        record
        for record in fresh
        if rubric_is_fully_reported(record, fixtures[record.fixture_id])
    ]
    unreported_dropped = len(fresh) - len(live)

    by_type: dict[str, dict[str, list[Record]]] = {}
    for record in live:
        fixture_type = fixtures[record.fixture_id].type
        by_type.setdefault(fixture_type, {}).setdefault(record.mode, []).append(record)

    rows = {
        fixture_type: tuple(
            _cell(mode, group[mode]) for mode in _mode_order(group)
        )
        for fixture_type, group in sorted(by_type.items())
    }
    return Report(
        rows, stale_dropped, unreported_dropped, _warnings(fixtures, live, by_type)
    )


def render(report: Report) -> str:
    lines: list[str] = []
    for fixture_type, cells in report.rows.items():
        lines.append(f"## {fixture_type}")
        lines.append(
            "| mode | n | success | critical defects | rework | tokens | p50 ms | p95 ms |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for cell in cells:
            p95 = "n/a" if cell.p95_ms is None else str(cell.p95_ms)
            lines.append(
                f"| {cell.mode} | {cell.samples} | {cell.success_rate:.0%} | "
                f"{cell.critical_defects} | {cell.rework} | {cell.cost_tokens} | "
                f"{cell.p50_ms} | {p95} |"
            )
        lines.append("")
    if report.stale_dropped:
        lines.append(
            f"stale 기록 {report.stale_dropped}건 제외됨 "
            "(fixture 정의가 측정 이후 바뀜)"
        )
    if report.unreported_dropped:
        lines.append(
            f"미완 채점 {report.unreported_dropped}건 제외됨 "
            "(rubric 항목을 전부 기록하지 않음)"
        )
    lines.extend(report.warnings)
    return "\n".join(lines)


def _cell(mode: str, records: list[Record]) -> Cell:
    durations = sorted(record.wall_ms for record in records)
    return Cell(
        mode=mode,
        samples=len(records),
        success_rate=sum(record.succeeded for record in records) / len(records),
        critical_defects=sum(record.critical_defects_found for record in records),
        rework=sum(record.rework_count for record in records),
        cost_tokens=sum(
            int(record.cost.get("input_tokens", 0) or 0)
            + int(record.cost.get("output_tokens", 0) or 0)
            for record in records
        ),
        p50_ms=_percentile(durations, 50),
        p95_ms=(
            _percentile(durations, 95)
            if len(durations) >= P95_MINIMUM_SAMPLES
            else None
        ),
    )


def _percentile(durations: list[int], percent: int) -> int:
    # `inclusive` handles a single sample -- verified on this interpreter,
    # quantiles([5], n=100, method="inclusive") is [5]*99 -- so there is no
    # small-n special case to write here.
    return int(round(quantiles(durations, n=100, method="inclusive")[percent - 1]))


def _mode_order(group: Mapping[str, list[Record]]) -> list[str]:
    """The baseline first, always: every gate is stated against legacy."""
    modes = sorted(group)
    if BASELINE_MODE in modes:
        modes.remove(BASELINE_MODE)
        return [BASELINE_MODE, *modes]
    return modes


def _warnings(
    fixtures: Mapping[str, Fixture],
    live: Sequence[Record],
    by_type: Mapping[str, Mapping[str, list[Record]]],
) -> tuple[str, ...]:
    tasks = {record.fixture_id for record in live}
    thin_types = [
        fixture_type
        for fixture_type, group in by_type.items()
        if min(len(records) for records in group.values()) < MINIMUM_REPEATS_PER_TYPE
    ]
    if len(tasks) >= MINIMUM_TASKS and not thin_types:
        return ()
    return (
        "기본 활성화 판단 불가: "
        f"태스크 {len(tasks)}/{MINIMUM_TASKS}"
        + (f", 반복 부족 {', '.join(sorted(thin_types))}" if thin_types else ""),
    )
