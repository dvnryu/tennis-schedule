import os
import requests
from bs4 import BeautifulSoup
import re
import warnings
from datetime import datetime, timedelta, timezone
import json

from lib.schema import (
    format_time_label,
    normalize_time_code,
    parse_japanese_date_label,
    short_date_label,
    short_facility_name,
)

warnings.filterwarnings('ignore')

BASE_URL = "https://www.fureai-net.city.kawasaki.jp/web"
JST = timezone(timedelta(hours=9), name="JST")


def now_jst():
    """返回日本时间"""
    return datetime.now(JST)


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())

_load_env()
USER_ID = os.environ.get("FUREAI_USER_ID", "")
PASSWORD = os.environ.get("FUREAI_PASSWORD", "")


def create_session():
    """创建带 UA 的 session"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) '
                       'Chrome/110.0.0.0 Safari/537.36',
    })
    return session


def parse(response):
    """解析 shift_jis 编码的 HTML"""
    return BeautifulSoup(response.content, 'html.parser', from_encoding='shift_jis')


def get_form_data(soup):
    """提取 form1 的所有 hidden input（模拟 doAction 提交）"""
    form = soup.find('form', {'name': 'form1'})
    data = {}
    if form:
        for inp in form.find_all('input', {'type': 'hidden'}):
            name = inp.get('name')
            if name:
                data[name] = inp.get('value', '')
    return data


def post_step(session, soup, action, extra_data=None):
    """模拟 doAction(document.form1, action) —— 提交 form1 到指定 URL"""
    data = get_form_data(soup)
    if extra_data:
        data.update(extra_data)
    r = session.post(f"{BASE_URL}/{action}", data=data, timeout=10, verify=False)
    return parse(r)


def get_title(soup):
    """获取页面标题（去掉公共前缀）"""
    title = soup.find('title')
    if title:
        text = title.text
        if 'ふれあいネット' in text:
            return text.split('ふれあいネット')[-1].strip()
        return text
    return 'N/A'


def login(session):
    """Step 1: 登录"""
    r = session.get(f"{BASE_URL}/index.jsp", timeout=10, verify=False)
    soup = parse(r)
    login_key = soup.find('input', {'name': 'loginJKey'})['value']

    r_login = session.post(f"{BASE_URL}/rsvWUserAttestationAction.do", data={
        'userId': USER_ID,
        'password': PASSWORD,
        'securityNo': '',
        'displayNo': 'pawab2000',
        'loginJKey': login_key,
    }, timeout=10, verify=False)

    soup = parse(r_login)
    title = get_title(soup)
    if '登録メニュー' in title:
        print("Step 1 ✅ 登录成功")
        return soup
    else:
        print(f"Step 1 ❌ 登录失败: {title}")
        return None


def navigate_to_facility_list(session, menu_soup):
    """
    Step 2~7: 从菜单一步步导航到场地列表页
    菜单 → 抽選の申込み → 利用目的から → 屋外スポーツ → テニス（屋外）→ テニスコート申込み → 富士見テニスコート
    """
    # Step 2: 抽選の申込み
    soup = post_step(session, menu_soup, 'lotWTransLotMenuAction.do')
    print(f"Step 2 ✅ {get_title(soup)}")

    # Step 3: 利用目的から
    soup = post_step(session, soup, 'lotWTransLotPpsdAction.do')
    print(f"Step 3 ✅ {get_title(soup)}")

    # Step 4: 屋外スポーツ — sendPpsdCd(document.form1, action, '100')
    soup = post_step(session, soup, 'lotWTransLotPpsAction.do', {'selectPpsdCd': '100'})
    print(f"Step 4 ✅ {get_title(soup)}")

    # Step 5: テニス（屋外）— sendPlwac3000(form1, action, ppsdCd, ppsCd)
    html = str(soup)
    tennis_matches = re.findall(
        r"sendPlwac3000\(document\.form1,\s*\w+,\s*'(\d+)',\s*'(\d+)'\)\">([^<]*テニス[^<]*)<", html
    )
    if not tennis_matches:
        print("Step 5 ❌ 没找到テニス选项")
        all_options = re.findall(
            r"sendPlwac3000\(document\.form1,\s*\w+,\s*'(\d+)',\s*'(\d+)'\)\">([^<]+)<", html
        )
        print("  可用选项:", all_options)
        return None

    ppsd_cd, pps_cd, tennis_name = tennis_matches[0]
    soup = post_step(session, soup, 'lotWTransLotAcceptListAction.do', {
        'selectPpsPpsdCd': ppsd_cd,
        'selectPpsCd': pps_cd,
    })
    print(f"Step 5 ✅ {get_title(soup)} (选择了: {tennis_name})")

    # Step 6: テニスコート 申込み — sendWTransLotBldGrpAction(form1, action, classCd)
    html = str(soup)
    # 从 submit 按钮的 onclick 中提取 classCd 和对应的组名
    # 从表格行中提取 classCd 和组名
    rows = soup.find_all('tr')
    groups = []
    for row in rows:
        btn = row.find('input', onclick=re.compile(r'sendWTransLotBldGrpAction'))
        if btn:
            onclick = btn.get('onclick', '')
            cd = re.search(r"'([^']+)'", onclick)
            name_td = row.find('td')
            if cd and name_td:
                groups.append((cd.group(1), name_td.text.strip()))

    if not groups:
        if '受付済' in soup.get_text():
            print("Step 6 ℹ️ 本期抽签已受理（受付済），申请入口已关闭，无需更新")
            exit(0)
        if '確認中' in soup.get_text():
            print("Step 6 ℹ️ 本期抽签处于確認中，暂无申込み入口")
            return None
        print("Step 6 ❌ 没找到申込みボタン")
        print("  页面内容:", soup.get_text()[:300])
        return None

    print("  可选抽選グループ:")
    for code, name in groups:
        print(f"    [{code}] {name}")

    # 选第一个（テニスコート）
    class_cd = groups[0][0]
    group_name = groups[0][1]
    soup = post_step(session, soup, 'lotWTransLotBldGrpAction.do', {'selectClassCd': class_cd})
    print(f"Step 6 ✅ {get_title(soup)} (选择了: {group_name}, classCd: {class_cd})")

    # Step 7: 富士見テニスコート — sendBldGrpCd(document.form1, action, '5080010')
    html = str(soup)
    bld_matches = re.findall(
        r"sendBldGrpCd\(document\.form1,\s*\w+,\s*'([^']+)'\)\">([^<]+)<", html
    )
    if not bld_matches:
        print("Step 7 ❌ 没找到 sendBldGrpCd")
        print("  页面内容:", soup.get_text()[:300])
        return None

    print(f"  可选建筑组:")
    for code, name in bld_matches:
        print(f"    [{code}] {name}")

    # 优先选择富士見テニスコート，找不到则选第一个
    bld_dict = {name: code for code, name in bld_matches}
    if '富士見テニスコート' in bld_dict:
        bld_code = bld_dict['富士見テニスコート']
        bld_name = '富士見テニスコート'
    else:
        bld_code = bld_matches[0][0]
        bld_name = bld_matches[0][1]
    soup = post_step(session, soup, 'lotWTransLotInstGrpAction.do', {
        'displayNo': 'plwba1000',
        'selectBldGrpCd': bld_code,
    })
    print(f"Step 7 ✅ {get_title(soup)} (选择了: {bld_name})")

    return soup


def parse_facility_list(soup):
    """解析场地列表页，提取 sendInstGrCd 链接"""
    html = str(soup)
    matches = re.findall(
        r"sendInstGrCd\(document\.form1,\s*[^,]+,\s*'([^']+)',\s*(\d+),\s*'([^']+)'\)\">([^<]+)<",
        html
    )
    facilities = []
    for inst_grp_cd, use_number, bld_cd, name in matches:
        facilities.append({
            'name': name.strip(),
            'instGrpCd': inst_grp_cd,
            'useNumber': use_number,
            'bldCd': bld_cd,
        })
    return facilities


def parse_schedule_page(soup):
    """解析一页时间表，返回 {日期: {时段: 申请数据}}"""
    table = soup.find('table', {'class': 'lotakitable'})
    if not table:
        return {}, None

    rows = table.find_all('tr')
    if len(rows) < 2:
        return {}, None

    # 表头日期
    header = rows[0]
    dates = [th.text.strip() for th in header.find_all('th')[1:]]

    schedule = {}
    for date in dates:
        schedule[date] = {}

    for row in rows[1:]:
        time_th = row.find('th')
        if not time_th:
            continue
        time_slot = time_th.text.strip()
        cells = row.find_all('td')
        for i, cell in enumerate(cells):
            if i >= len(dates):
                break
            link = cell.find('a')
            if link:
                schedule[dates[i]][time_slot] = link.text.strip()
            else:
                schedule[dates[i]][time_slot] = cell.text.strip() or '-'

    # 找"次の週"按钮的日期参数（用于翻页）
    next_date = None
    for btn in soup.find_all('input', value='次の週'):
        onclick = btn.get('onclick', '')
        m = re.search(r'movePage\(document\.form1,\s*\w+,\s*(\d+)\)', onclick)
        if m:
            next_date = m.group(1)
            break

    return schedule, next_date


def get_full_month_schedule(session, facility_soup, facility, start_ymd=None):
    if start_ymd is None:
        today = now_jst()
        # 下个月月初
        if today.month == 12:
            start_ymd = f'{today.year + 1}0101'
        else:
            start_ymd = f'{today.year}{today.month + 1:02d}01'
    """获取一个场地整个月的时间表（自动翻页）"""
    # 第一周 — 强制 srchStartYMD 回到月初
    current_soup = post_step(session, facility_soup, 'lotWTransLotInstSrchVacantAction.do', {
        'selectInstGrpCd': facility['instGrpCd'],
        'selectUseNumber': facility['useNumber'],
        'selectBldCd': facility['bldCd'],
        'srchStartYMD': start_ymd,
    })

    all_schedule = {}
    weeks_fetched = 0

    while weeks_fetched < 5:  # 最多5周，覆盖一个月
        week_data, next_date = parse_schedule_page(current_soup)
        if not week_data:
            break

        all_schedule.update(week_data)
        weeks_fetched += 1

        if not next_date:
            break

        # 翻到下一周 — movePage 设置 srchStartYMD 字段
        current_soup = post_step(session, current_soup, 'lotWTransLotInstSrchVacantAction.do', {
            'srchStartYMD': next_date,
        })

    return all_schedule, current_soup


def format_time(code):
    """0900 → 09:00"""
    return format_time_label(code)


def date_sort_key(d):
    """日期排序 key: '3月1日日曜日' → (3, 1)"""
    m = re.match(r'(\d+)月(\d+)日', d)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (99, 99)


def short_date(d):
    """'3月1日日曜日' → '3/1(日)'"""
    return short_date_label(d)


def is_weekend(d):
    """判断是否周末"""
    return '土曜日' in d or '日曜日' in d


def cell_class(val):
    """根据申请数返回 CSS class"""
    if val == '-':
        return 'unavailable'
    m = re.match(r'(\d+)/(\d+)', val)
    if not m:
        return ''
    applicants = int(m.group(2))
    if applicants == 0:
        return 'hot'       # 0 人申请，必中
    if applicants <= 3:
        return 'easy'      # 容易中签
    if applicants <= 10:
        return 'medium'    # 一般
    return 'hard'           # 竞争激烈


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
    print(f"HTML 报告已保存: {output_path}")


def build_lottery_json(all_data, today):
    all_dates = sorted(
        {date_str for schedule in all_data.values() for date_str in schedule.keys()},
        key=date_sort_key,
    )
    all_times = sorted(
        {time_code for schedule in all_data.values() for times in schedule.values() for time_code in times.keys()},
        key=lambda value: format_time(value),
    )

    date_items = []
    iso_dates = []
    for date_str in all_dates:
        actual = parse_japanese_date_label(date_str, today.date())
        iso_dates.append(actual.isoformat() if actual else None)
        date_items.append({
            'key': actual.isoformat() if actual else date_str,
            'source_label': date_str,
            'label': short_date(date_str),
            'short_label': short_date(date_str),
            'iso_date': actual.isoformat() if actual else None,
            'month_group': 'next',
            'day_group': 'special' if is_weekend(date_str) else 'weekday',
            'is_holiday': False,
            'holiday_name': '',
        })

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
        for date_str, date_key in zip(all_dates, iso_dates):
            if date_key is None:
                continue
            time_cells = {}
            for time_code in all_times:
                raw_value = schedule.get(date_str, {}).get(time_code, '-')
                status = cell_class(raw_value) or 'other'
                text = f'★{raw_value}' if status == 'hot' else raw_value
                time_cells[normalize_time_code(time_code)] = {
                    'status': status,
                    'text': text,
                    'raw': raw_value,
                }
            facility_cells[date_key] = time_cells
        cells[facility_key] = facility_cells

    start_candidates = [item['iso_date'] for item in date_items if item['iso_date']]
    return {
        'version': 1,
        'mode': 'lottery',
        'generated_at': today.isoformat(timespec='minutes'),
        'timezone': 'Asia/Tokyo',
        'page': {
            'title': '富士見テニスコート 抽選申込状況',
            'legend': '12面の中で一番申請が少ないコートの数値を表示',
            'facility_group': '富士見テニスコート',
        },
        'range': {
            'start': min(start_candidates) if start_candidates else None,
            'end': max(start_candidates) if start_candidates else None,
        },
        'filters': {
            'month': False,
            'day_type': True,
            'time': True,
            'open_only': False,
            'status_buttons': ['hot', 'easy', 'medium', 'hard'],
        },
        'dates': date_items,
        'time_slots': [{
            'key': normalize_time_code(time_code),
            'source_label': time_code,
            'label': format_time(time_code),
        } for time_code in all_times],
        'facilities': facilities,
        'cells': cells,
    }


def build_empty_lottery_json(today):
    return {
        'version': 1,
        'mode': 'lottery',
        'generated_at': today.isoformat(timespec='minutes'),
        'timezone': 'Asia/Tokyo',
        'page': {
            'title': '富士見テニスコート 抽選申込状況',
            'legend': '現在は抽選申込み対象外、または確認中です。',
            'facility_group': '富士見テニスコート',
        },
        'range': {
            'start': None,
            'end': None,
        },
        'filters': {
            'month': False,
            'day_type': True,
            'time': True,
            'open_only': False,
            'status_buttons': ['hot', 'easy', 'medium', 'hard'],
        },
        'dates': [],
        'time_slots': [],
        'facilities': [],
        'cells': {},
    }


if __name__ == "__main__":
    print("=" * 60)
    print("川崎市网球场预约系统 - 富士見テニスコート 全月抽选查询")
    print("=" * 60)

    session = create_session()

    # Step 1: 登录
    menu_soup = login(session)
    if not menu_soup:
        exit(1)

    # Step 2~7: 导航到场地列表
    print()
    facility_soup = navigate_to_facility_list(session, menu_soup)
    docs_dir = os.path.join(os.path.dirname(__file__), "docs")
    os.makedirs(docs_dir, exist_ok=True)
    if not facility_soup:
        json_path = os.path.join(docs_dir, "lottery.json")
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(build_empty_lottery_json(now_jst()), f, ensure_ascii=False, indent=2)
        print(f"JSON 报告已保存: {json_path}")
        output_path = os.path.join(docs_dir, "lot.html")
        write_app_entry(output_path, "lottery.json")
        print("\n抽选当前无可更新数据，已写出空页面数据。")
        exit(0)

    # 解析场地列表
    facilities = parse_facility_list(facility_soup)
    print(f"\n找到 {len(facilities)} 个场地")

    # 获取所有场地的整月时间表
    all_data = {}
    last_soup = facility_soup

    for i, facility in enumerate(facilities):
        print(f"\n[{i+1}/{len(facilities)}] 获取 {facility['name']} 的时间表...")

        if i > 0:
            last_soup = facility_soup

        schedule, last_soup = get_full_month_schedule(session, last_soup, facility)
        all_data[facility['name']] = schedule
        print(f"  ✅ 获取了 {len(schedule)} 天的数据")

    json_path = os.path.join(docs_dir, "lottery.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(build_lottery_json(all_data, now_jst()), f, ensure_ascii=False, indent=2)
    print(f"JSON 报告已保存: {json_path}")

    # 输出 HTML
    output_path = os.path.join(docs_dir, "lot.html")
    write_app_entry(output_path, "lottery.json")

    print(f"\n{'=' * 60}")
    print("完成！")
