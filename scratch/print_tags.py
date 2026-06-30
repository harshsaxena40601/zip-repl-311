
import sys

with open('src/App.tsx', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines[715:950]):
    line_num = 716 + i
    if '<div' in line or '</div' in line or '<motion.div' in line or '</motion.div' in line:
        print(f"{line_num}: {line.strip()}")
