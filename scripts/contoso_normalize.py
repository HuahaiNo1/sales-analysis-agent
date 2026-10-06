#!/usr/bin/env python3
"""Validate official Contoso CSVs and publish a small, decimal-exact app dataset."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

FACT_COLUMNS = {
    'OrderKey': 'order_key', 'LineNumber': 'line_number', 'OrderDate': 'order_date',
    'DeliveryDate': 'delivery_date', 'CustomerKey': 'customer_key', 'StoreKey': 'store_key',
    'ProductKey': 'product_key', 'Quantity': 'quantity', 'UnitPrice': 'unit_price',
    'NetPrice': 'net_price', 'UnitCost': 'unit_cost', 'CurrencyCode': 'currency_code',
    'ExchangeRate': 'exchange_rate',
}
DIMENSIONS = {
    'product': ('dim_product', 'product_key', {
        'ProductKey': 'product_key', 'ProductName': 'product_name', 'CategoryKey': 'category_key',
        'CategoryName': 'category_name', 'SubCategoryKey': 'subcategory_key',
        'SubCategoryName': 'subcategory_name', 'Brand': 'brand'}),
    'store': ('dim_store', 'store_key', {
        'StoreKey': 'store_key', 'Description': 'store_name', 'CountryName': 'store_country',
        'CountryCode': 'store_country_code', 'State': 'state'}),
    'customer': ('dim_customer_geo', 'customer_key', {
        'CustomerKey': 'customer_key', 'CountryFull': 'customer_country',
        'Country': 'customer_country_code', 'StateFull': 'customer_state'}),
}


def read_csv(path: Path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def write_csv(path: Path, columns, rows):
    with path.open('w', encoding='utf-8', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(data_dir: Path):
    raw, output = data_dir / 'raw', data_dir / 'processed'
    output.mkdir(parents=True, exist_ok=True)
    errors = Counter()
    tables, keys = {}, {}
    for source, (table, primary_key, mapping) in DIMENSIONS.items():
        rows = [{target: row[source_col] for source_col, target in mapping.items()}
                for row in read_csv(raw / f'{source}.csv')]
        values = [row[primary_key] for row in rows]
        errors[f'{table}_duplicate_key'] = len(values) - len(set(values))
        errors[f'{table}_missing_key'] = sum(not value for value in values)
        tables[table] = (list(mapping.values()), rows)
        keys[primary_key] = set(values)
    dates = []
    for row in read_csv(raw / 'date.csv'):
        day = date.fromisoformat(row['Date'])
        dates.append(dict(date=day.isoformat(), year=day.year, quarter=(day.month-1)//3+1,
                          month=day.month, year_month=day.strftime('%Y-%m'),
                          week_start=(day-timedelta(days=day.weekday())).isoformat()))
    keys['date'] = {row['date'] for row in dates}
    errors['dim_date_duplicate_key'] = len(dates) - len(keys['date'])
    tables['dim_date'] = (['date', 'year', 'quarter', 'month', 'year_month', 'week_start'], dates)

    fact, line_keys, order_contexts = [], set(), {}
    months, daily_rows, yearly_rows, yearly_orders = Counter(), Counter(), Counter(), {}
    max_scale = dict.fromkeys(['unit_price', 'net_price', 'unit_cost', 'exchange_rate'], 0)
    totals = dict(sales_amount=Decimal(0), gross_profit=Decimal(0), units_sold=0)
    sample = []
    for row in read_csv(raw / 'sales.csv'):
        record = {target: row[source] for source, target in FACT_COLUMNS.items()}
        key = (record['order_key'], record['line_number'])
        errors['duplicate_order_line'] += key in line_keys
        line_keys.add(key)
        for field in ['customer_key', 'store_key', 'product_key']:
            errors[f'missing_{field}_foreign_key'] += record[field] not in keys[field]
        day = record['order_date']
        errors['missing_order_date_foreign_key'] += day not in keys['date']
        errors['missing_delivery_date_foreign_key'] += record['delivery_date'] not in keys['date']
        errors['order_date_outside_2023_2025'] += not ('2023-01-01' <= day <= '2025-12-31')
        errors['delivery_before_order'] += record['delivery_date'] < day
        context = tuple(record[field] for field in ['order_date', 'delivery_date', 'customer_key', 'store_key', 'currency_code', 'exchange_rate'])
        errors['inconsistent_order_context'] += record['order_key'] in order_contexts and order_contexts[record['order_key']] != context
        order_contexts[record['order_key']] = context
        quantity = int(record['quantity'])
        errors['nonpositive_quantity'] += quantity <= 0
        amounts = {field: Decimal(record[field]) for field in max_scale}
        for field, value in amounts.items():
            errors['invalid_decimal'] += not value.is_finite()
            scale = max(0, -value.as_tuple().exponent)
            max_scale[field] = max(max_scale[field], scale)
            errors['numeric_18_6_overflow_or_rounding'] += scale > 6 or abs(value) >= Decimal('1000000000000')
        errors['nonpositive_sale_price'] += amounts['unit_price'] <= 0 or amounts['net_price'] <= 0
        errors['negative_cost'] += amounts['unit_cost'] < 0
        errors['net_price_above_unit_price'] += amounts['net_price'] > amounts['unit_price']
        errors['non_usd_or_nonunit_exchange_rate'] += record['currency_code'] != 'USD' or amounts['exchange_rate'] != 1
        # Discount weights in the archived official config support integer rates 0..14%.
        errors['discount_semantics_mismatch'] += not any(
            amounts['unit_price'] * (100-discount) / 100 == amounts['net_price']
            for discount in range(15))
        sales = quantity * amounts['net_price']
        cost = quantity * amounts['unit_cost']
        totals['sales_amount'] += sales
        totals['gross_profit'] += sales-cost
        totals['units_sold'] += quantity
        months[day[:7]] += 1
        daily_rows[day] += 1
        yearly_rows[day[:4]] += 1
        yearly_orders.setdefault(day[:4], set()).add(record['order_key'])
        if len(sample) < 3:
            sample.append({**record, 'line_sales_amount': str(sales), 'line_gross_profit': str(sales-cost)})
        fact.append(record)
    errors['unexpected_month_coverage'] = int(len(months) != 36)
    errors['unexpected_date_bounds'] = int(not daily_rows or min(daily_rows) != '2023-01-01' or max(daily_rows) != '2025-12-31')
    errors['unexpected_order_count'] = int(not 90000 <= len(order_contexts) <= 110000)
    errors['duplicate_orders_tables_present'] = int(any(raw.glob('orders*.csv')) or any(raw.glob('orderrows*.csv')))
    tables['fact_sales'] = (list(FACT_COLUMNS.values()), fact)
    report = {
        'status': 'passed' if not any(errors.values()) else 'failed',
        'source': 'SQLBI Contoso Data Generator V2', 'synthetic': True, 'currency': 'USD',
        'order_count': len(order_contexts), 'sales_row_count': len(fact),
        'table_rows': {table: len(rows) for table, (_, rows) in tables.items()},
        'coverage': {'start': min(daily_rows) if daily_rows else None, 'end': max(daily_rows) if daily_rows else None,
                     'months': len(months), 'days_with_sales': len(daily_rows)},
        'years': {year: {'orders': len(yearly_orders[year]), 'sales_rows': yearly_rows[year]} for year in sorted(yearly_rows)},
        'maximum_decimal_scale': max_scale, 'anomaly_counts': dict(errors),
        'totals_exact': {**{key: str(value) for key, value in totals.items()}, 'order_count': len(order_contexts)},
        'samples': sample,
        'raw_files': {path.name: {'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in sorted(raw.glob('*.csv'))},
    }
    provenance = data_dir / 'provenance'
    provenance.mkdir(exist_ok=True)
    if any(errors.values()):
        (provenance/'validation-report.json').write_text(json.dumps(report, indent=2)+'\n')
        raise ValueError(f'Contoso validation failed; no normalized tables published: {dict(errors)}')
    for table, (columns, rows) in tables.items():
        write_csv(output/f'{table}.csv', columns, rows)
    report['processed_files'] = {path.name: {'bytes': path.stat().st_size, 'sha256': sha256(path)} for path in sorted(output.glob('*.csv'))}
    report['data_version'] = 'contoso-v2-2023-2025-' + report['processed_files']['fact_sales.csv']['sha256'][:12]
    (provenance/'validation-report.json').write_text(json.dumps(report, indent=2)+'\n')
    (output/'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({key: report[key] for key in ['status', 'data_version', 'order_count', 'sales_row_count', 'coverage', 'maximum_decimal_scale', 'totals_exact']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path(__file__).resolve().parents[1]/'data')
    normalize(parser.parse_args().data_dir)
