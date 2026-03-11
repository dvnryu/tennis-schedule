import os
import re
import time
import warnings
from datetime import datetime, date, timedelta, timezone

import requests
from bs4 import BeautifulSoup

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
    code = (code
            .replace('０','0').replace('１','1').replace('２','2').replace('３','3')
            .replace('４','4').replace('５','5').replace('６','6').replace('７','7')
            .replace('８','8').replace('９','9'))
    if len(code) == 4 and code.isdigit():
        return f"{code[:2]}:{code[2:]}"
    return code


def date_sort_key(d):
    """'3月10日火曜日' → (3, 10)"""
    m = re.match(r'(\d+)月(\d+)日', d)
    return (int(m.group(1)), int(m.group(2))) if m else (99, 99)


def short_date(d):
    """'3月10日火曜日' → '3/10(火)'"""
    m = re.match(r'(\d+)月(\d+)日(.)', d)
    return f"{m.group(1)}/{m.group(2)}({m.group(3)})" if m else d[:7]


def actual_date_from_label(d, today):
    m = re.match(r'(\d+)月(\d+)日', d)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    year = today.year + (1 if month < today.month else 0)
    return date(year, month, day)


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


def write_rsv_html(all_data, output_path):
    today = now_jst()
    now = today.strftime('%Y-%m-%d %H:%M')
    next_month = today.month + 1 if today.month < 12 else 1

    # 全時間帯・日付
    all_times_set = set()
    all_dates_set = set()
    for sch in all_data.values():
        for date_str, times in sch.items():
            all_dates_set.add(date_str)
            all_times_set.update(times.keys())

    if not all_dates_set:
        print("⚠ データが空です")
        return

    # 全コート・全日付で一度も実データが無い時間帯（時間外・休館のみ）を除去
    def has_real_data(ts):
        for sch in all_data.values():
            for times in sch.values():
                if cell_class(times.get(ts, '-')) not in ('unavailable', 'closed', 'other'):
                    return True
        return False

    time_slots_raw = [ts for ts in sorted(all_times_set, key=lambda t: format_time(t))
                      if has_real_data(ts)]
    fmt_slots = [format_time(ts) for ts in time_slots_raw]
    all_dates = sorted(all_dates_set, key=date_sort_key)
    all_date_meta = {d: date_meta(d, today.date()) for d in all_dates}
    slots_json = '[' + ','.join(f'"{s}"' for s in fmt_slots) + ']'

    # ── サマリー: 全コートで最も良い状態を表示 ──
    priority = {'open':0,'general':1,'cancel':2,'full':3,'rain':4,'closed':5,'unavailable':6,'other':7}
    summary = {d: {ft: None for ft in fmt_slots} for d in all_dates}
    for sch in all_data.values():
        for date_str, times in sch.items():
            for ts, val in times.items():
                ft = format_time(ts)
                if ft not in fmt_slots:
                    continue
                cc = cell_class(val)
                cur = summary[date_str][ft]
                p = priority.get(cc, 7)
                if cur is None or p < cur[0]:
                    summary[date_str][ft] = (p, val, cc)

    sum_hdr = '<tr><th class="col-time">時間</th>'
    for d in all_dates:
        meta = all_date_meta[d]
        cls = 'col-special' if meta['day_group'] == 'special' else 'col-wd'
        title = f' title="{meta["holiday_name"]}"' if meta['is_holiday'] else ''
        sum_hdr += (
            f'<th class="{cls}" data-date-key="{meta["key"]}" '
            f'data-month-group="{meta["month_group"]}" data-day-group="{meta["day_group"]}"{title}>'
            f'{date_label(d, meta)}</th>'
        )
    sum_hdr += '</tr>'

    sum_body = ''
    for ts in time_slots_raw:
        ft = format_time(ts)
        sum_body += f'<tr class="row-time" data-time="{ft}"><td class="col-time">{ft}</td>'
        for d in all_dates:
            meta = all_date_meta[d]
            entry = summary[d][ft]
            if entry is None:
                val, cc = '-', 'unavailable'
            else:
                _, val, cc = entry
            day_cls = 'col-special' if meta['day_group'] == 'special' else 'col-wd'
            disp = cell_display(val, cc)
            sum_body += (
                f'<td class="{day_cls} lv-{cc}" data-level="{cc}" data-date-key="{meta["key"]}" '
                f'data-month-group="{meta["month_group"]}" data-day-group="{meta["day_group"]}">{disp}</td>'
            )
        sum_body += '</tr>'

    sum_html = (f'<div class="tbl-wrap"><table>'
                f'<thead>{sum_hdr}</thead><tbody>{sum_body}</tbody>'
                f'</table></div>')

    # ── 場別ビュー ──
    fac_html = ''
    for fac_name, sch in all_data.items():
        if not sch:
            continue
        dates = sorted(sch.keys(), key=date_sort_key)
        hdr = '<tr><th class="col-time">時間</th>'
        for d in dates:
            meta = all_date_meta[d]
            cls = 'col-special' if meta['day_group'] == 'special' else 'col-wd'
            title = f' title="{meta["holiday_name"]}"' if meta['is_holiday'] else ''
            hdr += (
                f'<th class="{cls}" data-date-key="{meta["key"]}" '
                f'data-month-group="{meta["month_group"]}" data-day-group="{meta["day_group"]}"{title}>'
                f'{date_label(d, meta)}</th>'
            )
        hdr += '</tr>'

        body = ''
        for ts in time_slots_raw:
            ft = format_time(ts)
            body += f'<tr class="row-time" data-time="{ft}"><td class="col-time">{ft}</td>'
            for d in dates:
                meta = all_date_meta[d]
                val = sch.get(d, {}).get(ts, '-')
                cc = cell_class(val)
                day_cls = 'col-special' if meta['day_group'] == 'special' else 'col-wd'
                disp = cell_display(val, cc)
                body += (
                    f'<td class="{day_cls} lv-{cc}" data-level="{cc}" data-date-key="{meta["key"]}" '
                    f'data-month-group="{meta["month_group"]}" data-day-group="{meta["day_group"]}">{disp}</td>'
                )
            body += '</tr>'

        fac_html += (f'<div class="facility">'
                     f'<div class="fac-hd" onclick="toggleBody(this)">'
                     f'{fac_name}<span class="arrow">▼</span></div>'
                     f'<div class="fac-bd open"><div class="tbl-wrap"><table>'
                     f'<thead>{hdr}</thead><tbody>{body}</tbody>'
                     f'</table></div></div></div>\n')

    # ── 日付別ビュー ──
    import unicodedata
    fac_names = list(all_data.keys())

    def short_fac(name):
        n = unicodedata.normalize('NFKC', name)
        m = re.search(r'(\d+)$', n)
        return f'場{m.group(1)}' if m else name[-2:]

    short_names = [short_fac(n) for n in fac_names]

    date_html = ''
    for d in all_dates:
        meta = all_date_meta[d]
        day_cls = 'special-date' if meta['day_group'] == 'special' else ''
        hdr = '<tr><th class="col-time">時間</th>'
        for fn in short_names:
            hdr += f'<th class="col-fac">{fn}</th>'
        hdr += '</tr>'

        body = ''
        for ts in time_slots_raw:
            ft = format_time(ts)
            body += f'<tr class="row-time" data-time="{ft}"><td class="col-time">{ft}</td>'
            for fac_name in fac_names:
                val = all_data[fac_name].get(d, {}).get(ts, '-')
                cc = cell_class(val)
                disp = cell_display(val, cc)
                body += f'<td class="lv-{cc}" data-level="{cc}">{disp}</td>'
            body += '</tr>'

        title = f' title="{meta["holiday_name"]}"' if meta['is_holiday'] else ''
        date_html += (f'<div class="facility {day_cls}" data-date-key="{meta["key"]}" '
                      f'data-month-group="{meta["month_group"]}" data-day-group="{meta["day_group"]}">'
                      f'<div class="fac-hd" onclick="toggleBody(this)">'
                      f'<span{title}>{date_label(d, meta)}</span><span class="arrow">▼</span></div>'
                      f'<div class="fac-bd open"><div class="tbl-wrap"><table>'
                      f'<thead>{hdr}</thead><tbody>{body}</tbody>'
                      f'</table></div></div></div>\n')

    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>富士見テニスコート 予約空き状況</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#0a0f1e;--card:#111827;--border:#1e293b;--text:#e2e8f0;--muted:#64748b;
  --open-bg:#064e3b;--open-fg:#6ee7b7;
  --full-bg:#450a0a;--full-fg:#fca5a5;
  --closed-bg:#292524;--closed-fg:#a8a29e;
  --general-bg:#0c2340;--general-fg:#7dd3fc;
  --rain-bg:#1e293b;--rain-fg:#94a3b8;
  --cancel-bg:#451a03;--cancel-fg:#fde68a;
  --na-bg:#0d1424;--na-fg:#475569;
  --we-tint:rgba(139,92,246,.06);
}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
.sticky-nav{{position:sticky;top:0;z-index:200}}
.site-header{{background:linear-gradient(135deg,#0f172a,#1e1b4b);border-bottom:1px solid var(--border);padding:16px 20px 12px}}
.site-title{{font-size:1.25em;font-weight:700;background:linear-gradient(135deg,#22d3ee,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.site-meta{{font-size:.74em;color:var(--muted);margin-top:3px}}
.toolbar{{background:#0d1424;border-bottom:1px solid var(--border)}}
.tabs{{display:flex;gap:2px;padding:10px 20px 0}}
.tab{{padding:7px 18px;border-radius:8px 8px 0 0;border:1px solid transparent;background:transparent;color:var(--muted);cursor:pointer;font-size:.85em;font-weight:500;transition:all .15s}}
.tab:hover{{color:var(--text)}}
.tab.on{{background:var(--card);border-color:var(--border);border-bottom-color:var(--card);color:var(--text)}}
.filters{{display:flex;flex-wrap:wrap;gap:8px 18px;padding:10px 20px;align-items:center}}
.fg{{display:flex;flex-wrap:wrap;gap:5px;align-items:center}}
.flabel{{font-size:.72em;color:var(--muted);white-space:nowrap;margin-right:2px}}
.btn{{padding:3px 11px;border-radius:20px;border:1px solid #2d3e58;background:transparent;color:var(--muted);cursor:pointer;font-size:.78em;white-space:nowrap;transition:all .15s}}
.btn:hover{{border-color:#475569;color:var(--text)}}
.btn.on{{border-color:transparent;color:#fff}}
.btn.lv-open.on{{background:#065f46;border-color:#059669;color:#6ee7b7}}
.btn.lv-full.on{{background:#450a0a;border-color:#b91c1c;color:#fca5a5}}
.btn.lv-general.on{{background:#0c2340;border-color:#0284c7;color:#7dd3fc}}
.btn.day.on,.btn.expand-btn.on{{background:#1e3a5f;border-color:#0284c7;color:#7dd3fc}}
.btn.time-btn.on{{background:#1e293b;border-color:#334155;color:var(--text)}}
.btn.reset{{border-color:#334155}}
.main{{padding:12px 20px;display:flex;flex-direction:column;gap:10px}}
.view{{display:none}}.view.on{{display:flex;flex-direction:column;gap:10px}}
.sum-legend{{font-size:.74em;color:var(--muted);padding:6px 2px}}
.facility{{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}}
.fac-hd{{padding:10px 16px;font-size:.93em;font-weight:600;cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;background:linear-gradient(90deg,#1e3a5f18,transparent);transition:background .15s}}
.fac-hd:hover{{background:linear-gradient(90deg,#1e3a5f44,transparent)}}
.arrow{{font-size:.75em;color:var(--muted);transition:transform .2s}}
.fac-hd.open .arrow{{transform:rotate(180deg)}}
.fac-bd{{display:none}}.fac-bd.open{{display:block}}
.tbl-wrap{{overflow-x:auto;padding-bottom:4px}}
table{{border-collapse:collapse;font-size:.82em;white-space:nowrap}}
thead th{{padding:6px 9px;text-align:center;background:#141e30;border-bottom:2px solid var(--border);font-weight:600;position:sticky;top:0;z-index:10}}
thead th.col-special{{background:#1a1232}}
thead th.col-time{{position:sticky;left:0;z-index:20;background:#0f1829}}
tbody tr:hover td{{filter:brightness(1.18)}}
tbody td{{padding:5px 9px;text-align:center;border-top:1px solid #161f30}}
tbody td.col-time{{background:#0f1829;font-weight:600;text-align:center;position:sticky;left:0;z-index:5;border-right:1px solid var(--border)}}
tbody td.col-special{{background:var(--we-tint)}}
td.lv-open{{background:var(--open-bg);color:var(--open-fg);font-weight:700}}
td.lv-full{{background:var(--full-bg);color:var(--full-fg)}}
td.lv-general{{background:var(--general-bg);color:var(--general-fg)}}
td.lv-rain{{background:var(--rain-bg);color:var(--rain-fg)}}
td.lv-cancel{{background:var(--cancel-bg);color:var(--cancel-fg)}}
td.lv-closed{{background:var(--closed-bg);color:var(--closed-fg)}}
td.lv-unavailable,td.lv-other{{background:var(--na-bg);color:var(--na-fg)}}
.special-date>.fac-hd{{background:linear-gradient(90deg,#2d1f4e44,transparent)}}
@media(max-width:640px){{
  .site-header,.filters,.tabs,.main{{padding-left:10px;padding-right:10px}}
  table{{font-size:.74em}}tbody td{{padding:4px 5px}}
}}
</style>
</head>
<body>
<div class="sticky-nav">
<header class="site-header">
  <a href="index.html" style="display:inline-block;font-size:.75em;color:#64748b;text-decoration:none;margin-bottom:8px;" onmouseover="this.style.color='#94a3b8'" onmouseout="this.style.color='#64748b'">← ホームへ</a>
  <div class="site-title">🎾 富士見テニスコート 予約空き状況</div>
  <div class="site-meta">更新: {now} &nbsp;·&nbsp; {today.month}月・{next_month}月 &nbsp;·&nbsp; ○=空き ×=予約あり</div>
</header>
<div class="toolbar">
  <div class="tabs">
    <button class="tab on" onclick="setView('summary',this)">サマリー</button>
    <button class="tab" onclick="setView('facility',this)">場別</button>
    <button class="tab" onclick="setView('date',this)">日付別</button>
  </div>
  <div class="filters">
    <div class="fg">
      <span class="flabel">絞込み</span>
      <button class="btn open-only" id="open-only-btn" onclick="toggleOpenOnly(this)">○ 空き</button>
    </div>
    <div class="fg">
      <span class="flabel">月</span>
      <button class="btn month on" data-month="all" onclick="setMonth(this)">全部</button>
      <button class="btn month" data-month="current" onclick="setMonth(this)">本月</button>
      <button class="btn month" data-month="next" onclick="setMonth(this)">下一月</button>
    </div>
    <div class="fg">
      <span class="flabel">曜日</span>
      <button class="btn day on" data-day="all" onclick="setDay(this)">全部</button>
      <button class="btn day" data-day="weekday" onclick="setDay(this)">平日</button>
      <button class="btn day" data-day="special" onclick="setDay(this)">周末+休日</button>
    </div>
    <div class="fg" id="time-fg">
      <span class="flabel">時間</span>
    </div>
    <div class="fg" id="expand-fg" style="display:none">
      <button class="btn expand-btn on" id="expand-btn" onclick="toggleAll()">全折畳</button>
    </div>
    <button class="btn reset" onclick="resetAll()">リセット</button>
  </div>
</div>
</div>

<div class="main">
  <div class="view on" id="view-summary">
    <div class="sum-legend">12面の中で最も空きがある枠を表示 &nbsp;·&nbsp; 緑=空き 赤=全面予約 灰=受付外</div>
    <div class="facility">{sum_html}</div>
  </div>
  <div class="view" id="view-facility">
{fac_html}  </div>
  <div class="view" id="view-date">
{date_html}  </div>
</div>

<script>
const SLOTS = {slots_json};
let activeTimes = new Set(SLOTS);
let openOnly = false;
let monthFilter = 'all';
let dayFilter = 'all';
let allExpanded = true;
let currentView = 'summary';

const tfg = document.getElementById('time-fg');
SLOTS.forEach(t => {{
  const b = document.createElement('button');
  b.className = 'btn time-btn on';
  b.textContent = t;
  b.dataset.time = t;
  b.onclick = () => {{
    b.classList.toggle('on');
    if (activeTimes.has(t)) activeTimes.delete(t); else activeTimes.add(t);
    applyFilters();
  }};
  tfg.appendChild(b);
}});

document.querySelectorAll('td[data-level]').forEach(td => {{
  td.dataset.rawText = td.textContent;
}});

function matchesDateFilters(el) {{
  if (monthFilter !== 'all' && el.dataset.monthGroup !== monthFilter) return false;
  if (dayFilter !== 'all' && el.dataset.dayGroup !== dayFilter) return false;
  return true;
}}

function applyTableFilters(table) {{
  if (!table) return false;

  const headers = Array.from(table.querySelectorAll('thead th[data-date-key]'));
  const rows = Array.from(table.querySelectorAll('tbody tr.row-time'));
  const visibleCols = {{}};

  headers.forEach(th => {{
    const key = th.dataset.dateKey;
    const dateOk = matchesDateFilters(th);
    if (!dateOk) {{
      visibleCols[key] = false;
      return;
    }}
    if (!openOnly) {{
      visibleCols[key] = true;
      return;
    }}
    visibleCols[key] = rows.some(tr =>
      activeTimes.has(tr.dataset.time) &&
      Array.from(tr.querySelectorAll(`td[data-date-key="${{key}}"]`)).some(td => td.dataset.level === 'open')
    );
  }});

  headers.forEach(th => {{
    th.style.display = visibleCols[th.dataset.dateKey] ? '' : 'none';
  }});

  let tableHasVisibleRow = false;
  rows.forEach(tr => {{
    const timeOk = activeTimes.has(tr.dataset.time);
    let rowHasVisibleCell = false;

    tr.querySelectorAll('td[data-date-key]').forEach(td => {{
      const colVisible = !!visibleCols[td.dataset.dateKey];
      const showText = !openOnly || td.dataset.level === 'open';
      td.style.display = colVisible ? '' : 'none';
      td.textContent = showText ? td.dataset.rawText : '';
      if (colVisible && showText) rowHasVisibleCell = true;
    }});

    tr.style.display = timeOk && rowHasVisibleCell ? '' : 'none';
    if (tr.style.display !== 'none') tableHasVisibleRow = true;
  }});

  return tableHasVisibleRow;
}}

function applyDateCards() {{
  document.querySelectorAll('#view-date .facility').forEach(card => {{
    const dateOk = matchesDateFilters(card);
    if (!dateOk) {{
      card.style.display = 'none';
      return;
    }}

    const table = card.querySelector('table');
    const facHeaders = Array.from(table.querySelectorAll('thead th.col-fac'));
    const rows = Array.from(card.querySelectorAll('tbody tr.row-time'));
    const visibleCols = facHeaders.map((_, idx) =>
      rows.some(tr => activeTimes.has(tr.dataset.time) && tr.querySelectorAll('td[data-level]')[idx]?.dataset.level === 'open')
    );

    facHeaders.forEach((th, idx) => {{
      th.style.display = !openOnly || visibleCols[idx] ? '' : 'none';
    }});

    let cardHasVisibleRow = false;
    rows.forEach(tr => {{
      const timeOk = activeTimes.has(tr.dataset.time);
      let rowHasVisibleCell = false;
      tr.querySelectorAll('td[data-level]').forEach((td, idx) => {{
        const colVisible = !openOnly || visibleCols[idx];
        const showText = !openOnly || td.dataset.level === 'open';
        td.style.display = colVisible ? '' : 'none';
        td.textContent = showText ? td.dataset.rawText : '';
        if (colVisible && showText) rowHasVisibleCell = true;
      }});
      tr.style.display = timeOk && rowHasVisibleCell ? '' : 'none';
      if (tr.style.display !== 'none') cardHasVisibleRow = true;
    }});

    card.style.display = cardHasVisibleRow ? '' : 'none';
  }});
}}

function applyFilters() {{
  applyTableFilters(document.querySelector('#view-summary table'));

  document.querySelectorAll('#view-facility .facility').forEach(card => {{
    const visible = applyTableFilters(card.querySelector('table'));
    card.style.display = visible ? '' : 'none';
  }});

  applyDateCards();
}}

function toggleOpenOnly(btn) {{
  openOnly = !openOnly;
  btn.classList.toggle('on', openOnly);
  applyFilters();
}}

function setMonth(btn) {{
  document.querySelectorAll('.btn.month').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  monthFilter = btn.dataset.month;
  applyFilters();
}}

function setDay(btn) {{
  document.querySelectorAll('.btn.day').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  dayFilter = btn.dataset.day;
  applyFilters();
}}
function setView(view, btn) {{
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  document.getElementById('view-summary').classList.toggle('on', view === 'summary');
  document.getElementById('view-facility').classList.toggle('on', view === 'facility');
  document.getElementById('view-date').classList.toggle('on', view === 'date');
  document.getElementById('expand-fg').style.display =
    (view === 'facility' || view === 'date') ? '' : 'none';
  currentView = view;
}}
function toggleBody(hd) {{
  hd.classList.toggle('open');
  hd.nextElementSibling.classList.toggle('open');
}}
function toggleAll() {{
  allExpanded = !allExpanded;
  const btn = document.getElementById('expand-btn');
  btn.textContent = allExpanded ? '全折畳' : '全展開';
  btn.classList.toggle('on', allExpanded);
  const sel = currentView === 'date' ? '#view-date .fac-hd' : '#view-facility .fac-hd';
  document.querySelectorAll(sel).forEach(hd => {{
    hd.classList.toggle('open', allExpanded);
    hd.nextElementSibling.classList.toggle('open', allExpanded);
  }});
}}
function resetAll() {{
  openOnly = false;
  monthFilter = 'all';
  dayFilter = 'all';
  activeTimes = new Set(SLOTS);
  document.getElementById('open-only-btn').classList.remove('on');
  document.querySelectorAll('.btn.month').forEach(b => b.classList.remove('on'));
  document.querySelector('.btn.month[data-month="all"]').classList.add('on');
  document.querySelectorAll('.btn.time-btn').forEach(b => b.classList.add('on'));
  document.querySelectorAll('.btn.day').forEach(b => b.classList.remove('on'));
  document.querySelector('.btn.day[data-day="all"]').classList.add('on');
  applyFilters();
}}

applyFilters();
</script>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML 保存完了: {output_path}")


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
    import json
    json_path = os.path.join(docs_dir, "rsv.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump({
            'updated': today.strftime('%Y-%m-%d %H:%M'),
            'data': all_data,
        }, f, ensure_ascii=False, indent=2)
    print(f"JSON 保存完了: {json_path}")

    output_path = os.path.join(docs_dir, "rsv.html")
    write_rsv_html(all_data, output_path)

    print(f"\n{'=' * 60}\n完了！")
