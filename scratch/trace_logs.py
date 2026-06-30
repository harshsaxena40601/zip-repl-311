
import re

def analyze_section(filepath, start_token):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    start = content.find(start_token)
    end = len(content)
    sections = [
        'function StatCard', 'function ScraperCard', 'function GlobalShopifyPanel',
        'function ActivityLogsPage', 'function AddWebsiteModal', 'export default function App'
    ]
    for next_section in sections:
        next_start = content.find(next_section, start + 1)
        if next_start != -1 and next_start < end:
            end = next_start
    
    block = content[start:end]
    lines = block.split('\n')
    stack = []
    
    for i, line in enumerate(lines):
        line_num = i + 1 # Relative to block
        # Find opens (ignoring self-closing)
        line_clean = re.sub(r'<(div|motion\.div)[^>]*/>', ' ', line)
        opens = re.findall(r'<(div|motion\.div)', line_clean)
        closes = re.findall(r'</(div|motion\.div)', line_clean)
        
        for o in opens:
            stack.append((line_num, o))
            print(f"{line_num:3}: + <{o}>")
        
        for c in closes:
            if stack:
                l, t = stack.pop()
                print(f"{line_num:3}: - </{c}> (closes line {l})")
            else:
                print(f"{line_num:3}: - </{c}> EXTRA!!")

if __name__ == '__main__':
    analyze_section('src/App.tsx', 'function ActivityLogsPage')
