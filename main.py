import os
import requests
from bs4 import BeautifulSoup
import re
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_URL = "https://www.fureai-net.city.kawasaki.jp/web"
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
        today = datetime.now()
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
    code = code.replace('０', '0').replace('１', '1').replace('２', '2').replace('３', '3') \
               .replace('４', '4').replace('５', '5').replace('６', '6').replace('７', '7') \
               .replace('８', '8').replace('９', '9')
    if len(code) == 4 and code.isdigit():
        return f"{code[:2]}:{code[2:]}"
    return code


def date_sort_key(d):
    """日期排序 key: '3月1日日曜日' → (3, 1)"""
    m = re.match(r'(\d+)月(\d+)日', d)
    if m:
        return (int(m.group(1)), int(m.group(2)))
    return (99, 99)


def short_date(d):
    """'3月1日日曜日' → '3/1(日)'"""
    m = re.match(r'(\d+)月(\d+)日(.)', d)
    if m:
        return f"{m.group(1)}/{m.group(2)}({m.group(3)})"
    return d[:6]


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


def write_html(all_data, output_path):
    """生成 HTML 报告"""
    today = datetime.now()
    next_month = today.month + 1 if today.month < 12 else 1
    now = today.strftime('%Y-%m-%d %H:%M')

    # 收集所有时段和日期
    all_times_set = set()
    all_dates_set = set()
    for schedule in all_data.values():
        for date, times in schedule.items():
            all_dates_set.add(date)
            all_times_set.update(times.keys())
    time_slots_raw = sorted(all_times_set, key=lambda t: format_time(t))
    fmt_slots = [format_time(ts) for ts in time_slots_raw]
    all_dates = sorted(all_dates_set, key=date_sort_key)
    slots_json = '[' + ','.join(f'"{s}"' for s in fmt_slots) + ']'

    # ── 汇总数据：{日期: {时段: 最优val}} ──
    # 对每个日期+时段，找申请人数最少（最容易中签）的那面球场的值
    summary = {d: {ft: None for ft in fmt_slots} for d in all_dates}
    for schedule in all_data.values():
        for date, times in schedule.items():
            for ts, val in times.items():
                ft = format_time(ts)
                cur = summary[date][ft]
                if val == '-':
                    continue
                m = re.match(r'\d+/(\d+)', val)
                if not m:
                    continue
                applicants = int(m.group(1))
                if cur is None:
                    summary[date][ft] = (applicants, val)
                elif applicants < cur[0]:
                    summary[date][ft] = (applicants, val)

    # 汇总表
    sum_hdr = '<tr><th class="col-time">時間</th>'
    for d in all_dates:
        cls = 'col-we' if is_weekend(d) else 'col-wd'
        sum_hdr += f'<th class="{cls}">{short_date(d)}</th>'
    sum_hdr += '</tr>'

    sum_body = ''
    for ts in time_slots_raw:
        ft = format_time(ts)
        sum_body += f'<tr class="row-time" data-time="{ft}"><td class="col-time">{ft}</td>'
        for d in all_dates:
            entry = summary[d][ft]
            if entry is None:
                val, lv = '-', 'unavailable'
            else:
                val = entry[1]
                lv = cell_class(val) or 'unavailable'
            display = f'★{val}' if lv == 'hot' else val
            we_cls = 'col-we' if is_weekend(d) else 'col-wd'
            sum_body += f'<td class="{we_cls} lv-{lv}" data-level="{lv}">{display}</td>'
        sum_body += '</tr>'

    sum_html = f'<div class="tbl-wrap"><table><thead>{sum_hdr}</thead><tbody>{sum_body}</tbody></table></div>'

    # ── 场地明细 ──
    fac_html = ''
    for fac_name, schedule in all_data.items():
        if not schedule:
            continue
        dates = sorted(schedule.keys(), key=date_sort_key)

        hdr = '<tr><th class="col-time">時間</th>'
        for d in dates:
            cls = 'col-we' if is_weekend(d) else 'col-wd'
            hdr += f'<th class="{cls}">{short_date(d)}</th>'
        hdr += '</tr>'

        body = ''
        for ts in time_slots_raw:
            ft = format_time(ts)
            body += f'<tr class="row-time" data-time="{ft}"><td class="col-time">{ft}</td>'
            for d in dates:
                val = schedule.get(d, {}).get(ts, '-')
                cc = cell_class(val)
                lv = cc if cc else 'other'
                we_cls = 'col-we' if is_weekend(d) else 'col-wd'
                display = f'★{val}' if cc == 'hot' else val
                body += f'<td class="{we_cls} lv-{lv}" data-level="{lv}">{display}</td>'
            body += '</tr>'

        fac_html += f'''<div class="facility">
<div class="fac-hd" onclick="toggleBody(this)">{fac_name}<span class="arrow">▼</span></div>
<div class="fac-bd open"><div class="tbl-wrap"><table><thead>{hdr}</thead><tbody>{body}</tbody></table></div></div>
</div>\n'''


    # ── 日付別视图 ──
    fac_names = list(all_data.keys())
    # 简短名：富士見テニス場１ → 場1
    import unicodedata
    def short_fac(name):
        n = unicodedata.normalize('NFKC', name)
        m = re.search(r'(\d+)$', n)
        return f'場{m.group(1)}' if m else name[-3:]

    short_names = [short_fac(n) for n in fac_names]

    date_html = ''
    for d in all_dates:
        we_cls = 'we-date' if is_weekend(d) else ''
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
                lv = cc if cc else 'other'
                display = f'★{val}' if cc == 'hot' else val
                body += f'<td class="lv-{lv}" data-level="{lv}">{display}</td>'
            body += '</tr>'

        date_html += f'''<div class="facility {we_cls}">
<div class="fac-hd" onclick="toggleBody(this)">{short_date(d)}<span class="arrow">▼</span></div>
<div class="fac-bd open"><div class="tbl-wrap"><table><thead>{hdr}</thead><tbody>{body}</tbody></table></div></div>
</div>\n'''

    html = f'''<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>富士見テニスコート 抽選申込状況</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
:root{{
  --bg:#0a0f1e;--card:#111827;--border:#1e293b;--text:#e2e8f0;--muted:#64748b;
  --hot-bg:#064e3b;--hot-fg:#6ee7b7;
  --easy-bg:#0c2340;--easy-fg:#7dd3fc;
  --med-bg:#451a03;--med-fg:#fde68a;
  --hard-bg:#450a0a;--hard-fg:#fca5a5;
  --na-bg:#0d1424;--na-fg:#2d3e58;
  --we-tint:rgba(139,92,246,.06);
}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}

/* Header */
.site-header{{background:linear-gradient(135deg,#0f172a,#1e1b4b);border-bottom:1px solid var(--border);padding:16px 20px 12px;position:sticky;top:0;z-index:200}}
.site-title{{font-size:1.25em;font-weight:700;background:linear-gradient(135deg,#22d3ee,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.site-meta{{font-size:.74em;color:var(--muted);margin-top:3px}}

/* Toolbar */
.toolbar{{background:#0d1424;border-bottom:1px solid var(--border);position:sticky;top:52px;z-index:150}}
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
.btn.lv-hot.on{{background:#065f46;border-color:#059669;color:#6ee7b7}}
.btn.lv-easy.on{{background:#0c2340;border-color:#0284c7;color:#7dd3fc}}
.btn.lv-medium.on{{background:#451a03;border-color:#b45309;color:#fde68a}}
.btn.lv-hard.on{{background:#450a0a;border-color:#b91c1c;color:#fca5a5}}
.btn.day.on,.btn.expand-btn.on{{background:#1e3a5f;border-color:#0284c7;color:#7dd3fc}}
.btn.time-btn.on{{background:#1e293b;border-color:#334155;color:var(--text)}}
.btn.reset{{border-color:#334155}}

/* Views */
.main{{padding:12px 20px;display:flex;flex-direction:column;gap:10px}}
.view{{display:none}}.view.on{{display:flex;flex-direction:column;gap:10px}}
.sum-legend{{font-size:.74em;color:var(--muted);padding:6px 2px}}

/* Facility cards */
.facility{{background:var(--card);border:1px solid var(--border);border-radius:10px;overflow:hidden}}
.fac-hd{{padding:10px 16px;font-size:.93em;font-weight:600;cursor:pointer;user-select:none;display:flex;justify-content:space-between;align-items:center;background:linear-gradient(90deg,#1e3a5f18,transparent);transition:background .15s}}
.fac-hd:hover{{background:linear-gradient(90deg,#1e3a5f44,transparent)}}
.arrow{{font-size:.75em;color:var(--muted);transition:transform .2s}}
.fac-hd.open .arrow{{transform:rotate(180deg)}}
.fac-bd{{display:none}}.fac-bd.open{{display:block}}

/* Table */
.tbl-wrap{{overflow-x:auto;padding-bottom:4px}}
table{{border-collapse:collapse;font-size:.82em;white-space:nowrap}}
thead th{{padding:6px 9px;text-align:center;background:#141e30;border-bottom:2px solid var(--border);font-weight:600;position:sticky;top:0;z-index:10}}
thead th.col-we{{background:#1a1232}}
thead th.col-time{{position:sticky;left:0;z-index:20;background:#0f1829}}
tbody tr:hover td{{filter:brightness(1.18)}}
tbody td{{padding:5px 9px;text-align:center;border-top:1px solid #161f30}}
tbody td.col-time{{background:#0f1829;font-weight:600;text-align:center;position:sticky;left:0;z-index:5;border-right:1px solid var(--border)}}
tbody td.col-we{{background:var(--we-tint)}}
td.lv-hot{{background:var(--hot-bg);color:var(--hot-fg);font-weight:700}}
td.lv-easy{{background:var(--easy-bg);color:var(--easy-fg)}}
td.lv-medium{{background:var(--med-bg);color:var(--med-fg)}}
td.lv-hard{{background:var(--hard-bg);color:var(--hard-fg)}}
td.lv-unavailable,td.lv-other{{background:var(--na-bg);color:var(--na-fg)}}

/* Filter states */
body.hide-wd .col-wd{{display:none}}
body.hide-we .col-we{{display:none}}
body.dim-mode td[data-level]{{opacity:.07}}
body.dim-mode td.col-time{{opacity:1!important}}
body.dim-mode td.lv-show{{opacity:1}}

.we-date > .fac-hd{{background:linear-gradient(90deg,#2d1f4e44,transparent)}}
@media(max-width:640px){{
  .site-header,.filters,.tabs,.main{{padding-left:10px;padding-right:10px}}
  table{{font-size:.74em}}tbody td{{padding:4px 5px}}
}}
</style>
</head>
<body>

<header class="site-header">
  <div class="site-title">🎾 富士見テニスコート 抽選申込状況</div>
  <div class="site-meta">更新: {now} &nbsp;·&nbsp; {next_month}月 &nbsp;·&nbsp; 表示: 名額/申請数</div>
</header>

<div class="toolbar">
  <div class="tabs">
    <button class="tab on" onclick="setView('summary',this)">サマリー</button>
    <button class="tab" onclick="setView('facility',this)">場別</button>
    <button class="tab" onclick="setView('date',this)">日付別</button>
  </div>
  <div class="filters">
    <div class="fg">
      <span class="flabel">難易度</span>
      <button class="btn lv-hot" data-lv="hot" onclick="toggleLv(this)">★ 必中(0人)</button>
      <button class="btn lv-easy" data-lv="easy" onclick="toggleLv(this)">● 容易(1-3)</button>
      <button class="btn lv-medium" data-lv="medium" onclick="toggleLv(this)">△ 一般(4-10)</button>
      <button class="btn lv-hard" data-lv="hard" onclick="toggleLv(this)">× 激戦(11+)</button>
    </div>
    <div class="fg">
      <span class="flabel">曜日</span>
      <button class="btn day on" data-day="all" onclick="setDay(this)">全部</button>
      <button class="btn day" data-day="wd" onclick="setDay(this)">平日</button>
      <button class="btn day" data-day="we" onclick="setDay(this)">週末</button>
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

<div class="main">
  <div class="view on" id="view-summary">
    <div class="sum-legend">12面の中で一番申請が少ないコートの数値を表示 &nbsp;·&nbsp; 緑＝必中、青＝容易、黄＝一般、赤＝激戦</div>
    <div class="facility">
      {sum_html}
    </div>
  </div>
  <div class="view" id="view-facility">
{fac_html}  </div>
  <div class="view" id="view-date">
{date_html}  </div>
</div>

<script>
const SLOTS = {slots_json};
const activeLvs = new Set();
let activeTimes = new Set(SLOTS);
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
    applyTime();
  }};
  tfg.appendChild(b);
}});

function applyTime() {{
  document.querySelectorAll('.row-time').forEach(tr => {{
    tr.style.display = activeTimes.has(tr.dataset.time) ? '' : 'none';
  }});
}}

function toggleLv(btn) {{
  btn.classList.toggle('on');
  const lv = btn.dataset.lv;
  if (activeLvs.has(lv)) activeLvs.delete(lv); else activeLvs.add(lv);
  applyLv();
}}

function applyLv() {{
  if (activeLvs.size === 0) {{
    document.body.classList.remove('dim-mode');
    document.querySelectorAll('td.lv-show').forEach(td => td.classList.remove('lv-show'));
  }} else {{
    document.body.classList.add('dim-mode');
    document.querySelectorAll('td[data-level]').forEach(td => {{
      td.classList.toggle('lv-show', activeLvs.has(td.dataset.level));
    }});
  }}
}}

function setDay(btn) {{
  document.querySelectorAll('.btn.day').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  document.body.classList.remove('hide-wd', 'hide-we');
  const d = btn.dataset.day;
  if (d === 'wd') document.body.classList.add('hide-we');
  if (d === 'we') document.body.classList.add('hide-wd');
}}

function setView(view, btn) {{
  document.querySelectorAll('.tab').forEach(b => b.classList.remove('on'));
  btn.classList.add('on');
  document.getElementById('view-summary').classList.toggle('on', view === 'summary');
  document.getElementById('view-facility').classList.toggle('on', view === 'facility');
  document.getElementById('view-date').classList.toggle('on', view === 'date');
  document.getElementById('expand-fg').style.display = (view === 'facility' || view === 'date') ? '' : 'none';
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
  activeLvs.clear();
  activeTimes = new Set(SLOTS);
  document.querySelectorAll('.btn[data-lv]').forEach(b => b.classList.remove('on'));
  document.querySelectorAll('.btn.time-btn').forEach(b => b.classList.add('on'));
  document.querySelectorAll('.btn.day').forEach(b => b.classList.remove('on'));
  document.querySelector('.btn.day[data-day="all"]').classList.add('on');
  document.body.classList.remove('dim-mode', 'hide-wd', 'hide-we');
  document.querySelectorAll('td.lv-show').forEach(td => td.classList.remove('lv-show'));
  document.querySelectorAll('.row-time').forEach(tr => tr.style.display = '');
}}
</script>
</body>
</html>'''

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"HTML 报告已保存: {output_path}")


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
    if not facility_soup:
        exit(1)

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

    # 输出 HTML
    output_path = os.path.join(os.path.dirname(__file__), "docs", "index.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    write_html(all_data, output_path)

    print(f"\n{'=' * 60}")
    print("完成！")
