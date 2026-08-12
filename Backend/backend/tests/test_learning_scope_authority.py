from __future__ import annotations

from copy import deepcopy

import pytest

from app.learning.generators import (
    CapabilityGenerator,
    KnowledgeGenerator,
    LearningModelOutputError,
    LearningOutcomeGenerator,
)
from app.learning.contracts import CapabilityGraph, CapabilityNode, LearningOutcome
from app.learning.scope import build_explicit_scope_anchors

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
)


def _narrow_scope():
    source_ref = "user:scope-authority:goal"
    goal = "Understand FastAPI Routing, GET, POST, and Swagger."
    fixture = build_fastapi_learning_pipeline_fixture()
    return fixture.scope.model_copy(
        deep=True,
        update={
            "user_goal": goal,
            "target_result": goal,
            "target_result_status": "assumed",
            "source_refs": [source_ref],
            "explicit_scope_anchors": build_explicit_scope_anchors(
                user_goal=goal,
                user_goal_source_ref=source_ref,
                target_result=goal,
                target_result_status="assumed",
            ),
        },
    )


def test_enumerated_user_goal_creates_code_owned_concept_anchors() -> None:
    scope = _narrow_scope()

    assert [item.text for item in scope.explicit_scope_anchors if item.kind == "concept"] == [
        "FastAPI Routing",
        "GET",
        "POST",
        "Swagger",
    ]
    assert all(
        item.source_ref == "user:scope-authority:goal"
        for item in scope.explicit_scope_anchors
    )


def test_plain_conjunction_does_not_become_a_concept_list() -> None:
    anchors = build_explicit_scope_anchors(
        user_goal="Learn FastAPI and build a CRUD API",
        user_goal_source_ref="user:plain-conjunction",
        target_result="Build a CRUD API",
        target_result_status="assumed",
    )

    assert [item.kind for item in anchors] == ["user_goal"]


def test_resource_suffix_is_not_part_of_the_last_concept_anchor() -> None:
    anchors = build_explicit_scope_anchors(
        user_goal="Understand GET, POST, Path Operation, and Swagger UI from this video",
        user_goal_source_ref="user:video-list",
        target_result="Understand the named concepts",
        target_result_status="assumed",
    )

    assert [item.text for item in anchors if item.kind == "concept"] == [
        "GET",
        "POST",
        "Path Operation",
        "Swagger UI",
    ]


def test_required_outcome_cannot_use_broad_goal_anchor_when_concepts_exist() -> None:
    scope = _narrow_scope()
    response = {
        "outcomes": [{
            "statement": "Handle path and query parameters",
            "acceptanceCriteria": ["Explain path and query parameters"],
            "importance": "required",
            "scopeAnchorIndexes": [0],
        }]
    }
    model = ScriptedPipelineModel([deepcopy(response), deepcopy(response)])

    with pytest.raises(LearningModelOutputError, match="explicit scope anchor"):
        LearningOutcomeGenerator(model).generate(scope)

    assert len(model.calls) == 2


def test_required_adjacent_capability_cannot_misuse_routing_anchor() -> None:
    scope = _narrow_scope()
    routing_anchor = next(
        index
        for index, item in enumerate(scope.explicit_scope_anchors)
        if item.kind == "concept" and item.text == "FastAPI Routing"
    )
    outcome = LearningOutcome(
        id="outcome-routing",
        statement="Explain FastAPI routing",
        acceptanceCriteria=["Explain how routes bind URLs to functions"],
        importance="required",
        sourceGoalRefs=[scope.artifact_id],
        scopeAnchorRefs=[scope.explicit_scope_anchors[routing_anchor].id],
    )
    response = {
        "capabilities": [{
            "name": "Path parameters",
            "description": "Explain how curly-brace path parameters are passed to functions.",
            "whyRequired": "A common routing detail.",
            "outcomeIndexes": [0],
            "importance": "required",
            "scopeAnchorIndexes": [routing_anchor],
        }],
        "edges": [],
    }
    model = ScriptedPipelineModel([deepcopy(response), deepcopy(response)])

    with pytest.raises(LearningModelOutputError, match="explicit scope anchor"):
        CapabilityGenerator(model).generate(scope, [outcome])

    assert len(model.calls) == 2


def test_required_knowledge_cannot_split_one_concept_anchor_twice() -> None:
    scope = _narrow_scope()
    routing_anchor = next(
        index
        for index, item in enumerate(scope.explicit_scope_anchors)
        if item.kind == "concept" and item.text == "FastAPI Routing"
    )
    outcome = LearningOutcome(
        id="outcome-routing",
        statement="Explain FastAPI Routing",
        acceptanceCriteria=["Explain FastAPI Routing"],
        importance="required",
        sourceGoalRefs=[scope.artifact_id],
        scopeAnchorRefs=[scope.explicit_scope_anchors[routing_anchor].id],
    )
    capability_graph = CapabilityGraph(
        artifactId="capability-graph-routing",
        scopeRef={
            "artifactType": "learning_scope",
            "artifactId": scope.artifact_id,
            "version": scope.version,
        },
        outcomes=[outcome],
        capabilities=[
            CapabilityNode(
                id="capability-routing",
                name="FastAPI Routing",
                description="Explain FastAPI Routing",
                whyRequired="The user explicitly named it.",
                outcomeRefs=[outcome.id],
                importance="required",
                scopeAnchorRefs=[scope.explicit_scope_anchors[routing_anchor].id],
            )
        ],
    )
    response = {
        "knowledge": [
            {
                "name": "FastAPI Routing basics",
                "explanation": "FastAPI Routing basics.",
                "whyRequired": "Explicit concept.",
                "capabilityIndexes": [0],
                "importance": "required",
                "masteryIndicators": ["Explain routing"],
                "scopeAnchorIndexes": [routing_anchor],
                "coverageRequirements": ["Explain routing"],
            },
            {
                "name": "FastAPI Routing parameters",
                "explanation": "FastAPI Routing parameter details.",
                "whyRequired": "A split detail.",
                "capabilityIndexes": [0],
                "importance": "required",
                "masteryIndicators": ["Explain parameters"],
                "scopeAnchorIndexes": [routing_anchor],
                "coverageRequirements": ["Explain parameters"],
            },
        ],
        "edges": [],
    }
    model = ScriptedPipelineModel([deepcopy(response), deepcopy(response)])

    with pytest.raises(Exception, match="duplicates a required scope anchor"):
        KnowledgeGenerator(model).generate(scope, capability_graph)

    assert len(model.calls) == 2
