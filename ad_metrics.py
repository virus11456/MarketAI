"""Deterministic, conservative ad metrics with source-line provenance.

Amounts are decimal strings on the wire. Unknown values stay unknown; this
module never asks a language model to parse, calculate, or validate numbers.
"""
import csv
import hashlib
import io
import json
import re
from decimal import Decimal, ROUND_HALF_UP, localcontext

BASE = ('spend', 'impressions', 'clicks', 'conversions', 'revenue')
RATIOS = ('ctr', 'cpc', 'cpm', 'cpa', 'roas')
LABELS = dict(zip(BASE + RATIOS, ('花費', '曝光', '點擊', '成果', '轉換價值', 'CTR (%)', 'CPC', 'CPM', 'CPA', 'ROAS')))
FORMULAS = {
    'ctr': ('clicks', 'impressions', 100, '點擊 ÷ 曝光 × 100'),
    'cpc': ('spend', 'clicks', 1, '花費 ÷ 點擊'),
    'cpm': ('spend', 'impressions', 1000, '花費 ÷ 曝光 × 1000'),
    'cpa': ('spend', 'conversions', 1, '花費 ÷ 同一定義的成果'),
    'roas': ('revenue', 'spend', 1, '轉換價值 ÷ 花費'),
}
ALIASES = {
    'spend': ['花費', '費用', '花費金額', '總花費', 'spend', 'amount spent', 'cost'],
    'impressions': ['曝光', '曝光次數', 'impressions', 'impr.'],
    'clicks': ['點擊', '點擊次數', '連結點擊次數', 'clicks', 'link clicks'],
    'conversions': ['成果', '轉換', '轉換次數', 'conversions', 'results'],
    'revenue': ['轉換價值', '購買轉換值', 'conversion value', 'conv. value'],
    'ctr': ['ctr', '點閱率', '點擊率'],
    'cpc': ['cpc', '平均單次點擊出價', 'avg. cpc'],
    'cpm': ['cpm', '每千次曝光成本', 'avg. cpm'],
    'cpa': ['cpa', '每次成果成本', '單次轉換費用', 'cost / conv.'],
    'roas': ['roas', '轉換價值/費用', 'conv. value / cost'],
    'currency': ['幣別', '貨幣代碼', 'currency', 'currency code'],
    'conversion_type': ['成果指標', '轉換事件', '成果定義', 'result indicator', 'result type'],
    'name': ['廣告活動', '廣告活動名稱', '廣告組合', '廣告組合名稱', '名稱', 'campaign', 'campaign name', 'ad set name'],
    'start': ['報告開始', '報告開始日期', '開始日期', 'reporting starts', 'start date'],
    'end': ['報告結束', '報告結束日期', '結束日期', 'reporting ends', 'end date'],
}
CURRENCIES = {'TWD', 'USD', 'HKD', 'JPY', 'EUR', 'CNY', 'GBP', 'SGD', 'AUD', 'CAD'}


def normalized(value):
    return re.sub(r'\s+', '', value.strip().lower().replace('（', '(').replace('）', ')'))


def field_name(value):
    clean = re.sub(r'\((?:twd|usd|hkd|jpy|eur|cny|gbp|sgd|aud|cad|%)\)', '', normalized(value))
    return next((field for field, aliases in ALIASES.items() if clean in map(normalized, aliases)), None)


def currencies_in(value):
    found = set(re.findall(r'\b(?:TWD|USD|HKD|JPY|EUR|CNY|GBP|SGD|AUD|CAD)\b', value.upper()))
    if re.search(r'NT\$|NTD', value, re.I):
        found.add('TWD')
    return found


def number(value):
    text = value.strip()
    if text.lower() in ('', '-', '—', '--', 'n/a', 'null', '未提供'):
        return None
    text = re.sub(r'^(?:NT\$|TWD|USD|HKD|JPY|EUR|CNY|GBP|SGD|AUD|CAD)\s*', '', text, flags=re.I)
    if not re.fullmatch(r'(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
        raise ValueError('必須是非負數字，千分位需使用三位分組；不接受百分比或含糊的貨幣符號')
    result = Decimal(text.replace(',', ''))
    if result > Decimal('1e18') or len(result.as_tuple().digits) > 24:
        raise ValueError('數字超出可處理範圍')
    return result


def display(value):
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 60
        return format(value.quantize(Decimal('.0001'), rounding=ROUND_HALF_UP), 'f').rstrip('0').rstrip('.') or '0'


def input_rows(text):
    # CSV quoting is preserved, including embedded delimiters and newlines.
    delimiter = next((d for d in ('|', '\t', ',') if any(sum(field_name(c) in BASE for c in row) >= 2 for row in csv.reader(io.StringIO(text), delimiter=d))), None)
    if delimiter is None:
        return None
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = []
    previous = 0
    for cells in reader:
        rows.append((previous + 1, [cell.strip() for cell in cells]))
        previous = reader.line_num
    return rows


def extract_rows(text, errors, warnings):
    table = input_rows(text)
    if table is None:
        # Explicit field/value lines only. Repeated fields are ambiguous, not totals.
        values, lines = {}, []
        for line_no, line in enumerate(text.splitlines(), 1):
            match = re.match(r'^\s*(.+?)(?:\s*[:：]\s*|\s+)([^\n]+?)\s*$', line)
            field = field_name(match[1]) if match else None
            if not field:
                if line.strip():
                    warnings.append(f'第 {line_no} 行未納入計算；請核對是否包含其他活動或數據。')
                continue
            if field in values:
                errors.append(f'第 {line_no} 行重複欄位 {match[1]}；多筆活動請使用表格。')
            values[field] = match[2]
            lines.append(line_no)
        return [{'line': lines[0] if lines else 1, 'source_lines': lines, 'values': values, 'header_currency': currencies_in(text)}] if any(k in values for k in BASE) else []
    header = None
    records = []
    for line_no, cells in table:
        fields = [field_name(c) for c in cells]
        if sum(f in BASE for f in fields) >= 2:
            if header is not None:
                errors.append(f'第 {line_no} 行出現第二組表頭，請每個平台只提供一張同層級報表。')
                continue
            header = fields
            if len([f for f in fields if f]) != len(set(f for f in fields if f)):
                errors.append(f'第 {line_no} 行有重複或同義欄位，請保留一個明確的指標欄。')
            header_currency = currencies_in(' '.join(cells))
            continue
        if not any(cells) or all(re.fullmatch(r'[-: ]*', c) for c in cells):
            continue
        if header is None:
            warnings.append(f'第 {line_no} 行是表頭前的說明，未納入計算；期間需自行確認。')
            continue
        if len(cells) != len(header):
            errors.append(f'第 {line_no} 行欄數不符，請確認分隔符號與引號。')
            continue
        values = {f: cells[i] for i, f in enumerate(header) if f}
        label = values.get('name') or cells[0]
        records.append({'line': line_no, 'source_lines': [line_no], 'values': values, 'header_currency': header_currency,
                        'identity': tuple(cells),
                        'total': bool(re.match(r'^(?:(?:總計|總和|合計)(?:[：:\s]|$)|grand total\b|total(?:\s|:|$))', label, re.I))})
    return records


def audit_platform(platform, text, metadata, month):
    errors, warnings = [], []
    records = extract_rows(text, errors, warnings)
    if not records:
        errors.append('無法辨識數據。請提供有表頭的 CSV／表格，或「花費：100」等欄位和值。')
    details = [r for r in records if not r.get('total')]
    totals = [r for r in records if r.get('total')]
    selected = details or totals
    if not details and len(totals) > 1:
        errors.append('只有多個總計列，無法判斷涵蓋範圍；請提供明細或一個平台總計。')
    if totals and details:
        warnings.append('總計列不參與加總，僅使用明細列；請確認明細沒有重複或重疊。')
    elif totals:
        warnings.append('僅使用單一總計列，請確認它涵蓋完整報表。')
    supplied_currency = metadata.get('currency', '').strip().upper()
    supplied_type = metadata.get('conversion_type', '').strip()
    if supplied_currency and supplied_currency not in CURRENCIES:
        errors.append('不支援的幣別代碼。')
    parsed = []
    seen = set()
    for record in selected:
        values = record['values']
        signature = record.get('identity', tuple(sorted(values.items())))
        if signature in seen:
            errors.append(f'第 {record["line"]} 行與另一列完全相同，請確認是否重複匯入。')
        seen.add(signature)
        metrics = {}
        for field in BASE:
            try:
                metrics[field] = number(values.get(field, ''))
                if field in ('impressions', 'clicks') and metrics[field] is not None and metrics[field] != metrics[field].to_integral_value():
                    raise ValueError('曝光與點擊必須是整數')
            except ValueError as exc:
                errors.append(f'第 {record["line"]} 行 {LABELS[field]}：{exc}')
                metrics[field] = None
        raw_currency = values.get('currency', '').strip().upper()
        if raw_currency and raw_currency not in CURRENCIES:
            errors.append(f'第 {record["line"]} 行幣別代碼無法辨識。')
        currency = record['header_currency'] | currencies_in(raw_currency)
        for field in ('spend', 'revenue'):
            currency |= currencies_in(values.get(field, ''))
        if supplied_currency:
            currency.add(supplied_currency)
        if len(currency) > 1:
            errors.append(f'第 {record["line"]} 行幣別互相衝突，請拆分資料或修正幣別。')
        currency = next(iter(currency)) if len(currency) == 1 else None
        event = values.get('conversion_type', '').strip()
        if event and supplied_type and normalized(event) != normalized(supplied_type):
            errors.append(f'第 {record["line"]} 行成果定義與填寫的定義不符。')
        event = event or supplied_type or None
        for field in ('start', 'end'):
            date = values.get(field, '')
            if date:
                try:
                    from datetime import date as date_type
                    parsed_date = date_type.fromisoformat(date.replace('/', '-'))
                    if parsed_date.strftime('%Y-%m') != month:
                        errors.append(f'第 {record["line"]} 行日期不在所選月份。')
                except ValueError:
                    errors.append(f'第 {record["line"]} 行日期需使用 YYYY-MM-DD。')
        if values.get('start') and values.get('end') and values['start'].replace('/', '-') > values['end'].replace('/', '-'):
            errors.append(f'第 {record["line"]} 行開始日期晚於結束日期。')
        parsed.append({**record, 'metrics': metrics, 'currency': currency, 'conversion_type': event})
    currencies = {r['currency'] for r in parsed}
    events = {r['conversion_type'] for r in parsed if r['metrics']['conversions'] is not None or r['metrics']['revenue'] is not None}
    currency = next(iter(currencies)) if len(currencies) == 1 and None not in currencies else None
    event = next(iter(events)) if len(events) == 1 and None not in events else None
    if len(currencies - {None}) > 1:
        errors.append('同一平台含多個幣別，請拆分為不同平台資料。')
    if not currency:
        warnings.append('幣別未確認，花費與金額比率不加總；請補填幣別。')
    if not event:
        warnings.append('成果定義缺少或不同，成果、轉換價值、CPA 與 ROAS 不加總；請拆分不同事件。')
    calculated = {}
    reasons = {}
    with localcontext() as ctx:
        ctx.prec = 60
        for field in BASE:
            values = [r['metrics'][field] for r in parsed]
            if not values or any(v is None for v in values):
                calculated[field] = None
                reasons[field] = '來源有缺值；不以零補齊'
            elif field in ('spend', 'revenue') and not currency:
                calculated[field] = None
                reasons[field] = '幣別未確認或不一致'
            elif field in ('conversions', 'revenue') and not event:
                calculated[field] = None
                reasons[field] = '成果定義未確認或不一致'
            else:
                calculated[field] = sum(values, Decimal(0))
        for field, (numerator, denominator, multiplier, formula) in FORMULAS.items():
            n, d = calculated[numerator], calculated[denominator]
            calculated[field] = n / d * multiplier if n is not None and d is not None and d > 0 else None
            if calculated[field] is None:
                reasons[field] = '分子／分母未確認，或分母為零'
    comparisons = []
    for row in parsed:
        for key, (num, den, factor, formula) in FORMULAS.items():
            raw = row['values'].get(key, '').strip()
            n, d = row['metrics'][num], row['metrics'][den]
            if not raw or n is None or d is None or d == 0:
                continue
            if key == 'ctr' and not raw.endswith('%'):
                warnings.append(f'第 {row["line"]} 行 CTR 未標示百分比，原值不納入比對；採用點擊／曝光重新計算。')
                continue
            try:
                reported = number(raw.removesuffix('%'))
            except ValueError:
                warnings.append(f'第 {row["line"]} 行 {key.upper()} 格式不明，原值不納入比對。')
                continue
            with localcontext() as ctx:
                ctx.prec = 60
                expected = n / d * factor
            if reported is not None and abs(reported - expected) > Decimal('.01'):
                comparisons.append({'line': row['line'], 'metric': key, 'reported': display(reported), 'calculated': display(expected)})
                warnings.append(f'第 {row["line"]} 行 {key.upper()} 原值 {display(reported)} 與程式計算 {display(expected)} 不符，請核對欄位定義。')
    if details:
        for row in totals:
            for key in BASE:
                try:
                    reported = number(row['values'].get(key, ''))
                except ValueError:
                    continue
                if reported is not None and calculated[key] is not None and abs(reported - calculated[key]) > Decimal('.01'):
                    warnings.append(f'第 {row["line"]} 行總計的 {LABELS[key]} 與明細加總不同，請確認報表篩選範圍。')
    if any(v is None for v in calculated.values()):
        warnings.append('部分指標缺少必要資料或分母為零，顯示「未計算」，不視為 0。')
    return {
        'platform': platform, 'currency': currency, 'conversion_type': event,
        'metrics': {k: display(v) for k, v in calculated.items()}, 'reasons': reasons,
        'warnings': list(dict.fromkeys(warnings)), 'errors': errors,
        'rows': [{'line': r['line'], 'source_lines': r['source_lines'], 'name': r['values'].get('name', ''),
                  'currency': r['currency'], 'conversion_type': r['conversion_type'],
                  'metrics': {k: display(v) for k, v in r['metrics'].items()}} for r in parsed],
        'excluded_total_lines': [r['line'] for r in totals] if details else [], 'comparisons': comparisons,
    }


def audit_report(data):
    if not isinstance(data, dict):
        raise ValueError('請提供報告資料物件。')
    for field in ('client_name', 'company_name'):
        if not isinstance(data.get(field), str) or not data[field].strip() or len(data[field]) > 200:
            raise ValueError('客戶與公司名稱必須是 1 至 200 字元的文字。')
    month = data.get('report_month', '')
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('請提供有效的報告月份 YYYY-MM。')
    if month.startswith('0000'):
        raise ValueError('報告年份無效。')
    platforms = data.get('platforms')
    if not isinstance(platforms, dict) or not platforms or len(platforms) > 12:
        raise ValueError('請提供 1 至 12 個平台的資料。')
    metadata = data.get('platform_metadata', {})
    if not isinstance(metadata, dict):
        raise ValueError('平台設定格式錯誤。')
    for platform, text in platforms.items():
        if not platform.strip() or len(platform) > 100:
            raise ValueError('平台名稱必須是 1 至 100 字元。')
        if not isinstance(text, str) or not text.strip() or len(text) > 200000 or len(text.splitlines()) > 5000:
            raise ValueError('每個平台需有文字資料，最多 200,000 字元及 5,000 行。')
        meta = metadata.get(platform, {})
        if not isinstance(meta, dict) or any(not isinstance(meta.get(k, ''), str) or len(meta.get(k, '')) > 100 for k in ('currency', 'conversion_type')):
            raise ValueError('幣別與成果定義需為不超過 100 字元的文字。')
    try:
        entries = [audit_platform(p, t, metadata.get(p, {}), month) for p, t in platforms.items()]
    except csv.Error as exc:
        raise ValueError('CSV 格式錯誤或單一儲存格過長，請修正後再試。') from exc
    canonical = {k: data.get(k) for k in ('client_name', 'company_name', 'report_month', 'platforms', 'platform_metadata', 'period_confirmed')}
    fingerprint = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    warnings = ['不同平台的成果與轉換價值可能重複歸因，本報告不產生跨平台總成果或整體 ROAS。',
                '程式計算僅驗算輸入資料；仍需人工確認期間、幣別、資料層級與重複列。']
    if data.get('period_confirmed') is not True:
        warnings.append('尚未確認各平台的資料期間與所選月份一致。')
    return {'version': 1, 'fingerprint': fingerprint, 'valid': all(not e['errors'] for e in entries),
            'platforms': entries, 'warnings': warnings, 'formulas': {k: v[3] for k, v in FORMULAS.items()}}


def markdown_cell(value):
    return str(value).replace('|', '／').replace('\n', ' ').replace('\r', ' ')


def metrics_markdown(entry):
    rows = ['| 指標 | 程式計算值 |', '| --- | --- |']
    rows += [f'| {LABELS[k]} | {entry["metrics"][k] if entry["metrics"][k] is not None else "未計算"} |' for k in BASE + RATIOS]
    rows += ['', f'幣別：{markdown_cell(entry["currency"] or "未確認")}；成果定義：{markdown_cell(entry["conversion_type"] or "未確認")}',
             '來源行：' + ', '.join(str(line) for row in entry['rows'] for line in row['source_lines'])]
    rows += [f'- {w}' for w in entry['warnings']]
    return '\n'.join(rows)
