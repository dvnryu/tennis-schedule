import os
import requests
from bs4 import BeautifulSoup
import re
import warnings
from datetime import datetime

warnings.filterwarnings('ignore')

BASE_URL = "https://www.fureai-net.city.kawasaki.jp/web"
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


def get_full_month_schedule(session, facility_soup, facility, start_ymd='20260301'):
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
    now = datetime.now().strftime('%Y-%m-%d %H:%M')

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>富士見テニスコート 抽選状況</title>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #0f172a; color: #e2e8f0; padding: 20px;
    max-width: 1200px; margin: 0 auto;
  }}
  h1 {{
    text-align: center; font-size: 1.6em; margin-bottom: 4px;
    background: linear-gradient(135deg, #22d3ee, #a78bfa);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .subtitle {{ text-align: center; color: #94a3b8; font-size: 0.85em; margin-bottom: 24px; }}
  .legend {{
    display: flex; gap: 16px; justify-content: center; flex-wrap: wrap;
    margin-bottom: 24px; font-size: 0.82em;
  }}
  .legend span {{
    padding: 4px 10px; border-radius: 6px; display: inline-flex; align-items: center; gap: 4px;
  }}
  .legend .hot {{ background: #065f46; color: #6ee7b7; }}
  .legend .easy {{ background: #1e3a5f; color: #7dd3fc; }}
  .legend .medium {{ background: #713f12; color: #fde68a; }}
  .legend .hard {{ background: #7f1d1d; color: #fca5a5; }}
  .legend .unavailable {{ background: #1e293b; color: #64748b; }}

  .facility {{
    background: #1e293b; border-radius: 12px; margin-bottom: 20px;
    overflow: hidden; border: 1px solid #334155;
  }}
  .facility-header {{
    padding: 12px 20px; font-size: 1.1em; font-weight: 600;
    background: linear-gradient(135deg, #1e3a5f, #312e81);
    cursor: pointer; user-select: none;
    display: flex; justify-content: space-between; align-items: center;
  }}
  .facility-header:hover {{ background: linear-gradient(135deg, #1e40af, #4338ca); }}
  .facility-header .toggle {{ font-size: 0.8em; color: #94a3b8; }}
  .facility-body {{ padding: 12px; overflow-x: auto; }}
  .facility-body.collapsed {{ display: none; }}

  table {{
    width: 100%; border-collapse: collapse; font-size: 0.85em;
    margin-bottom: 8px;
  }}
  th, td {{
    padding: 6px 8px; text-align: center; white-space: nowrap;
    border: 1px solid #334155;
  }}
  th {{ background: #334155; font-weight: 600; position: sticky; top: 0; }}
  th.weekend {{ background: #3b2d60; }}
  td.time {{ background: #1e3a5f; font-weight: 600; text-align: right; }}
  td.hot {{ background: #065f46; color: #6ee7b7; font-weight: 700; }}
  td.easy {{ background: #0c3547; color: #7dd3fc; }}
  td.medium {{ background: #422006; color: #fde68a; }}
  td.hard {{ background: #450a0a; color: #fca5a5; }}
  td.unavailable {{ background: #0f172a; color: #475569; }}
  td.weekend {{ background-color: rgba(99, 102, 241, 0.08); }}

  .week-label {{
    font-size: 0.75em; color: #64748b; padding: 8px 0 2px;
    border-bottom: 1px solid #1e293b;
  }}

  @media (max-width: 640px) {{
    body {{ padding: 8px; }}
    table {{ font-size: 0.75em; }}
    th, td {{ padding: 4px 3px; }}
  }}
</style>
</head>
<body>
<h1>&#127934; 富士見テニスコート 抽選申込状況</h1>
<p class="subtitle">更新時間: {now} &nbsp;|&nbsp; 対象月: 3月 &nbsp;|&nbsp; 格式: 名額数/申請人数</p>

<div class="legend">
  <span class="hot">&#9733; 0人 必中</span>
  <span class="easy">1~3人 容易</span>
  <span class="medium">4~10人 一般</span>
  <span class="hard">11人+ 激戦</span>
  <span class="unavailable">- 不可</span>
</div>
'''

    for facility_name, schedule in all_data.items():
        html += f'<div class="facility">\n'
        html += f'<div class="facility-header" onclick="this.nextElementSibling.classList.toggle(\'collapsed\')">'
        html += f'{facility_name} <span class="toggle">&#9660;</span></div>\n'
        html += f'<div class="facility-body">\n'

        if not schedule:
            html += '<p style="padding:12px;color:#64748b;">无数据</p>\n'
            html += '</div></div>\n'
            continue

        # 收集时段并排序
        all_times = set()
        for dd in schedule.values():
            all_times.update(dd.keys())
        time_slots = sorted(all_times, key=lambda t: format_time(t))

        # 日期排序
        dates = sorted(schedule.keys(), key=date_sort_key)

        # 按周输出表格
        week_size = 7
        for ws in range(0, len(dates), week_size):
            week_dates = dates[ws:ws + week_size]

            html += '<table>\n<tr><th>时段</th>'
            for d in week_dates:
                cls = ' class="weekend"' if is_weekend(d) else ''
                html += f'<th{cls}>{short_date(d)}</th>'
            html += '</tr>\n'

            for ts in time_slots:
                html += f'<tr><td class="time">{format_time(ts)}</td>'
                for d in week_dates:
                    val = schedule.get(d, {}).get(ts, '-')
                    cc = cell_class(val)
                    we = ' weekend' if is_weekend(d) else ''
                    display = val
                    if val.endswith('/0'):
                        display = f'&#9733;{val}'
                    html += f'<td class="{cc}{we}">{display}</td>'
                html += '</tr>\n'

            html += '</table>\n'

        html += '</div></div>\n'

    html += '''
<script>
// 默认折叠除第一个外的所有场地
document.querySelectorAll('.facility-body').forEach((el, i) => {
  if (i > 0) el.classList.add('collapsed');
});
</script>
</body>
</html>
'''

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
