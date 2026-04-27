import re
content = open('src/data_agent_baseline/tools/registry.py').read()
content = re.sub(r'<<<<<<< ours\n(.*?)=======\n.*?>>>>>>> theirs\n?', r'\1', content, flags=re.DOTALL)
open('src/data_agent_baseline/tools/registry.py', 'w').write(content)
