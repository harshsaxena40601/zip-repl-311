
import re

def check_all_tags(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Simple regex for opening and closing tags (ignoring self-closing)
    # This is a bit naive but good for finding major imbalances
    tags = re.findall(r'<(div|motion\.div|main|header|footer|section|table|thead|tbody|tr|td|th|button|h1|h2|p|span)|</(div|motion\.div|main|header|footer|section|table|thead|tbody|tr|td|th|button|h1|h2|p|span)', content)
    
    stack = []
    for open_tag, close_tag in tags:
        if open_tag:
            stack.append(open_tag)
        else:
            if not stack:
                print(f"Extra closing tag: </{close_tag}>")
            else:
                last = stack.pop()
                if last != close_tag:
                    print(f"Mismatch: <{last}> closed by </{close_tag}>")
    
    if stack:
        print("Unclosed tags:", stack)
    else:
        print("All tags balanced (within reason)")

if __name__ == '__main__':
    check_all_tags('src/App.tsx')
