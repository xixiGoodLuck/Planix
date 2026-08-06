from fastapi import APIRouter

from ..schemas import AiMaterialDraftOut, AiMaterialDraftRequest
from ..services.model_knowledge import create_material_draft


router = APIRouter(prefix="/api/materials", tags=["materials"])


@router.post("/ai-draft", response_model=AiMaterialDraftOut)
def create_ai_material_draft(payload: AiMaterialDraftRequest) -> AiMaterialDraftOut:
    return create_material_draft(payload)
