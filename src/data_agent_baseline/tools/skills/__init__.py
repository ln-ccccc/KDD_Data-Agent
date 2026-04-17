import importlib
import pkgutil
import inspect
import sys
from pathlib import Path
from data_agent_baseline.tools.registry import ToolRegistry, ToolSpec

def load_skills(registry: ToolRegistry) -> None:
    """
    Dynamically loads all skills defined in the `skills` package.
    A skill file should define a `TOOL_SPEC` (ToolSpec) and a `TOOL_HANDLER` (callable).
    """
    skills_dir = Path(__file__).parent
    
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
            print(f"Loaded skill: {spec.name} from {module_name}.py")