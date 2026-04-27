import re
content = open('src/data_agent_baseline/config.py').read()
content = re.sub(r'<<<<<<< HEAD\n=======\nimport os\n>>>>>>>[^\n]*\n', 'import os\n', content)
content = re.sub(r'<<<<<<< HEAD\n.*?agent_config = AgentConfig\(.*?\n        api_key=str\(agent_payload.get\("api_key", agent_defaults.api_key\)\),\n=======\n.*?api_key=str\(api_key_val\),\n>>>>>>>[^\n]*\n', '''    api_base_val = os.environ.get("DATA_AGENT_API_BASE") or agent_payload.get("api_base", agent_defaults.api_base)
    api_key_val = os.environ.get("DATA_AGENT_API_KEY") or agent_payload.get("api_key", agent_defaults.api_key)

    agent_config = AgentConfig(
        model=str(agent_payload.get("model", agent_defaults.model)),
        api_base=str(api_base_val),
        api_key=str(api_key_val),
''', content, flags=re.DOTALL)
open('src/data_agent_baseline/config.py', 'w').write(content)
