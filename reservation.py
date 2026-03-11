import os
import re
import time
import warnings
from datetime import datetime, date, timedelta, timezone
import json

import requests
from bs4 import BeautifulSoup

from lib.schema import (
    format_time_label,
    normalize_time_code,
    parse_japanese_date_label,
    short_date_label,
    short_facility_name,
)

warnings.filterwarnings('ignore')

BASE_URL = "https://www.fureai-net.city.kawasaki.jp/web"
TARGET_FACILITY = '富士見テニスコート'
JST = timezone(timedelta(hours=9), name="JST")
JAPAN_HOLIDAYS = {
    "2026-01-01": "元日",
    "2026-01-12": "成人の日",
    "2026-02-11": "建国記念の日",
    "2026-02-23": "天皇誕生日",
    "2026-03-20": "春分の日",
    "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日",
    "2026-05-04": "みどりの日",
    "2026-05-05": "こどもの日",
    "2026-05-06": "休日",
    "2026-07-20": "海の日",
    "2026-08-11": "山の日",
    "2026-09-21": "敬老の日",
    "2026-09-22": "休日",
    "2026-09-23": "秋分の日",
    "2026-10-12": "スポーツの日",
    "2026-11-03": "文化の日",
    "2026-11-23": "勤労感謝の日",
}


def now_jst():
    return datetime.now(JST)


def create_session():
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                      'AppleWebKit/537.36 (KHTML, like Gecko) '
                      'Chrome/110.0.0.0 Safari/537.36',
    })
    return session


def parse(response):
    return BeautifulSoup(response.content, 'html.parser', from_encoding='shift_jis')


def get_form_data(soup):
    form = soup.find('form', {'name': 'form1'})
    data = {}
    if form:
        for inp in form.find_all('input', {'type': 'hidden'}):
            n = inp.get('name')
            if n:
                data[n] = inp.get('value', '')
    return data


def post_step(session, soup, action, extra=None):
    data = get_form_data(soup)
    if extra:
        data.update(extra)
    r = session.post(f"{BASE_URL}/{action}", data=data, timeout=20, verify=False)
    return parse(r)


def get_title(soup):
    t = soup.find('title')
    if t and 'ふれあいネット' in t.text:
        return t.text.split('ふれあいネット')[-1].strip()
    return t.text if t else 'N/A'


# ─── ナビゲーション ───────────────────────────────────────────────────────────

def navigate_to_inst_list(session):
    """ログインなしで 施設選択画面 まで遷移。戻り値: (inst_list_soup, facilities)"""
    r = session.get(f"{BASE_URL}/index.jsp", timeout=15, verify=False)
    soup = parse(r)
    print(f"Step 1 ✅ {get_title(soup)}")

    soup = post_step(session, soup, 'rsvWTransRsvMenuAction.do')
    print(f"Step 2 ✅ {get_title(soup)}")

    soup = post_step(session, soup, 'rsvWTransInstSrchPpsdAction.do')
    print(f"Step 3 ✅ {get_title(soup)}")

    soup = post_step(session, soup, 'rsvWTransInstSrchPpsAction.do',
                     {'selectPpsdCd': '100'})
    print(f"Step 4 ✅ {get_title(soup)}")

    soup = post_step(session, soup, 'rsvWTransInstSrchBuildAction.do', {
        'selectPpsdCd': '100', 'selectPpsCd': '100050', 'selectPpsPpsdCd': '100',
    })
    print(f"Step 5 ✅ {get_title(soup)}")

    soup = post_step(session, soup, 'rsvWTransInstSrchInstAction.do',
                     {'selectBldCd': '0'})
    print(f"Step 6 ✅ {get_title(soup)}")

    ul = soup.find('ul', {'id': 'list5'})
    if not ul:
        print("❌ 施設リストが見つかりません")
        return None, []

    facilities = []
    for i, li in enumerate(ul.find_all('li')):
        txt = li.get_text(strip=True)
        if TARGET_FACILITY in txt and i > 0:
            name = txt.replace(TARGET_FACILITY, '').strip()
            facilities.append({'index': i - 1, 'name': name or f'場{i}'})

    print(f"  対象施設 {len(facilities)} 面 を検出")
    return soup, facilities


# ─── 週次時間帯テーブルのパース ───────────────────────────────────────────────

def parse_week_table(soup):
    """
    rsvakitable テーブルをパースして {日付文字列: {時間帯: ステータス}} と next_available を返す。
    日付文字列: '3月10日火曜日' 形式
    ステータス: '空き' | '予約あり' | '受付期間外' | '休館日' | '保守日・主催事業' | ...
    """
    table = soup.find('table', {'class': 'rsvakitable'})
    if not table:
        return {}, None

    rows = table.find_all('tr')
    if len(rows) < 2:
        return {}, None

    # ヘッダー行: 日付列 (th class=rsvakitable3)
    header = rows[0]
    date_ths = header.find_all('th', {'class': 'rsvakitable3'})
    dates = [th.get_text(strip=True).replace('\n', '') for th in date_ths]
    # '3月10日火曜日' の形式に正規化
    dates = [re.sub(r'\s+', '', d) for d in dates]

    schedule = {d: {} for d in dates}

    for row in rows[1:]:
        time_th = row.find('th', {'class': 'rsvakitable2'})
        if not time_th:
            continue
        # 時間帯: '０９００' など（アンカーを除いたテキスト）
        time_slot = time_th.find(text=True, recursive=False)
        if not time_slot:
            # アンカーの前のテキストノードを取得
            time_slot = time_th.get_text().strip().split('\n')[0].strip()
        else:
            time_slot = time_slot.strip()

        cells = row.find_all('td')
        for i, cell in enumerate(cells):
            if i >= len(dates):
                break
            img = cell.find('img')
            if img:
                alt = img.get('alt', '')
                # alt = '3月10日火曜日０９００空き' → ステータス部分だけ抽出
                # 末尾の日本語ステータス語を取る
                status = extract_status(alt)
            else:
                status = cell.get_text(strip=True) or '-'
            schedule[dates[i]][time_slot] = status

    # 「次の週」に渡す情報は form data をそのまま使うので None を返す
    return schedule, None


def extract_status(alt_text):
    """
    img.alt から状態部分だけ抽出。
    例: '3月10日火曜日０９００空き' → '空き'
    """
    for keyword in ['空き', '予約あり', '休館日', '保守日・主催事業', '受付期間外',
                    '時間外', '一般開放', '雨天', '取消処理中', '開放予定']:
        if keyword in alt_text:
            return keyword
    return alt_text or '-'


def get_week_start_date(soup):
    """週テーブルの先頭日付を date オブジェクトで返す。"""
    table = soup.find('table', {'class': 'rsvakitable'})
    if not table:
        return None
    rows = table.find_all('tr')
    if not rows:
        return None
    # ヘッダー行の年
    year_th = rows[0].find('th', {'class': 'rsvakitable'})
    year_text = year_th.get_text(strip=True) if year_th else ''
    year_m = re.search(r'(\d{4})年', year_text)
    year = int(year_m.group(1)) if year_m else now_jst().year

    # 最初の日付列
    first_th = rows[0].find('th', {'class': 'rsvakitable3'})
    if not first_th:
        return None
    date_text = first_th.get_text(strip=True)
    m = re.search(r'(\d+)月(\d+)日', date_text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    return date(year, month, day)


# ─── 空き状況スクレイピング ───────────────────────────────────────────────────

def get_facility_schedule(session, inst_soup, facility_index, end_date):
    """
    指定コートの週次時間帯データを取得（today ～ end_date）。
    戻り値: {日付文字列: {時間帯: ステータス}}
    """
    today = now_jst()
    start_ymd = today.strftime('%Y%m%d')

    # ① 月次カレンダーを経由して週次ビューへ（最初の1回だけ）
    time.sleep(0.5)
    month_soup = post_step(session, inst_soup, 'rsvWInstSrchMonthVacantAction.do', {
        'selectInstNo': str(facility_index),
        'srchSelectYMD': start_ymd,
        'dispYY': today.strftime('%Y'),
        'dispMM': today.strftime('%m'),
        'dispDD': today.strftime('%d'),
        'selectYY': today.strftime('%Y'),
        'selectMM': today.strftime('%m'),
        'selectDD': today.strftime('%d'),
    })

    time.sleep(0.5)
    week_soup = post_step(session, month_soup, 'rsvWInstSrchVacantAction.do', {
        'srchSelectYMD': start_ymd,
        'dispYY': today.strftime('%Y'),
        'dispMM': today.strftime('%m'),
        'dispDD': today.strftime('%d'),
        'selectYY': today.strftime('%Y'),
        'selectMM': today.strftime('%m'),
        'selectDD': today.strftime('%d'),
        'transVacantMode': '7',
    })

    all_schedule = {}
    weeks_fetched = 0

    while True:
        week_start = get_week_start_date(week_soup)
        if week_start is None or week_start > end_date:
            break

        week_data, _ = parse_week_table(week_soup)
        if not week_data:
            break

        # end_date 以降の日付を除外
        end_md = (end_date.month, end_date.day)
        week_data = {d: t for d, t in week_data.items() if date_sort_key(d) <= end_md}
        all_schedule.update(week_data)
        weeks_fetched += 1
        print(f"    週 {weeks_fetched}: {week_start.strftime('%m/%d')}～ ({len(week_data)} 日分)")

        # 上限チェック（念のため）
        if weeks_fetched >= 12:
            break

        # 次の週へ（transVacantMode=4）
        time.sleep(0.4)
        week_soup = post_step(session, week_soup, 'rsvWInstSrchVacantAction.do', {
            'transVacantMode': '4',
            'srchSelectInstNo': str(facility_index),
        })

    return all_schedule


# ─── HTML 生成 ───────────────────────────────────────────────────────────────

def format_time(code):
    """'０９００' → '09:00'"""
    return format_time_label(code)


def date_sort_key(d):
    """'3月10日火曜日' → (3, 10)"""
    m = re.match(r'(\d+)月(\d+)日', d)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)


def short_date(d):
    """'3月10日火曜日' → '3/10(火)'"""
    return short_date_label(d)


def actual_date_from_label(d, today):
    return parse_japanese_date_label(d, today)


def date_meta(d, today):
    actual = actual_date_from_label(d, today)
    next_month = today.month + 1 if today.month < 12 else 1
    next_year = today.year if today.month < 12 else today.year + 1

    if actual is None:
        return {
            'key': d,
            'month_group': 'other',
            'day_group': 'weekday',
            'is_holiday': False,
            'holiday_name': '',
        }

    iso = actual.isoformat()
    is_holiday = iso in JAPAN_HOLIDAYS
    is_special = actual.weekday() >= 5 or is_holiday
    if actual.year == today.year and actual.month == today.month:
        month_group = 'current'
    elif actual.year == next_year and actual.month == next_month:
        month_group = 'next'
    else:
        month_group = 'other'

    return {
        'key': iso,
        'month_group': month_group,
        'day_group': 'special' if is_special else 'weekday',
        'is_holiday': is_holiday,
        'holiday_name': JAPAN_HOLIDAYS.get(iso, ''),
    }


def date_label(d, meta):
    label = short_date(d)
    if meta['is_holiday'] and '日)' not in label and '土)' not in label:
        return f"{label}・祝"
    return label


def cell_class(val):
    if val in ('空き',):
        return 'open'
    if val in ('予約あり',):
        return 'full'
    if val in ('受付期間外', '時間外', '-'):
        return 'unavailable'
    if val in ('休館日', '保守日・主催事業'):
        return 'closed'
    if val in ('一般開放', '開放予定'):
        return 'general'
    if val in ('雨天',):
        return 'rain'
    if val in ('取消処理中',):
        return 'cancel'
    return 'other'


def cell_display(val, cc):
    labels = {
        'open':      '○空き',
        'full':      '×予約',
        'unavailable': '—',
        'closed':    '休',
        'general':   '開放',
        'rain':      '雨',
        'cancel':    '取消',
    }
    return labels.get(cc, val[:3] if val else '-')


def write_app_entry(output_path, src):
    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="0; url=app.html?src={src}">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Redirecting...</title>
</head>
<body>
<script>location.replace('app.html?src={src}');</script>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML 保存完了: {output_path}")


def build_reservation_json(all_data, today, end_date):
    all_dates = sorted(
        {date_str for schedule in all_data.values() for date_str in schedule.keys()},
        key=date_sort_key,
    )
    all_times = sorted(
        {time_code for schedule in all_data.values() for times in schedule.values() for time_code in times.keys()},
        key=lambda value: format_time(value),
    )

    date_items = []
    for date_str in all_dates:
        meta = date_meta(date_str, today.date())
        actual = actual_date_from_label(date_str, today.date())
        date_items.append({
            'key': meta['key'],
            'source_label': date_str,
            'label': date_label(date_str, meta),
            'short_label': short_date(date_str),
            'iso_date': actual.isoformat() if actual else None,
            'month_group': meta['month_group'],
            'day_group': meta['day_group'],
            'is_holiday': meta['is_holiday'],
            'holiday_name': meta['holiday_name'],
        })

    time_items = [{
        'key': normalize_time_code(time_code),
        'source_label': time_code,
        'label': format_time(time_code),
    } for time_code in all_times]

    facilities = []
    cells = {}
    for index, facility_name in enumerate(all_data.keys(), start=1):
        facility_key = f'court_{index}'
        facilities.append({
            'key': facility_key,
            'name': facility_name,
            'short_name': short_facility_name(facility_name),
        })
        facility_cells = {}
        schedule = all_data[facility_name]
        for date_str in all_dates:
            actual = actual_date_from_label(date_str, today.date())
            if actual is None:
                continue
            date_key = actual.isoformat()
            date_cells = {}
            for time_code in all_times:
                raw_value = schedule.get(date_str, {}).get(time_code, '-')
                state = cell_class(raw_value) or 'other'
                date_cells[normalize_time_code(time_code)] = {
                    'status': state,
                    'text': cell_display(raw_value, state),
                    'raw': raw_value,
                }
            facility_cells[date_key] = date_cells
        cells[facility_key] = facility_cells

    return {
        'version': 1,
        'mode': 'reservation',
        'generated_at': today.isoformat(timespec='minutes'),
        'timezone': 'Asia/Tokyo',
        'page': {
            'title': '富士見テニスコート 予約空き状況',
            'legend': '12面の中で最も空きがある枠を表示',
            'facility_group': TARGET_FACILITY,
        },
        'range': {
            'start': today.date().isoformat(),
            'end': end_date.isoformat(),
        },
        'filters': {
            'month': True,
            'day_type': True,
            'time': True,
            'open_only': True,
            'status_buttons': [],
        },
        'dates': date_items,
        'time_slots': time_items,
        'facilities': facilities,
        'cells': cells,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("川崎市 富士見テニスコート 予約空き状況スクレイパー")
    print("=" * 60)

    today = now_jst()
    # 今月+来月末を終了日に設定
    next_month = today.month + 1 if today.month < 12 else 1
    next_year = today.year if today.month < 12 else today.year + 1
    import calendar
    last_day = calendar.monthrange(next_year, next_month)[1]
    end_date = date(next_year, next_month, last_day)
    print(f"取得期間: {today.strftime('%Y/%m/%d')} ～ {end_date}")

    session = create_session()
    inst_soup, facilities = navigate_to_inst_list(session)
    if not inst_soup or not facilities:
        print("❌ ナビゲーション失敗")
        exit(1)

    all_data = {}
    for fac in facilities:
        print(f"\n[{fac['index']}] {fac['name']} の空き状況を取得中...")
        # セッション状態をリセットするため、毎回施設リストまで再ナビゲート
        fresh_inst_soup, _ = navigate_to_inst_list(session)
        schedule = get_facility_schedule(session, fresh_inst_soup or inst_soup, fac['index'], end_date)
        all_data[fac['name']] = schedule
        print(f"  ✅ {len(schedule)} 日分取得")

    if not any(all_data.values()):
        print("⚠ データが取得できませんでした")
        exit(1)

    docs_dir = os.path.join(os.path.dirname(__file__), "docs")
    os.makedirs(docs_dir, exist_ok=True)

    # JSON保存
    json_path = os.path.join(docs_dir, "rsv.json")
    json_data = build_reservation_json(all_data, today, end_date)
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    print(f"JSON 保存完了: {json_path}")

    output_path = os.path.join(docs_dir, "rsv.html")
    write_app_entry(output_path, "rsv.json")

    print(f"\n{'=' * 60}\n完了！")
