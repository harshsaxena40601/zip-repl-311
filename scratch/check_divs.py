
import re

def check_tags(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Use regex to find all opening, closing, and self-closing tags
    # Focus on div and motion.div
    tags = re.findall(r'<(div|motion\.div)[^>]*>|</div>|</motion\.div>', content)
    
    # This regex is hard. Let's do it line by line and handle self-closing.
    lines = content.split('\n')
    stack = []
    
    for i, line in enumerate(lines):
        line_num = i + 1
        # Find all tags in this line
        # 1. Self-closing: <tag ... />
        # 2. Opening: <tag ... >
        # 3. Closing: </tag>
        
        # Remove self-closing tags first so they don't interfere
        line_clean = re.sub(r'<(div|motion\.div)[^>]*/>', ' ', line)
        
        # Find opening tags
        opens = re.findall(r'<(div|motion\.div)', line_clean)
        # Find closing tags
        closes = re.findall(r'</(div|motion\.div)', line_clean)
        
        for o in opens:
            stack.append((line_num, o))
        
        for c in closes:
            if not stack:
                print(f"Extra closing tag at line {line_num}: </{c}>")
            else:
                last_line, last_tag = stack.pop()
                if last_tag != c:
                    print(f"Mismatch at line {line_num}: <{last_tag}> (from line {last_line}) closed by </{c}>")
    
    for line_num, tag in stack:
        print(f"Unclosed tag from line {line_num}: <{tag}>")

if __name__ == '__main__':
    check_tags('src/App.tsx')

