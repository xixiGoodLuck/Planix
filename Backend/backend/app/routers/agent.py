from fastapi import APIRouter

from ..schemas import AiPayload
from ..services.evaluator import PlannerEvaluator
from ..services.planner import PlannerAgent
from ..services.tools import list_tools

router = APIRouter(prefix="/api", tags=["agent"])

agent = PlannerAgent()
evaluator = PlannerEvaluator()


@router.post("/agent/plan")
def plan(payload: AiPayload) -> dict[str, object]:
    return agent.plan(payload)


@router.post("/agent/review")
def review(payload: AiPayload) -> dict[str, object]:
    return agent.review(payload)


@router.get("/agent/tools")
def tools() -> list[dict[str, object]]:
    return list_tools()


@router.post("/eval/planner")
def eval_planner(payload: AiPayload) -> dict[str, object]:
    return evaluator.run(payload)
