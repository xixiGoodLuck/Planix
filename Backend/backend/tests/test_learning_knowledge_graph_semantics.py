from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.learning.contracts import KnowledgeEdge
from app.learning.generators.knowledge_generator import KnowledgeDrafts
from app.learning.services import KnowledgeGenerationPipeline, KnowledgePipelineError
from app.learning.validators import (
    LearningArtifactValidationError,
    LearningArtifactValidator,
)
from learning_fixtures import build_fastapi_crud_learning_fixture
from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    fastapi_pipeline_responses,
)


def _edge(source: str, target: str, relation: str) -> KnowledgeEdge:
    return KnowledgeEdge(
        sourceKnowledgeId=source,
        targetKnowledgeId=target,
        relation=relation,
        reason=f"test {relation} relation",
    )


def _valid_drafts() -> dict:
    return {
        "knowledge": [
            {
                "name": "Foundation",
                "explanation": "A foundational concept.",
                "whyRequired": "It supports the target capability.",
                "capabilityIndexes": [0],
                "importance": "required",
                "masteryIndicators": ["Explain the foundation"],
            },
            {
                "name": "Application",
                "explanation": "An applied concept.",
                "whyRequired": "It applies the foundation.",
                "capabilityIndexes": [0],
                "importance": "required",
                "masteryIndicators": ["Apply the foundation"],
            },
        ],
        "edges": [],
    }


def test_only_prerequisite_edges_participate_in_prerequisite_dag() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    first, second = fixture.knowledge_graph.nodes[:2]
    graph = fixture.knowledge_graph.model_copy(
        update={
            "edges": [
                *fixture.knowledge_graph.edges,
                _edge(first.id, second.id, "supports"),
                _edge(second.id, first.id, "supports"),
            ]
        }
    )

    LearningArtifactValidator().validate_knowledge_graph(
        fixture.scope,
        fixture.capability_graph,
        graph,
    )


def test_part_of_uses_an_independent_containment_dag() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    first, second = fixture.knowledge_graph.nodes[:2]
    graph = fixture.knowledge_graph.model_copy(
        update={
            "edges": [
                *fixture.knowledge_graph.edges,
                _edge(first.id, second.id, "part_of"),
                _edge(second.id, first.id, "part_of"),
            ]
        }
    )

    with pytest.raises(
        LearningArtifactValidationError,
        match="knowledge_cycle_containment.*cycle path",
    ):
        LearningArtifactValidator().validate_knowledge_graph(
            fixture.scope,
            fixture.capability_graph,
            graph,
        )


@pytest.mark.parametrize(
    "source,target,expected",
    [
        (0, 0, "prior-index-only"),
        (1, 0, "prior-index-only"),
        (0, 2, "available indexes"),
    ],
)
def test_prerequisite_draft_rejects_self_forward_and_out_of_range_indexes(
    source: int,
    target: int,
    expected: str,
) -> None:
    raw = _valid_drafts()
    raw["edges"] = [
        {
            "sourceIndex": source,
            "targetIndex": target,
            "relation": "prerequisite",
            "reason": "invalid prerequisite",
        }
    ]

    with pytest.raises(ValidationError, match=expected):
        KnowledgeDrafts.model_validate(raw)


def test_required_capability_requires_required_knowledge_coverage() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    missing_id = "capability-persistence"
    nodes = [
        node.model_copy(
            update={
                "capability_refs": [
                    item for item in node.capability_refs if item != missing_id
                ]
            }
        )
        for node in fixture.knowledge_graph.nodes
    ]
    graph = fixture.knowledge_graph.model_copy(update={"nodes": nodes})

    with pytest.raises(
        LearningArtifactValidationError,
        match="required_capability_coverage",
    ) as caught:
        LearningArtifactValidator().validate_knowledge_graph(
            fixture.scope,
            fixture.capability_graph,
            graph,
        )

    assert caught.value.path.endswith(missing_id)


def test_targeted_repair_only_appends_and_binds_missing_capability() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    responses = deepcopy(fastapi_pipeline_responses()[:3])
    for node in responses[2]["knowledge"]:
        node["capabilityIndexes"] = [
            index for index in node["capabilityIndexes"] if index != 2
        ] or [0]
    responses.append(
        {
            "additions": [
                {
                    "name": "Persistence lifecycle",
                    "explanation": "Persistent state survives separate requests.",
                    "whyRequired": "The missing persistence capability requires it.",
                    "capabilityIndexes": [2],
                    "importance": "required",
                    "masteryIndicators": ["Store and retrieve state"],
                    "prerequisiteIndexes": [0],
                }
            ]
        }
    )
    model = ScriptedPipelineModel(responses)

    result = KnowledgeGenerationPipeline(model=model).generate(fixture.scope)

    original_names = [
        item["name"] for item in fastapi_pipeline_responses()[2]["knowledge"]
    ]
    persistence_id = next(
        item.id
        for item in result.capability_graph.capabilities
        if item.name == "Persistence"
    )
    assert [item.name for item in result.knowledge_graph.nodes[:-1]] == original_names
    for persisted, original in zip(
        result.knowledge_graph.nodes[:-1],
        fastapi_pipeline_responses()[2]["knowledge"],
        strict=True,
    ):
        assert persisted.explanation == original["explanation"]
        assert persisted.why_required == original["whyRequired"]
        assert persisted.importance == original["importance"]
    assert result.knowledge_graph.nodes[-1].name == "Persistence lifecycle"
    assert persistence_id in result.knowledge_graph.nodes[-1].capability_refs
    assert result.model_usage["knowledge"]["graphRepairs"] == 1
    assert [call["feature"] for call in model.calls][-1] == (
        "learning_knowledge_graph_repair"
    )


def test_contract_repair_is_bounded_to_one_retry() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    responses = deepcopy(fastapi_pipeline_responses()[:3])
    invalid = deepcopy(responses[2])
    invalid["edges"][0]["sourceIndex"] = 3
    invalid["edges"][0]["targetIndex"] = 0
    responses[2] = invalid
    responses.append(deepcopy(fastapi_pipeline_responses()[2]))
    model = ScriptedPipelineModel(responses)

    result = KnowledgeGenerationPipeline(model=model).generate(fixture.scope)

    assert len(model.calls) == 4
    assert result.model_usage["knowledge"]["contractRepairs"] == 1
    assert model.calls[-1]["feature"] == "learning_knowledge_contract_repair"


def test_cycle_still_fails_closed_after_one_append_only_repair() -> None:
    fixture = build_fastapi_crud_learning_fixture()
    responses = deepcopy(fastapi_pipeline_responses()[:3])
    responses[2]["edges"] = [
        {
            "sourceIndex": 0,
            "targetIndex": 1,
            "relation": "part_of",
            "reason": "first containment",
        },
        {
            "sourceIndex": 1,
            "targetIndex": 0,
            "relation": "part_of",
            "reason": "reverse containment",
        },
    ]
    responses.append({"additions": []})
    model = ScriptedPipelineModel(responses)

    with pytest.raises(KnowledgePipelineError, match="knowledge_cycle_containment"):
        KnowledgeGenerationPipeline(model=model).generate(fixture.scope)

    assert len(model.calls) == 4
    assert model.calls[-1]["feature"] == "learning_knowledge_graph_repair"
