import re

with open('tests/test_photo_warm.py', 'r') as f:
    content = f.read()

# Fix list matching
content = re.sub(r'\["(f[0-9]+)", "(f[0-9]+)"\]', r'[("\1", ROOT), ("\2", ROOT)]', content)
content = content.replace('["f3"]', '[("f3", ROOT)]')
content = content.replace('["f1", "f2", "f3"]', '[("f1", ROOT), ("f2", ROOT), ("f3", ROOT)]')
content = content.replace('["f2", "f3"]', '[("f2", ROOT), ("f3", ROOT)]')

# Fix in / not in
content = re.sub(r'assert "(f[0-9]+)" (not )?in pw\._warm_known', r'assert ("\1", ROOT) \2in pw._warm_known', content)
content = re.sub(r'assert "([0-9_]+)" (not )?in pw\._warm_known', r'assert ("\1", ROOT) \2in pw._warm_known', content)

# Check for enqueue_warm inside test_concurrent_enqueue_starts_one_thread worker loop
content = content.replace('pw.enqueue_warm(f"f{i}", settings)', 'pw.enqueue_warm(f"f{i}", ROOT, settings)')

with open('tests/test_photo_warm.py', 'w') as f:
    f.write(content)
