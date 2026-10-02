import {describe, expect, it} from 'vitest';
import {elapsedLabel, observedTimeline, type TimedTask} from './collaboration';

const task = (id: string, start: string | null, end: string | null): TimedTask => ({
  task_id: id, role: 'competitor', title: id,
  started_at: start && `2026-10-03T00:00:${start}Z`, finished_at: end && `2026-10-03T00:00:${end}Z`,
});

describe('observed collaboration timing', () => {
  it('shows actual temporal overlap without claiming a speedup', () => {
    const result = observedTimeline([task('b', '02', '06'), task('a', '00', '04'), task('c', '06', '08')]);
    expect(result.peak).toBe(2);
    expect(result.spanMs).toBe(8000);
    expect(result.bars.map(bar => [bar.task_id, bar.offset, bar.width])).toEqual([
      ['a', 0, 50], ['b', 25, 50], ['c', 75, 25],
    ]);
  });
  it('does not label touching intervals as parallel', () => {
    expect(observedTimeline([task('a', '00', '04'), task('b', '04', '08')]).peak).toBe(1);
  });
  it('does not invent durations for running, reversed or missing timestamps', () => {
    const result = observedTimeline([task('running', '00', null), task('missing', null, null), task('reversed', '05', '03')]);
    expect(result.bars).toEqual([]);
    expect(result.peak).toBe(0);
    expect(result.omitted).toBe(3);
  });
  it('handles zero length replay intervals without fabricated overlap or NaN', () => {
    const result = observedTimeline([task('instant', '02', '02')]);
    expect(result.peak).toBe(0);
    expect(result.bars[0].width).toBe(0);
    expect(result.bars[0].offset).toBe(0);
  });
  it('formats subsecond replay timing and real elapsed time explicitly', () => {
    expect(elapsedLabel(12)).toBe('12 毫秒');
    expect(elapsedLabel(2300)).toBe('2.3 秒');
    expect(elapsedLabel(90000)).toBe('1 分 30 秒');
    expect(elapsedLabel(NaN)).toBe('未记录');
  });
});
