"""Fix import path voor developer_api"""

with open('backend/main.py', 'r') as f:
    lines = f.readlines()

# Zoek de eerste regel
new_lines = []
added_path = False
for line in lines:
    if not added_path and (line.startswith('import ') or line.startswith('from ')):
        # Voeg sys.path fix toe voor de eerste import
        new_lines.append('import sys\n')
        new_lines.append('sys.path.insert(0, "/root/europese-zoekmachine")\n')
        new_lines.append('\n')
        added_path = True
    new_lines.append(line)

with open('backend/main.py', 'w') as f:
    f.writelines(new_lines)

print("✅ Import path gefixt!")
