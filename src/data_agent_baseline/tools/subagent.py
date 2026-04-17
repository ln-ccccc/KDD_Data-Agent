import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from data_agent_baseline.benchmark.schema import PublicTask
from data_agent_baseline.agents.prompt import SUBAGENT_SYSTEM_PROMPT


def build_subagent_tool(
    create_agent_func: Callable[[], Any],
) -> Callable[[PublicTask, dict[str, Any]], Any]:
    """
    Creates a closure that acts as a tool handler for delegating tasks to a subagent.
    `create_agent_func` should return an instance of LangGraphReActAgent configured for the subagent.
    """
    from data_agent_baseline.tools.registry import ToolExecutionResult

    def delegate_to_data_analyst(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
        sub_task_description = action_input.get("sub_task_description")
        focus_files = action_input.get("focus_files", [])

        if not sub_task_description:
            return ToolExecutionResult(
                ok=False,
                content={"error": "sub_task_description is required to delegate to the data analyst subagent."},
            )

        # Create a proxy task for the subagent
        sub_task = PublicTask(
            id=f"{task.id}_subtask",
            question=f"SUB-TASK DESCRIPTION:\n{sub_task_description}\n\nFOCUS FILES:\n{json.dumps(focus_files)}",
            # We don't have answer details for the subtask, it just needs to explore
            answer_columns=[],
            answer_rows=[],
            tables=[],
            context_dir=task.context_dir,
        )

        try:
            # Instantiate the subagent with its specific configuration
            subagent = create_agent_func()
            
            # The subagent runs in its own isolated state machine
            run_result = subagent.run(sub_task)
            
            # The subagent's goal is to call `subagent_finish` which will set its "answer"
            if run_result.succeeded and run_result.answer:
                # Assuming subagent_finish sets the answer as a structured dict
                # For a normal AnswerTable it has columns/rows. But subagent_finish might return a generic dict
                # We will just extract the raw data it produced.
                # Actually, run_result.answer is an AnswerTable object. 
                # Let's define a special tool `subagent_finish` that just dumps its input into the AnswerTable
                
                # To make it simple without altering the AnswerTable schema, we can extract the last step's action_input 
                # if the action was subagent_finish.
                last_step = run_result.steps[-1] if run_result.steps else None
                if last_step and last_step.action == "subagent_finish":
                    return ToolExecutionResult(
                        ok=True,
                        content={
                            "status": "success",
                            "summary": last_step.action_input.get("summary", "No summary provided."),
                            "data_references": last_step.action_input.get("data_references", []),
                            "raw_findings": last_step.action_input.get("raw_findings", {}),
                        },
                    )
                else:
                    return ToolExecutionResult(
                        ok=True,
                        content={
                            "status": "partial_success",
                            "summary": "Subagent finished but did not explicitly call subagent_finish. Review its steps.",
                            "steps_count": len(run_result.steps),
                            "failure_reason": run_result.failure_reason,
                        },
                    )
            else:
                return ToolExecutionResult(
                    ok=False,
                    content={
                        "status": "failure",
                        "error": "Subagent failed to complete the task.",
                        "reason": run_result.failure_reason,
                        "steps_taken": len(run_result.steps),
                    },
                )
        except Exception as e:
            import traceback
            return ToolExecutionResult(
                ok=False,
                content={
                    "status": "exception",
                    "error": str(e),
                    "traceback": traceback.format_exc()
                }
            )

    return delegate_to_data_analyst


def subagent_finish(task: PublicTask, action_input: dict[str, Any]) -> Any:
    """
    Tool called by the subagent to report its findings back to the primary agent.
    It marks the subagent's execution as terminal.
    """
    from data_agent_baseline.tools.registry import ToolExecutionResult
    summary = action_input.get("summary", "")
    data_references = action_input.get("data_references", [])
    raw_findings = action_input.get("raw_findings", {})
    
    if not summary:
        return ToolExecutionResult(
            ok=False,
            content={"error": "You must provide a 'summary' of your findings."}
        )

    # We return a dummy AnswerTable to satisfy the typing, but the delegate handler will extract the action_input
    from data_agent_baseline.benchmark.schema import AnswerTable
    
    return ToolExecutionResult(
        ok=True,
        content="Subagent task completed. Findings recorded.",
        is_terminal=True,
        answer=AnswerTable(columns=["status"], rows=[["finished"]])
    )
