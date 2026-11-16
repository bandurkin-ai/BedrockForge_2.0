# Splits docs/TZ/BF2-TZ.docx (converted by pandoc to GFM) into docs/spec/*.md.
# Usage: pandoc docs/TZ/BF2-TZ.docx -t gfm --wrap=none -o /tmp/tz.md && python tools/spec/split_tz.py /tmp/tz.md docs/spec
import re, os, sys
src, out = sys.argv[1], sys.argv[2]
t = open(src, encoding='utf-8').read().splitlines()
TR = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
 ["a","b","v","g","d","e","e","zh","z","i","y","k","l","m","n","o","p","r","s","t","u","f","kh","ts","ch","sh","shch","","y","","e","yu","ya"]))
def slug(s, n=5):
    s = s.lower()
    s = ''.join(TR.get(c, c) for c in s)
    s = re.sub(r'\\\[.*?\\\]|\[.*?\]|\(.*?\)', ' ', s)
    w = re.findall(r'[a-z0-9]+', s)
    return '-'.join(w[:n])
os.makedirs(out, exist_ok=True)
files = []  # (name, title, lines)
cur = None
def start(name, title):
    global cur
    cur = [name, title, []]; files.append(cur)
appx = {'А':'a','Б':'b','В':'v','Г':'g','Д':'d','Е':'e','Ж':'zh'}
for line in t:
    m1 = re.match(r'^# (.*)', line)
    m2 = re.match(r'^## (\d+)\. (.*)', line)
    ma = re.match(r'^## Приложение (\S)\. (.*)', line)
    m3 = re.match(r'^### ([78])\.(\d+)\. (.*)', line)
    if m1:
        cur = None; continue
    if m2:
        n = int(m2.group(1))
        if n in (7, 8):
            start(f'{n:02d}-00-intro-{slug(m2.group(2),4)}', line[3:])
        else:
            start(f'{n:02d}-{slug(m2.group(2))}', line[3:])
    elif ma:
        start(f'pril-{appx[ma.group(1)]}-{slug(ma.group(2),4)}', line[3:])
    elif m3:
        a, b = int(m3.group(1)), int(m3.group(2))
        # merge 7.0 / 8.0 into intro file
        if b == 0:
            cur[2].append(line); continue
        start(f'{a:02d}-{b:02d}-{slug(m3.group(3))}', line[4:])
    if cur is None:
        if line.strip(): start('00-preamble', 'Преамбула')
        else: continue
    cur[2].append(line)
for name, title, lines in files:
    with open(os.path.join(out, name + '.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines).strip() + '\n')
    print(f'{name}.md\t{sum(len(l) for l in lines)}\t{title}')
