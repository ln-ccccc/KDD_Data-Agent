import re
from pathlib import Path

def resolve(path_str, strategy):
    p = Path(path_str)
    if not p.exists(): return
    content = p.read_text(encoding='utf-8')
    
    def repl(m):
        ours = m.group(1)
        theirs = m.group(2)
        if strategy == "both":
            # Add newline only if both are non-empty
            if ours and theirs:
                return ours + "\n" + theirs
            return ours + theirs
        elif strategy == "ours":
            return ours
        elif strategy == "theirs":
            return theirs
        return m.group(0)

    pattern = re.compile(r'<<<<<<< HEAD(?:\r?\n)?(.*?)(?:\r?\n)?=======(?:\r?\n)?(.*?)(?:\r?\n)?>>>>>>>[^\r\n]*(?:\r?\n|$)', flags=re.DOTALL)
    
    new_content = pattern.sub(repl, content)
    
    p.write_text(new_content, encoding='utf-8')

# 1. .gitignore - keep both
resolve('.gitignore', 'both')

# 2. README.zh.md - keep ours
resolve('README.zh.md', 'ours')

# 3. architecture_update.md - keep both
resolve('architecture_update.md', 'both')

# 4. pyproject.toml - keep ours
resolve('pyproject.toml', 'ours')

# 6. kdd_eval.py - keep ours
resolve('src/data_agent_baseline/benchmark/kdd_eval.py', 'ours')

# 7. config.py - keep both
resolve('src/data_agent_baseline/config.py', 'both')

# 10. runner.py - keep ours
resolve('src/data_agent_baseline/run/runner.py', 'ours')

# 11. duckdb_exec.py - keep both
resolve('src/data_agent_baseline/tools/duckdb_exec.py', 'both')

# 12. filesystem.py - keep ours
resolve('src/data_agent_baseline/tools/filesystem.py', 'ours')

# 14. subagent.py - keep ours
resolve('src/data_agent_baseline/tools/subagent.py', 'ours')

# For prompt.py, plan_execute_graph.py, react_graph.py, registry.py
resolve('src/data_agent_baseline/agents/prompt.py', 'both')
resolve('src/data_agent_baseline/orchestrators/langgraph/plan_execute_graph.py', 'ours')
resolve('src/data_agent_baseline/orchestrators/langgraph/react_graph.py', 'ours')
resolve('src/data_agent_baseline/tools/registry.py', 'both')

