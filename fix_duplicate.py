#!/usr/bin/env python3
"""
Fix the duplicate code in swimcloud_client.py _get function.
"""
import re

with open('swimcloud_client.py', 'r') as f:
    content = f.read()

# Find and fix the duplicate lines
# The issue is lines 81-84 are duplicates of 74-77
lines = content.split('\n')
fixed_lines = []

for i, line in enumerate(lines):
    # Skip the duplicate lines (81-84 in 1-indexed, 80-83 in 0-indexed)
    if 80 <= i <= 83:
        continue
    fixed_lines.append(line)

fixed_content = '\n'.join(fixed_lines)

# Write back
with open('swimcloud_client.py', 'w') as f:
    f.write(fixed_content)

print("Fixed duplicate code in _get function")
print("Before fix had duplicate 'proxy_url' logic")
print("After fix should have clean logic")
