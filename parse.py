# -*- coding: utf-8 -*-
import gzip
import json
import os
import re
import sqlite3
import sys
from collections import OrderedDict

from bs4 import BeautifulSoup

INFLECTIONS = {}
ENTRIES = {}

FIX_TEXT_PATTERNS = {
    ' ': re.compile(r'\s+'),
    '(': re.compile(r'\(\s+'),
    ')': re.compile(r'\s+\)'),
    ', ': re.compile(r'\s*,\s*'),
    ' + ': re.compile(r'\s*\+\s*'),
    '/': re.compile(r'\s*\/\s*'),
}


def decrypt_blob(blob):
    """Decrypt blob to text"""
    # Keys to decrypt
    BLOB_KEY_A = 2
    BLOB_KEY_B = 7

    org_length = len(blob)
    length = org_length / BLOB_KEY_A
    length = int(length)

    tmp = [0] * org_length
    for x in range(0, length * 2, 2):
        if x > (BLOB_KEY_B * 2) + length:
            tmp[x] = blob[x]
            tmp[x + 1] = blob[x + 1]
        else:
            tmp[x] = blob[x + 1]
            tmp[x + 1] = blob[x]

    if org_length % 2 == 1:
        tmp[org_length - 1] = blob[org_length - 1]

    output = gzip.decompress(bytearray(tmp))
    return str(output, 'utf8')


def fix_text(text):
    text = text.strip()
    for rpl in FIX_TEXT_PATTERNS:
        text = FIX_TEXT_PATTERNS[rpl].sub(rpl, text)

    return text


def restore_html(html_doc):
    """
    TFlat uses custom tags to shorten HTML content.
    This method restores it back.
    """
    return (html_doc.replace('<d1', '<div class="')
            .replace('<d3>', '</div></div></div>')
            .replace('<a1', '<a href="')
            .replace('<s1', '<span class="')
            .replace('<s2>', '</span></span>'))


def parse_tab_content(content, entry={}):
    if not content or len(content) <= 3:
        return entry

    if not content.startswith('<div'):
        content = fix_text(content)
        content = '<div><div class="m">{}</div></div>'.format(content)

    soup = BeautifulSoup(content, 'html.parser')
    root = soup.div

    # Unwrap ul, li
    for elm in root.find_all(['ul', 'li']):
        elm.unwrap()

    pronunciation = ''
    try:
        pronunciation = root.find('div', attrs={'class': 'p5l fl'}).string
    except Exception:
        w = root.find(attrs={'class': 'w'})
        if w:
            parent = w.parent
            w.decompose()
            pronunciation = parent.text

    if pronunciation and 'pronunciation' not in entry:
        pronunciation = pronunciation.strip()
        if pronunciation:
            entry['pronunciation'] = pronunciation

    try:
        body = root.find(attrs={'class': re.compile(r'^[meidub]{1,2}$')}).parent
    except Exception:
        return entry

    # Unwrap e/em
    for elm_m in body.find_all(attrs={'class': 'm'}):
        tmp = []
        for elm in elm_m.find_all(attrs={'class': re.compile(r'^em?$')}):
            tmp.append(elm.extract())

        tmp.reverse()
        for t in tmp:
            elm_m.insert_after(t)

    if 'parts' in entry:
        parts = entry['parts']
    else:
        parts = OrderedDict()
        parts['_'] = {
            'meanings': OrderedDict(),
            'phrases': OrderedDict(),
        }

    current_part = '_'
    current_meaning = '_'
    current_example = ''
    current_example_meaning = ''
    current_phrase = ''
    current_phrase_meaning = ''

    for child in body.find_all(attrs={'class': re.compile(r'^[meidub]{1,2}$')}):
        if isinstance(child, str):
            continue

        text = fix_text(child.text)
        if 'ub' in child['class'] or 'b' in child['class']:
            current_part = text.lower().strip()
            if 'current_part' not in parts:
                parts[current_part] = {
                    'meanings': OrderedDict(),
                    'phrases': OrderedDict(),
                }
        elif 'm' in child['class']:
            current_meaning = text
            parts[current_part]['meanings'][current_meaning] = OrderedDict()
        elif 'e' in child['class']:
            current_example = text
        elif 'em' in child['class']:
            current_example_meaning = text
            if current_meaning not in parts[current_part]['meanings']:
                parts[current_part]['meanings'][current_meaning] = OrderedDict()
            parts[current_part]['meanings'][current_meaning][current_example] = current_example_meaning
        elif 'id' in child['class']:
            current_phrase = text
        elif 'im' in child['class']:
            current_phrase_meaning = text
            parts[current_part]['phrases'][current_phrase] = current_phrase_meaning

    entry['parts'] = parts
    return entry


def parse_word(row):
    """Parse word"""
    global INFLECTIONS, ENTRIES
    word, blob, mean = row

    print(word)
    # Word starts with '@' or '(xem)' = infection
    if mean.startswith('@') or mean.startswith('(xem)'):
        inf = ''
        # Remove '@' & '(xem)' prefixes
        if mean.startswith('(xem)'):
            inf = mean[5:]
        else:
            inf = mean[1:]

        inf = inf.strip().rstrip('#')
        if inf:
            inf = inf.replace('_', ' ')
            if inf not in INFLECTIONS:
                INFLECTIONS[inf] = set()

            INFLECTIONS[inf].add(word)
        return

    if len(blob) == 0:
        return

    # If first 3 chars are the same -> encrypted blob
    if len(blob) > 3 and blob[0] == blob[1] == blob[2]:
        data = decrypt_blob(blob[3:])
    else:
        data = str(blob, 'utf8')

    data = restore_html(data)
    # Main, eng-eng, technical, synonym, grammar
    tab_contents = data.split('##')
    entry = parse_tab_content(tab_contents[0], {})
    if entry and len(tab_contents) > 2:
        entry = parse_tab_content(tab_contents[2], entry)

    ENTRIES[word] = entry


def parse_file(db_file):
    # Connect to DB
    db = sqlite3.connect(db_file)
    cursor = db.cursor()

    # Query all words
    query = 'SELECT word, av, mean FROM av'
    cursor.execute(query)

    rows = cursor.fetchall()
    for row in rows:
        parse_word(row)


def serialize_sets(obj):
    """Make set JSON serializable"""
    if isinstance(obj, set):
        return list(obj)

    return obj


def main():
    args = sys.argv
    if len(args) == 1:
        print('parser.py <database-file>')
        return

    db_file = args[1]
    if not os.path.isfile(db_file):
        print(f'"{db_file}" is not a valid file!')
        return

    parse_file(db_file)

    output = {
        'entries': ENTRIES,
        'inflections': INFLECTIONS,
    }

    with open('output.json', 'w') as fp:
        json.dump(output, fp, indent=2, default=serialize_sets, ensure_ascii=False)


if __name__ == '__main__':
    main()
