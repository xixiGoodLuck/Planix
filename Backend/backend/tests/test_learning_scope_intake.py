from __future__ import annotations

import time
from copy import deepcopy

import pytest

from app.learning.contracts import LearningScope
from app.learning.generators import LearningModelOutputError, LearningModelResponse
from app.learning.runtime import (
    InMemoryArtifactStore,
    LearningRuntime,
    PostgresArtifactStore,
    PostgresLearningArtifactRepository,
)
from app.learning.services import LearningPipeline
from app.main import app
from app.routers.learning import LearningRunManager, get_learning_run_manager

from learning_pipeline_fixtures import (
    ScriptedPipelineModel,
    build_fastapi_learning_pipeline_fixture,
    fastapi_pipeline_responses,
)


INITIAL_GAPS = [
    {
        "question": "最终希望做到什么？",
        "whyItMatters": "目标成果会改变路线。",
        "affectedFields": ["target_result"],
    },
    {
        "question": "目前掌握哪些相关知识？",
        "whyItMatters": "已有基础会改变起点。",
        "affectedFields": ["current_level"],
    },
    {
        "question": "大约愿意投入多少内容时间？",
        "whyItMatters": "预算会控制内容长度。",
        "affectedFields": ["content_budget"],
    },
    {
        "question": "是否只看中文内容？",
        "whyItMatters": "语言会改变候选资源。",
        "affectedFields": ["language_preference"],
    },
    {
        "question": "是否偏好 B 站？",
        "whyItMatters": "平台偏好会改变资源排序。",
        "affectedFields": ["resource_preference"],
    },
    {
        "question": "是否已有指定视频？",
        "whyItMatters": "指定视频可以优先验证。",
        "affectedFields": ["resource_preference.user_supplied_urls"],
    },
]


class ProgressiveScopeModel:
    def __init__(self):
        self.pipeline = ScriptedPipelineModel(fastapi_pipeline_responses())
        self.scope_calls: list[dict] = []

    def complete(self, *, stage, feature, system, payload, response_type, max_tokens):
        if feature != "learning_scope_analysis":
            return self.pipeline.complete(
                stage=stage,
                feature=feature,
                system=system,
                payload=payload,
                response_type=response_type,
                max_tokens=max_tokens,
            )
        self.scope_calls.append(deepcopy(payload))
        message = payload["latestUserMessage"]
        if "分析失败" in message:
            raise LearningModelOutputError(stage, "provider prompt and token details")
        if "模型返回URL" in message:
            raw = {
                "goalIdentified": True,
                "userGoal": "https://www.bilibili.com/video/BV1xx411c7mD",
                "recommendedGaps": [],
            }
        elif "我会 Python" in message and "Routing" in message:
            raw = {
                "goalIdentified": True,
                "userGoal": "学习 FastAPI 的 Routing、GET、POST 和 Swagger",
                "targetResult": "学习 FastAPI",
                "currentLevel": {
                    "summary": "用户已掌握 Python，但未说明对 FastAPI 或 Web 开发的熟悉程度。",
                    "knownSkills": ["Python"],
                    "knownTechnologies": ["Python"],
                    "uncertainAreas": [],
                },
                "recommendedGaps": [],
            }
        elif "我会 Python" in message and "CRUD" in message:
            raw = {
                "goalIdentified": False,
                "targetResult": "完成 CRUD API",
                "currentLevel": {
                    "summary": "我会 Python",
                    "knownSkills": ["Python"],
                    "knownTechnologies": ["Python"],
                    "uncertainAreas": [],
                },
                "recommendedGaps": [],
            }
        elif "我会 Python" in message:
            raw = {
                "goalIdentified": False,
                "currentLevel": {
                    "summary": "我会 Python",
                    "knownSkills": ["Python"],
                    "knownTechnologies": ["Python"],
                    "uncertainAreas": [],
                },
                "recommendedGaps": INITIAL_GAPS,
            }
        elif "不要替我选择" in message:
            raw = {
                "goalIdentified": True,
                "userGoal": "FastAPI",
                "targetResult": "完成模型示例项目",
                "currentLevel": {
                    "summary": "熟悉 Docker",
                    "knownSkills": ["Docker"],
                    "knownTechnologies": ["Docker"],
                    "uncertainAreas": [],
                },
                "recommendedGaps": INITIAL_GAPS,
            }
        elif "仍然不回答" in message:
            raw = {
                "goalIdentified": False,
                "recommendedGaps": INITIAL_GAPS,
            }
        else:
            raw = {
                "goalIdentified": True,
                "userGoal": "FastAPI",
                "recommendedGaps": INITIAL_GAPS,
            }
        return LearningModelResponse(
            value=response_type.model_validate(raw),
            model_usage={"provider": "fixture", "model": "progressive-scope"},
        )


def _wait_for_terminal(client, run_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        response = client.get(f"/api/learning/runs/{run_id}")
        assert response.status_code == 200
        payload = response.json()
        if payload["status"] in {"completed", "failed"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("Learning run did not reach a terminal state")


@pytest.fixture()
def intake_api(client):
    managers: list[LearningRunManager] = []

    def install(*, store=None, model=None):
        artifact_store = store or InMemoryArtifactStore()
        semantic_model = model or ProgressiveScopeModel()
        provider = build_fastapi_learning_pipeline_fixture().provider

        def runtime_factory():
            return LearningRuntime(
                LearningPipeline(provider=provider, model=semantic_model),
                artifact_store=artifact_store,
            )

        manager = LearningRunManager(runtime_factory)
        managers.append(manager)
        app.dependency_overrides[get_learning_run_manager] = lambda: manager
        return client, manager, artifact_store, semantic_model, runtime_factory

    yield install

    app.dependency_overrides.pop(get_learning_run_manager, None)
    for manager in managers:
        manager.shutdown()


def _create(client, message="我想学习 FastAPI。"):
    response = client.post(
        "/api/learning/intakes",
        json={"message": message, "preferredLanguage": "zh-CN"},
    )
    return response


def test_initial_intake_returns_known_topic_and_one_batch_of_optional_gaps(intake_api) -> None:
    client, _, _, _, _ = intake_api()

    response = _create(client)
    payload = response.json()

    assert response.status_code == 201
    assert payload["scope"]["userGoal"] == "FastAPI"
    assert payload["scope"]["confirmed"] is False
    assert payload["review"]["knownInformation"][0]["field"] == "user_goal"
    assert 2 <= len(payload["review"]["recommendedGaps"]) <= 6
    assert all(gap["blocking"] is False for gap in payload["review"]["recommendedGaps"])
    assert payload["review"]["readyForPlanning"] is False
    assert payload["runId"] is None


def test_continue_without_supplement_starts_the_existing_learning_runtime(intake_api) -> None:
    client, _, store, _, _ = intake_api()
    created = _create(client).json()

    continued = client.post(f"/api/learning/intakes/{created['intakeId']}/continue")

    assert continued.status_code == 202
    payload = continued.json()
    assert payload["runId"] == created["intakeId"]
    assert payload["scope"]["confirmed"] is True
    versions = store.list_versions(
        created["intakeId"],
        "learning_scope",
        created["scope"]["artifactId"],
    )
    assert [item.version for item in versions] == [1, 2]
    assert _wait_for_terminal(client, payload["runId"])["status"] == "completed"


def test_natural_language_continue_phrase_maps_to_typed_continue(intake_api) -> None:
    client, _, store, semantic_model, _ = intake_api()
    created = _create(client).json()
    scope_call_count = len(semantic_model.scope_calls)

    response = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={"message": "其他先不填，直接继续。", "preferredLanguage": "zh-CN"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["runId"] == created["intakeId"]
    assert payload["scope"]["confirmed"] is True
    assert len(semantic_model.scope_calls) == scope_call_count
    assert [
        item.version
        for item in store.list_versions(
            created["intakeId"],
            "learning_scope",
            created["scope"]["artifactId"],
        )
    ] == [1, 2]
    assert _wait_for_terminal(client, payload["runId"])["status"] == "completed"


def test_complete_supplement_is_ready_and_auto_starts(intake_api) -> None:
    client, _, _, _, _ = intake_api()
    created = _create(client).json()

    response = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "我会 Python，最后想完成 CRUD API，内容控制在 90 分钟，只看中文 B站视频。",
            "preferredLanguage": "zh-CN",
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["review"]["readyForPlanning"] is True
    assert payload["runId"] == created["intakeId"]
    assert payload["scope"]["contentBudget"]["targetTotalMinutes"] == 90
    assert payload["scope"]["languagePreference"]["preferredLanguages"] == ["zh-CN"]
    assert payload["scope"]["resourcePreference"]["preferredPlatforms"] == ["bilibili"]


def test_supported_supplement_target_is_bounded_and_model_commentary_is_not_a_fact(intake_api) -> None:
    client, _, _, _, _ = intake_api()
    created = _create(client).json()

    payload = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "我会 Python，想理解 Routing、GET、POST 和 Swagger，内容控制在 90 分钟，只看中文 B 站视频。",
            "preferredLanguage": "zh-CN",
        },
    ).json()

    assert payload["scope"]["userGoal"] == "学习 FastAPI 的 Routing、GET、POST 和 Swagger"
    assert payload["scope"]["targetResult"] == "理解 Routing、GET、POST 和 Swagger"
    assert payload["scope"]["currentLevel"]["summary"] == "Python"
    assert "未说明" not in str(payload["review"]["knownInformation"])


def test_partial_supplement_returns_only_remaining_high_impact_gap(intake_api) -> None:
    client, _, _, _, _ = intake_api()
    created = _create(client).json()

    response = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={"message": "我会 Python。", "preferredLanguage": "zh-CN"},
    )
    payload = response.json()

    high_fields = {
        field
        for gap in payload["review"]["recommendedGaps"]
        if gap["impact"] == "high"
        for field in gap["affectedFields"]
    }
    assert payload["review"]["readyForPlanning"] is False
    assert high_fields == {"target_result"}
    assert "current_level" not in {
        field
        for gap in payload["review"]["recommendedGaps"]
        for field in gap["affectedFields"]
    }


def test_after_two_question_batches_the_system_does_not_ask_again(intake_api) -> None:
    client, _, _, _, _ = intake_api()
    created = _create(client).json()
    second = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={"message": "我会 Python。", "preferredLanguage": "zh-CN"},
    ).json()

    third = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={"message": "仍然不回答其他问题。", "preferredLanguage": "zh-CN"},
    ).json()

    assert second["review"]["recommendationRound"] == 2
    assert second["review"]["recommendedGaps"]
    assert third["scope"]["version"] == 3
    assert third["review"]["recommendedGaps"] == []
    assert third["review"]["autoContinueReason"] == "high_impact_gaps_remain"
    assert third["runId"] is None


def test_model_examples_and_assumptions_never_become_user_facts(intake_api) -> None:
    client, _, _, _, _ = intake_api()

    payload = _create(client, "我想学习 FastAPI，请不要替我选择模型示例。").json()

    assert payload["scope"]["targetResult"] == "FastAPI"
    assert payload["scope"]["currentLevel"]["sourceRefs"] == []
    assert payload["scope"]["resourcePreference"]["preferredPlatforms"] == []
    assert payload["scope"]["assumptions"]
    assert all(
        item["sourceRef"].startswith("system:scope-readiness:")
        for item in payload["scope"]["assumptions"]
    )
    known_fields = {item["field"] for item in payload["review"]["knownInformation"]}
    assert "target_result" not in known_fields
    assert "current_level" not in known_fields


def test_user_bilibili_url_is_extracted_and_never_sent_to_the_model(intake_api) -> None:
    client, _, _, model, _ = intake_api()
    supplied = "https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1"

    payload = _create(client, f"我想学习 FastAPI，先看这个视频：{supplied}").json()

    assert payload["scope"]["resourcePreference"]["userSuppliedUrls"] == [
        "https://www.bilibili.com/video/BV1xx411c7mD"
    ]
    serialized_model_payload = str(model.scope_calls[0])
    assert supplied not in serialized_model_payload
    assert "BV1xx411c7mD" not in serialized_model_payload


def test_typed_resource_urls_patch_scope_without_calling_the_model(intake_api) -> None:
    client, _, store, model, _ = intake_api()
    created = _create(client).json()
    scope_call_count = len(model.scope_calls)

    response = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "",
            "preferredLanguage": "zh-CN",
            "resourceUrls": [
                "http://www.bilibili.com/video/BV1zV2QBtE39?spm_id_from=333.1",
                "https://www.bilibili.com/video/BV1zV2QBtE39",
            ],
            "deferAutoStart": True,
        },
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["scope"]["version"] == 2
    assert payload["runId"] is None
    assert payload["scope"]["resourcePreference"]["userSuppliedUrls"] == [
        "https://www.bilibili.com/video/BV1zV2QBtE39"
    ]
    assert payload["scope"]["resourcePreference"]["preferredPlatforms"] == [
        "bilibili"
    ]
    assert len(model.scope_calls) == scope_call_count
    versions = store.list_versions(
        created["intakeId"],
        "learning_scope",
        created["scope"]["artifactId"],
    )
    assert [item.version for item in versions] == [1, 2]


def test_typed_resource_urls_are_never_in_the_semantic_model_payload(intake_api) -> None:
    client, _, _, model, _ = intake_api()
    created = _create(client).json()
    supplied = "https://www.bilibili.com/video/BV1zV2QBtE39"

    payload = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "我会 Python。",
            "preferredLanguage": "zh-CN",
            "resourceUrls": [supplied],
            "deferAutoStart": True,
        },
    ).json()

    assert payload["scope"]["version"] == 2
    assert payload["scope"]["resourcePreference"]["userSuppliedUrls"] == [supplied]
    assert payload["scope"]["sourceRefs"][-1].endswith(":resource:2")
    serialized_model_payload = str(model.scope_calls[-1])
    assert supplied not in serialized_model_payload
    assert "BV1zV2QBtE39" not in serialized_model_payload


def test_continue_preserves_the_typed_video_on_the_runtime_scope(intake_api) -> None:
    client, _, store, _, _ = intake_api()
    created = _create(client).json()
    supplied = "https://www.bilibili.com/video/BV1xx411c7mD"
    bound = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "resourceUrls": [supplied],
            "preferredLanguage": "zh-CN",
            "deferAutoStart": True,
        },
    ).json()

    continued = client.post(
        f"/api/learning/intakes/{created['intakeId']}/continue"
    ).json()

    assert continued["runId"] == created["intakeId"]
    assert continued["scope"]["confirmed"] is True
    assert continued["scope"]["resourcePreference"]["userSuppliedUrls"] == [supplied]
    terminal = _wait_for_terminal(client, continued["runId"])
    assert terminal["status"] == "failed"
    assert terminal["error"]["stage"] == "evidence_generation"
    versions = store.list_versions(
        created["intakeId"],
        "learning_scope",
        bound["scope"]["artifactId"],
    )
    assert [item.version for item in versions] == [1, 2, 3]
    latest_scope = store.get_artifact(created["intakeId"], versions[-1])
    assert isinstance(latest_scope, LearningScope)
    assert latest_scope.resource_preference.user_supplied_urls == [supplied]


def test_typed_resource_urls_reject_non_bilibili_values(intake_api) -> None:
    client, _, _, _, _ = intake_api()
    created = _create(client).json()

    response = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "resourceUrls": ["https://example.com/video/BV1zV2QBtE39"],
            "preferredLanguage": "zh-CN",
        },
    )

    assert response.status_code == 422
    assert "Bilibili domain" in response.text


def test_model_returned_url_is_rejected_and_no_plan_is_created(intake_api) -> None:
    client, manager, store, _, _ = intake_api()

    response = _create(client, "模型返回URL FastAPI")

    assert response.status_code == 503
    assert response.json()["detail"] == "Learning scope analysis is temporarily unavailable"
    assert manager._runs == {}
    assert store._artifacts == {}


def test_latest_scope_review_recovers_after_manager_recreation(intake_api) -> None:
    client, manager_a, store, model, runtime_factory = intake_api()
    created = _create(client).json()
    updated = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "我会 Python。",
            "preferredLanguage": "zh-CN",
            "resourceUrls": ["https://www.bilibili.com/video/BV1zV2QBtE39"],
            "deferAutoStart": True,
        },
    ).json()
    manager_a.shutdown()
    manager_b = LearningRunManager(runtime_factory)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_b

    recovered = client.get(f"/api/learning/intakes/{created['intakeId']}")

    assert recovered.status_code == 200
    assert recovered.json()["scope"] == updated["scope"]
    assert recovered.json()["review"] == updated["review"]
    assert store.list_versions(
        created["intakeId"], "learning_scope", created["scope"]["artifactId"]
    )
    assert model.scope_calls
    manager_b.shutdown()


def test_latest_scope_review_recovers_from_postgresql_after_restart(intake_api) -> None:
    persistent_store = PostgresArtifactStore(PostgresLearningArtifactRepository())
    client, manager_a, _, _, runtime_factory = intake_api(store=persistent_store)
    created = _create(client).json()
    updated = client.post(
        f"/api/learning/intakes/{created['intakeId']}/supplements",
        json={
            "message": "我会 Python。",
            "preferredLanguage": "zh-CN",
            "resourceUrls": ["https://www.bilibili.com/video/BV1zV2QBtE39"],
            "deferAutoStart": True,
        },
    ).json()
    manager_a.shutdown()
    manager_b = LearningRunManager(runtime_factory)
    app.dependency_overrides[get_learning_run_manager] = lambda: manager_b

    recovered = client.get(f"/api/learning/intakes/{created['intakeId']}")

    assert recovered.status_code == 200
    assert recovered.json()["scope"]["version"] == 2
    assert recovered.json()["scope"] == updated["scope"]
    assert recovered.json()["review"] == updated["review"]
    assert recovered.json()["scope"]["resourcePreference"]["userSuppliedUrls"] == [
        "https://www.bilibili.com/video/BV1zV2QBtE39"
    ]
    manager_b.shutdown()


def test_initial_analysis_failure_keeps_pipeline_artifacts_empty(intake_api) -> None:
    client, manager, store, _, _ = intake_api()

    response = _create(client, "分析失败：我想学习 FastAPI")

    assert response.status_code == 503
    assert manager._runs == {}
    assert store._artifacts == {}
