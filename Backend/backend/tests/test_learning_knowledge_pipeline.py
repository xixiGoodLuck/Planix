from __future__ import annotations

import json
from typing import Any

import pytest

from app.learning.contracts import (
    ContentBudget,
    CurrentLevel,
    LanguagePreference,
    LearningAssumption,
    LearningScope,
    ResourcePreference,
)
from app.learning.generators import LearningModelResponse, RouterLearningModel
from app.learning.services import KnowledgeGenerationPipeline, KnowledgePipelineError
from app.services.llm import LlmResult


class ScriptedLearningModel:
    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def complete(
        self,
        *,
        stage: str,
        feature: str,
        system: str,
        payload: dict[str, Any],
        response_type,
        max_tokens: int,
    ):
        self.calls.append(
            {
                "stage": stage,
                "feature": feature,
                "system": system,
                "payload": payload,
                "maxTokens": max_tokens,
            }
        )
        raw = self.responses.pop(0)
        return LearningModelResponse(
            value=response_type.model_validate(raw),
            model_usage={"provider": "fixture", "model": "scripted"},
        )


class JsonLlmStub:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def complete(self, feature: str, system: str, user: str, **kwargs):
        self.calls.append(
            {
                "feature": feature,
                "system": system,
                "user": json.loads(user),
                **kwargs,
            }
        )
        return (
            LlmResult(
                content=json.dumps(self.payload, ensure_ascii=False),
                provider="fixture",
                model="json-stub",
            ),
            None,
        )


def _scope(
    user_goal: str,
    target_result: str,
    *,
    known_skills: list[str] | None = None,
    assumptions: list[LearningAssumption] | None = None,
) -> LearningScope:
    return LearningScope(
        artifactId=f"scope-{len(user_goal)}-{len(target_result)}",
        userGoal=user_goal,
        targetResult=target_result,
        currentLevel=CurrentLevel(
            summary="、".join(known_skills or []) or "尚未确认",
            knownSkills=known_skills or [],
            sourceRefs=["user:1"],
        ),
        contentBudget=ContentBudget(targetTotalMinutes=600),
        languagePreference=LanguagePreference(preferredLanguages=["zh-CN"]),
        resourcePreference=ResourcePreference(preferredStyles=["hands_on"]),
        assumptions=assumptions or [],
        unknowns=[],
        sourceRefs=["user:1"],
        confirmed=True,
    )


def _fastapi_responses() -> list[dict[str, Any]]:
    return [
        {
            "outcomes": [
                {
                    "statement": "能够独立理解并实现基础CRUD API",
                    "acceptanceCriteria": [
                        "能创建API路由",
                        "能处理并校验请求数据",
                        "能完成数据库CRUD",
                    ],
                    "importance": "required",
                }
            ]
        },
        {
            "capabilities": [
                {
                    "name": "API设计能力",
                    "description": "把资源操作映射为清晰的HTTP接口。",
                    "whyRequired": "CRUD必须具有稳定的接口边界。",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "数据建模能力",
                    "description": "定义并校验请求与响应数据。",
                    "whyRequired": "CRUD输入必须经过结构化校验。",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "数据持久化能力",
                    "description": "保存、查询并更新资源状态。",
                    "whyRequired": "CRUD结果必须跨请求保留。",
                    "outcomeIndexes": [0],
                    "importance": "required",
                },
                {
                    "name": "API测试能力",
                    "description": "验证CRUD端点的输入与结果。",
                    "whyRequired": "验收标准需要可重复检查。",
                    "outcomeIndexes": [0],
                    "importance": "important",
                },
            ],
            "edges": [
                {"sourceIndex": 1, "targetIndex": 2, "relation": "supports"},
                {"sourceIndex": 0, "targetIndex": 3, "relation": "supports"},
            ],
        },
        {
            "knowledge": [
                {
                    "name": "HTTP",
                    "explanation": "HTTP方法和状态码表达资源操作。",
                    "whyRequired": "它为API设计能力提供协议语义。",
                    "capabilityIndexes": [0],
                    "importance": "required",
                    "masteryIndicators": ["能为CRUD操作选择方法和状态码"],
                },
                {
                    "name": "Routing",
                    "explanation": "Routing把HTTP请求绑定到处理函数。",
                    "whyRequired": "CRUD操作需要可访问的接口入口。",
                    "capabilityIndexes": [0],
                    "importance": "required",
                    "masteryIndicators": ["能定义路径参数和CRUD路由"],
                },
                {
                    "name": "Pydantic",
                    "explanation": "Pydantic定义请求与响应的数据结构。",
                    "whyRequired": "它直接支持数据建模和校验能力。",
                    "capabilityIndexes": [1],
                    "importance": "required",
                    "masteryIndicators": ["能定义创建和更新Schema"],
                },
                {
                    "name": "Database",
                    "explanation": "数据库保存并查询资源状态。",
                    "whyRequired": "它直接支持数据持久化能力。",
                    "capabilityIndexes": [2],
                    "importance": "required",
                    "masteryIndicators": ["能保存并查询记录"],
                },
                {
                    "name": "CRUD",
                    "explanation": "CRUD组合创建、读取、更新和删除流程。",
                    "whyRequired": "它把全部目标能力组合为最终结果。",
                    "capabilityIndexes": [0, 1, 2, 3],
                    "importance": "required",
                    "masteryIndicators": ["能完成四类端点并验证结果"],
                },
            ],
            "edges": [
                {
                    "sourceIndex": 0,
                    "targetIndex": 1,
                    "relation": "prerequisite",
                    "reason": "先理解HTTP语义再定义路由。",
                },
                {
                    "sourceIndex": 1,
                    "targetIndex": 4,
                    "relation": "prerequisite",
                    "reason": "CRUD需要路由入口。",
                },
                {
                    "sourceIndex": 2,
                    "targetIndex": 4,
                    "relation": "prerequisite",
                    "reason": "CRUD输入需要数据校验。",
                },
                {
                    "sourceIndex": 3,
                    "targetIndex": 4,
                    "relation": "prerequisite",
                    "reason": "CRUD结果需要持久化。",
                },
            ],
        },
    ]


def _compact_responses(
    *,
    outcome: str,
    capability: str,
    knowledge: list[str],
) -> list[dict[str, Any]]:
    return [
        {
            "outcomes": [
                {
                    "statement": outcome,
                    "acceptanceCriteria": [f"能够展示{outcome}"],
                    "importance": "required",
                }
            ]
        },
        {
            "capabilities": [
                {
                    "name": capability,
                    "description": f"具备{capability}。",
                    "whyRequired": f"该能力直接支持目标：{outcome}。",
                    "outcomeIndexes": [0],
                    "importance": "required",
                }
            ],
            "edges": [],
        },
        {
            "knowledge": [
                {
                    "name": name,
                    "explanation": f"理解并应用{name}。",
                    "whyRequired": f"{name}支持{capability}。",
                    "capabilityIndexes": [0],
                    "importance": "required" if index == 0 else "important",
                    "masteryIndicators": [f"能解释并使用{name}"],
                }
                for index, name in enumerate(knowledge)
            ],
            "edges": [
                {
                    "sourceIndex": index - 1,
                    "targetIndex": index,
                    "relation": "supports",
                    "reason": "从基础概念逐步组织知识。",
                }
                for index in range(1, len(knowledge))
            ],
        },
    ]


def test_fastapi_scope_generates_valid_outcome_capability_and_knowledge() -> None:
    scope = _scope(
        "我要学习FastAPI并完成CRUD API",
        "独立完成一个可验证的FastAPI CRUD API",
    )
    model = ScriptedLearningModel(_fastapi_responses())

    result = KnowledgeGenerationPipeline(model=model).generate(scope)

    assert len(model.calls) == 3
    assert result.outcomes[0].source_goal_refs == [scope.artifact_id]
    assert all(
        "artifactId" not in json.dumps(call["payload"], ensure_ascii=False)
        for call in model.calls
    )
    assert {item.name for item in result.capability_graph.capabilities} == {
        "API设计能力",
        "数据建模能力",
        "数据持久化能力",
        "API测试能力",
    }
    assert {item.name for item in result.knowledge_graph.nodes} == {
        "HTTP",
        "Routing",
        "Pydantic",
        "Database",
        "CRUD",
    }
    assert all(item.importance in {"required", "important", "optional"} for item in result.knowledge_graph.nodes)


def test_python_goal_does_not_expand_to_unrequested_engineering_topics() -> None:
    scope = _scope("我想学习Python", "能够独立编写基础Python程序")
    model = ScriptedLearningModel(
        _compact_responses(
            outcome="能够独立编写基础Python程序",
            capability="基础程序构建能力",
            knowledge=["控制流", "函数", "常用集合"],
        )
    )

    result = KnowledgeGenerationPipeline(model=model).generate(scope)
    serialized = json.dumps(
        result.knowledge_graph.model_dump(by_alias=True),
        ensure_ascii=False,
    ).casefold()

    assert all(
        term not in serialized
        for term in ("部署", "分布式", "高级工程", "deployment", "distributed")
    )


def test_known_python_basics_are_not_reintroduced_as_variable_basics() -> None:
    scope = _scope(
        "用Python编写一个小型命令行工具",
        "能够组织和调试一个多模块Python程序",
        known_skills=["Python基础"],
    )
    model = ScriptedLearningModel(
        _compact_responses(
            outcome="能够组织和调试小型Python程序",
            capability="程序组织能力",
            knowledge=["函数设计", "模块组织", "异常处理"],
        )
    )

    result = KnowledgeGenerationPipeline(model=model).generate(scope)

    assert "Python基础" in model.calls[2]["payload"]["scope"]["currentLevel"]["knownSkills"]
    assert all("变量" not in item.name for item in result.knowledge_graph.nodes)


def test_ambiguous_ai_goal_preserves_scope_and_uses_declared_assumption() -> None:
    assumption = LearningAssumption(
        id="assumption-ai-boundary",
        statement="当前只覆盖AI核心概念，不假定部署、研究或生产系统目标。",
        basis="用户目标宽泛，因此采用最小保守边界。",
        sourceRef="scope-default:1",
        impact="medium",
    )
    scope = _scope(
        "学习AI",
        "理解AI的核心概念和典型用途",
        assumptions=[assumption],
    )
    model = ScriptedLearningModel(
        _compact_responses(
            outcome="能够解释AI的核心概念和典型用途",
            capability="AI概念辨析能力",
            knowledge=["机器学习基本概念", "训练数据与模型"],
        )
    )

    result = KnowledgeGenerationPipeline(model=model).generate(scope)

    assert result.scope.user_goal == "学习AI"
    assert result.scope.assumptions == [assumption]
    assert result.outcomes[0].source_goal_refs == [scope.artifact_id]
    assert "部署" not in "".join(item.name for item in result.knowledge_graph.nodes)


def test_invalid_model_output_is_rejected_by_draft_contract() -> None:
    scope = _scope("我想学习Python", "能够编写基础Python程序")
    llm = JsonLlmStub(
        {
            "outcomes": [
                {
                    "id": "model-must-not-own-this-id",
                    "statement": "能够编写基础Python程序",
                    "acceptanceCriteria": ["能运行一个基础程序"],
                    "importance": "required",
                }
            ]
        }
    )
    model = RouterLearningModel(llm=llm)

    with pytest.raises(KnowledgePipelineError, match="failed contract validation") as caught:
        KnowledgeGenerationPipeline(model=model).generate(scope)

    assert caught.value.stage == "learning_outcomes"
    assert len(llm.calls) == 1
    assert llm.calls[0]["task_type"] == "planning_learning"
    assert llm.calls[0]["record_run"] is False


def test_pipeline_stops_before_knowledge_when_capability_index_is_invalid() -> None:
    scope = _scope("我想学习Python", "能够编写基础Python程序")
    responses = _compact_responses(
        outcome="能够编写基础Python程序",
        capability="基础程序构建能力",
        knowledge=["函数"],
    )
    responses[1]["capabilities"][0]["outcomeIndexes"] = [9]
    model = ScriptedLearningModel(responses)

    with pytest.raises(KnowledgePipelineError, match="available range") as caught:
        KnowledgeGenerationPipeline(model=model).generate(scope)

    assert caught.value.stage == "learning_capabilities"
    assert [item["stage"] for item in model.calls] == [
        "learning_outcomes",
        "learning_capabilities",
    ]


def test_pipeline_validates_capability_graph_before_generating_knowledge() -> None:
    scope = _scope(
        "我要学习FastAPI并完成CRUD API",
        "独立完成一个可验证的FastAPI CRUD API",
    )
    responses = _fastapi_responses()
    responses[1]["edges"] = [
        {"sourceIndex": 0, "targetIndex": 1, "relation": "prerequisite"},
        {"sourceIndex": 1, "targetIndex": 0, "relation": "prerequisite"},
    ]
    model = ScriptedLearningModel(responses)

    with pytest.raises(KnowledgePipelineError, match="capability_cycle") as caught:
        KnowledgeGenerationPipeline(model=model).generate(scope)

    assert caught.value.stage == "learning_capabilities_validation"
    assert [item["stage"] for item in model.calls] == [
        "learning_outcomes",
        "learning_capabilities",
    ]
