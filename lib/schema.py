import re
import unicodedata
from datetime import date


def normalize_time_code(code):
    text = (code
            .replace('０', '0').replace('１', '1').replace('２', '2').replace('３', '3')
            .replace('４', '4').replace('５', '5').replace('６', '6').replace('７', '7')
            .replace('８', '8').replace('９', '9'))
    if len(text) == 4 and text.isdigit():
        return text
    return code


def format_time_label(code):
    text = normalize_time_code(code)
    if len(text) == 4 and text.isdigit():
        return f"{text[:2]}:{text[2:]}"
    return code


def parse_japanese_date_label(label, today):
    match = re.match(r'(\d+)月(\d+)日', label)
    if not match:
        return None
    month, day = int(match.group(1)), int(match.group(2))
    year = today.year + (1 if month < today.month else 0)
    return date(year, month, day)


def short_date_label(label):
    match = re.match(r'(\d+)月(\d+)日(.)', label)
    if match:
        return f"{match.group(1)}/{match.group(2)}({match.group(3)})"
    return label


def short_facility_name(name):
    normalized = unicodedata.normalize('NFKC', name)
    match = re.search(r'(\d+)$', normalized)
    return f'場{match.group(1)}' if match else name[-3:]
