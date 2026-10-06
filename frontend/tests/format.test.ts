import { describe, expect, it } from 'vitest'
import { compactValue, exclusiveEnd, formatValue, numberValue, periodLabel } from '../src/lib/format'
describe('deterministic presentation', () => {
  it('does not turn an undefined or null denominator into zero', () => { expect(numberValue(null)).toBeNull(); expect(formatValue(null, 'avg_order_value')).toBe('—'); expect(formatValue(undefined, 'order_count')).toBe('—') })
  it('keeps exact numeric meaning in normal-size labels and uses USD', () => { expect(formatValue('123456.78', 'sales_amount')).toBe('$123,456.78'); expect(formatValue('97215', 'order_count')).toBe('97,215'); expect(compactValue('1256000', 'sales_amount')).toBe('$1.26M') })
  it('uses percentage points supplied by the server, not fractional scaling', () => { expect(formatValue('10.3', 'change_pct_sales_amount')).toBe('+10.30%'); expect(formatValue('-3', 'change_pct_units_sold')).toBe('-3.00%') })
  it('keeps inclusive UI and exclusive API dates aligned at month/year/leap boundaries', () => { expect(exclusiveEnd('2025-12-31')).toBe('2026-01-01'); expect(exclusiveEnd('2024-02-29')).toBe('2024-03-01'); expect(periodLabel({ start: '2024-02-01', end: '2024-03-01' })).toBe('2024-02-01 至 2024-02-29') })
  it('fails closed for nonfinite numbers', () => { expect(numberValue('NaN')).toBeNull(); expect(numberValue(Infinity)).toBeNull(); expect(formatValue('Infinity', 'sales_amount')).toBe('—') })
})
