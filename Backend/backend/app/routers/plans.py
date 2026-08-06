from fastapi import APIRouter

from ..schemas import PlanCreate, PlanOut, PlanRefinedTaskUpdate, PlanUpdate
from ..services.plans import (
    create_plan,
    delete_all_plans,
    delete_plan,
    delete_plan_refined_task,
    list_month_plans,
    list_plans,
    save_plan_refined_task,
    update_plan,
)

router = APIRouter(prefix="/api/plans", tags=["plans"])


@router.get("", response_model=list[PlanOut])
def get_plans(date: str) -> list[PlanOut]:
    return list_plans(date)


@router.get("/month", response_model=list[PlanOut])
def get_month_plans(year: int, month: int) -> list[PlanOut]:
    return list_month_plans(year, month)


@router.post("", response_model=PlanOut)
def post_plan(payload: PlanCreate) -> PlanOut:
    return create_plan(payload)


@router.patch("/{plan_id}", response_model=PlanOut)
def patch_plan(plan_id: str, payload: PlanUpdate) -> PlanOut:
    return update_plan(plan_id, payload)


@router.patch("/{plan_id}/refined-task", response_model=PlanOut)
def patch_plan_refined_task(plan_id: str, payload: PlanRefinedTaskUpdate) -> PlanOut:
    return save_plan_refined_task(plan_id, payload)


@router.delete("/{plan_id}/refined-task", response_model=PlanOut)
def remove_plan_refined_task(plan_id: str) -> PlanOut:
    return delete_plan_refined_task(plan_id)


@router.delete("/all")
def remove_all_plans() -> dict[str, int]:
    return {"deleted": delete_all_plans()}


@router.delete("/{plan_id}", status_code=204)
def remove_plan(plan_id: str) -> None:
    delete_plan(plan_id)
