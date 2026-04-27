content = open('src/data_agent_baseline/tools/duckdb_exec.py').read()
import re
content = re.sub(r'<<<<<<< ours\n=======\n(.*?)\n>>>>>>> theirs\n', r'\1\n', content, flags=re.DOTALL)
content = re.sub(r'<<<<<<< ours\n(.*?)\n=======\n(.*?)\n>>>>>>> theirs\n', r'\1\n', content, flags=re.DOTALL)
open('src/data_agent_baseline/tools/duckdb_exec.py', 'w').write(content)
