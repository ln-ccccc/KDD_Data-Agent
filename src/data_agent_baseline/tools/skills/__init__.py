import importlib
import pkgutil
import inspect
import sys
from pathlib import Path
from typing import Any
from data_agent_baseline.tools.registry import ToolRegistry, ToolSpec, ToolExecutionResult
from data_agent_baseline.benchmark.schema import PublicTask

def _create_markdown_skill_handler(content: str) -> Any:
    """Creates a tool handler that simply returns the markdown content."""
    def handler(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(ok=True, content=content)
    return handler

def load_skills(registry: ToolRegistry) -> None:
    """
    Dynamically loads all skills defined in the `skills` package.
    Supports both .py files (executable skills) and .md files (declarative skills).
    """
    skills_dir = Path(__file__).parent
    
    # 1. Load Python Skills (.py)
    for _, module_name, _ in pkgutil.iter_modules([str(skills_dir)]):
        full_module_name = f"data_agent_baseline.tools.skills.{module_name}"
        
        # Import the module
        if full_module_name not in sys.modules:
            module = importlib.import_module(full_module_name)
        else:
            module = sys.modules[full_module_name]
            importlib.reload(module)
            
        # Check if it has the required attributes
        if hasattr(module, "TOOL_SPEC") and hasattr(module, "TOOL_HANDLER"):
            spec: ToolSpec = getattr(module, "TOOL_SPEC")
            handler = getattr(module, "TOOL_HANDLER")
            
            # Register the skill
            registry.specs[spec.name] = spec
            registry.handlers[spec.name] = handler
            print(f"Loaded Code Skill: {spec.name} from {module_name}.py")
            
    # 2. Load Markdown Skills (.md)
    for md_file in skills_dir.glob("*.md"):
        skill_name = md_file.stem
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Extract the first line as description if it's a heading
        lines = content.strip().split("\n")
        description = lines[0].replace("#", "").strip() if lines and lines[0].startswith("#") else f"Learn how to handle {skill_name} patterns."
        
        spec = ToolSpec(
            name=f"skill_learn_{skill_name}",
            description=f"Skill (Knowledge): {description}. Use this tool with no arguments to read the cheat sheet/guide.",
            input_schema={}
        )
        
        registry.specs[spec.name] = spec
        registry.handlers[spec.name] = _create_markdown_skill_handler(content)
        print(f"Loaded Knowledge Skill: {spec.name} from {md_file.name}")