
import re

def analyze_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    sections = [
        'function StatCard',
        'function ScraperCard',
        'function GlobalShopifyPanel',
        'function ActivityLogsPage',
        'function AddWebsiteModal',
        'export default function App'
    ]
    
    for i, section in enumerate(sections):
        start = content.find(section)
        if start == -1:
            print(f"Section {section} not found")
            continue
        
        # Find the end of the section (start of next section or end of file)
        end = len(content)
        for next_section in sections:
            next_start = content.find(next_section, start + 1)
            if next_start != -1 and next_start < end:
                end = next_start
        
        block = content[start:end]
        
        # Count tags, but ignore self-closing
        # Regex for non-self-closing tags
        div_open = len(re.findall(r'<div(?![^>]*/>)', block))
        div_close = block.count('</div')
        motion_open = len(re.findall(r'<motion\.div(?![^>]*/>)', block))
        motion_close = block.count('</motion.div>')
        
        print(f"{section}:")
        print(f"  div: {div_open} vs {div_close} ({div_open - div_close})")
        print(f"  motion.div: {motion_open} vs {motion_close} ({motion_open - motion_close})")

if __name__ == '__main__':
    analyze_file('src/App.tsx')
