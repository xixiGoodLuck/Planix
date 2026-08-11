from __future__ import annotations

from dataclasses import dataclass

from app.learning.contracts import (
    CapabilityEdge,
    CapabilityGraph,
    CapabilityNode,
    ContentBudget,
    ContentSegment,
    ContentSelection,
    CoverageEdge,
    EvidenceGraph,
    EvidenceSourceRange,
    KnowledgeEdge,
    KnowledgeGraph,
    KnowledgeNode,
    LanguagePreference,
    LearningArtifactRef,
    LearningContentItem,
    LearningContentPlan,
    LearningOutcome,
    LearningQualityReport,
    LearningScope,
    RecommendedContent,
    ResourcePreference,
    SegmentEvidence,
    SelectedSegment,
    VideoResource,
    CurrentLevel,
)


@dataclass(frozen=True)
class FastApiLearningFixture:
    scope: LearningScope
    capability_graph: CapabilityGraph
    knowledge_graph: KnowledgeGraph
    evidence_graph: EvidenceGraph
    content_selection: ContentSelection
    content_plan: LearningContentPlan
    quality_report: LearningQualityReport


def _ref(artifact_type: str, artifact) -> LearningArtifactRef:
    return LearningArtifactRef(
        artifactType=artifact_type,
        artifactId=artifact.artifact_id,
        version=artifact.version,
    )


def build_fastapi_crud_learning_fixture() -> FastApiLearningFixture:
    scope = LearningScope(
        artifactId="learning-scope-fastapi-crud",
        userGoal="30天学习FastAPI并完成CRUD API",
        targetResult="能够独立实现带数据校验和持久化的FastAPI CRUD API",
        currentLevel=CurrentLevel(
            summary="会Python基础，尚未系统使用FastAPI",
            knownSkills=["Python基础"],
            knownTechnologies=["Python"],
            uncertainAreas=["异步API设计"],
            sourceRefs=["user:1"],
        ),
        contentBudget=ContentBudget(
            targetTotalMinutes=1200,
            maximumTotalMinutes=1800,
            maximumVideoCount=3,
            maximumSegmentMinutes=20,
        ),
        languagePreference=LanguagePreference(
            preferredLanguages=["zh-CN"],
            acceptableLanguages=["en"],
            subtitlesAcceptable=True,
        ),
        resourcePreference=ResourcePreference(
            preferredPlatforms=[],
            preferredStyles=["hands_on", "project_based"],
            freeOnly=True,
        ),
        assumptions=[],
        unknowns=[],
        sourceRefs=["user:1"],
        confirmed=True,
    )

    outcome = LearningOutcome(
        id="outcome-fastapi-crud",
        statement="完成可运行并可验证的FastAPI CRUD API",
        acceptanceCriteria=[
            "支持创建、读取、更新和删除",
            "请求与响应经过数据校验",
            "数据可以持久化",
        ],
        importance="required",
        sourceGoalRefs=[scope.artifact_id],
    )
    capability_graph = CapabilityGraph(
        artifactId="capability-graph-fastapi-crud",
        scopeRef=_ref("learning_scope", scope),
        outcomes=[outcome],
        capabilities=[
            CapabilityNode(
                id="capability-api-design",
                name="API Design",
                description="把资源操作映射为清晰的HTTP API。",
                whyRequired="CRUD需要稳定的资源和接口边界。",
                outcomeRefs=[outcome.id],
                importance="required",
            ),
            CapabilityNode(
                id="capability-data-validation",
                name="Data Validation",
                description="验证输入并定义稳定响应结构。",
                whyRequired="错误数据不能进入业务和持久化层。",
                outcomeRefs=[outcome.id],
                importance="required",
            ),
            CapabilityNode(
                id="capability-persistence",
                name="Persistence",
                description="将资源状态保存并重新读取。",
                whyRequired="CRUD的结果必须跨请求保存。",
                outcomeRefs=[outcome.id],
                importance="required",
            ),
            CapabilityNode(
                id="capability-crud",
                name="CRUD",
                description="实现创建、读取、更新和删除完整链路。",
                whyRequired="这是目标结果的直接能力。",
                outcomeRefs=[outcome.id],
                importance="required",
            ),
        ],
        edges=[
            CapabilityEdge(
                sourceCapabilityId="capability-api-design",
                targetCapabilityId="capability-crud",
                relation="supports",
            ),
            CapabilityEdge(
                sourceCapabilityId="capability-data-validation",
                targetCapabilityId="capability-crud",
                relation="prerequisite",
            ),
            CapabilityEdge(
                sourceCapabilityId="capability-persistence",
                targetCapabilityId="capability-crud",
                relation="prerequisite",
            ),
        ],
    )

    knowledge_nodes = [
        KnowledgeNode(
            id="knowledge-http",
            name="HTTP",
            explanation="HTTP方法和状态码表达客户端与服务端的资源操作。",
            whyRequired="API Design需要正确表达读取与变更语义。",
            capabilityRefs=["capability-api-design"],
            outcomeRefs=[outcome.id],
            importance="required",
            masteryIndicators=["能为CRUD操作选择HTTP方法和状态码"],
        ),
        KnowledgeNode(
            id="knowledge-routing",
            name="Routing",
            explanation="FastAPI路由把HTTP请求绑定到处理函数。",
            whyRequired="每个CRUD操作都需要可访问的端点。",
            capabilityRefs=["capability-api-design", "capability-crud"],
            outcomeRefs=[outcome.id],
            importance="required",
            masteryIndicators=["能定义路径参数和CRUD路由"],
        ),
        KnowledgeNode(
            id="knowledge-pydantic",
            name="Pydantic",
            explanation="Pydantic模型定义请求、响应和数据校验规则。",
            whyRequired="FastAPI依靠结构化模型保护数据边界。",
            capabilityRefs=["capability-data-validation", "capability-crud"],
            outcomeRefs=[outcome.id],
            importance="required",
            masteryIndicators=["能定义创建和更新Schema"],
        ),
        KnowledgeNode(
            id="knowledge-database",
            name="Database",
            explanation="持久化层负责保存并查询资源状态。",
            whyRequired="CRUD结果需要跨请求存在。",
            capabilityRefs=["capability-persistence", "capability-crud"],
            outcomeRefs=[outcome.id],
            importance="required",
            masteryIndicators=["能保存、查询、更新和删除记录"],
        ),
        KnowledgeNode(
            id="knowledge-crud",
            name="CRUD",
            explanation="CRUD将路由、校验和持久化组合成完整资源生命周期。",
            whyRequired="它直接对应最终目标结果。",
            capabilityRefs=["capability-crud"],
            outcomeRefs=[outcome.id],
            importance="required",
            masteryIndicators=["能端到端完成四类资源操作"],
        ),
    ]
    knowledge_graph = KnowledgeGraph(
        artifactId="knowledge-graph-fastapi-crud",
        scopeRef=_ref("learning_scope", scope),
        capabilityGraphRef=_ref("capability_graph", capability_graph),
        nodes=knowledge_nodes,
        edges=[
            KnowledgeEdge(
                sourceKnowledgeId="knowledge-http",
                targetKnowledgeId="knowledge-routing",
                relation="prerequisite",
                reason="理解HTTP语义后才能设计路由。",
            ),
            KnowledgeEdge(
                sourceKnowledgeId="knowledge-routing",
                targetKnowledgeId="knowledge-crud",
                relation="prerequisite",
                reason="CRUD操作需要路由入口。",
            ),
            KnowledgeEdge(
                sourceKnowledgeId="knowledge-pydantic",
                targetKnowledgeId="knowledge-crud",
                relation="prerequisite",
                reason="CRUD输入必须经过数据校验。",
            ),
            KnowledgeEdge(
                sourceKnowledgeId="knowledge-database",
                targetKnowledgeId="knowledge-crud",
                relation="prerequisite",
                reason="CRUD结果必须持久化。",
            ),
        ],
    )

    resource = VideoResource(
        id="video-a",
        provider="fixture",
        externalId="fixture-fastapi-crud-a",
        canonicalUrl="https://example.test/videos/fastapi-crud-a",
        title="FastAPI CRUD Learning Fixture",
        author="Planix Test Fixture",
        language="zh-CN",
        durationSeconds=7200,
        contentFingerprint="sha256:fixture-fastapi-crud-a-v1",
    )
    routing_evidence = SegmentEvidence(
        id="evidence-routing",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        segmentId="segment-routing",
        kind="transcript_span",
        supportedClaim="该片段解释HTTP方法、FastAPI路由以及CRUD端点映射。",
        sourceRange=EvidenceSourceRange(
            locatorType="transcript_chars",
            startOffset=100,
            endOffset=900,
        ),
        verificationStatus="verified",
    )
    pydantic_evidence = SegmentEvidence(
        id="evidence-pydantic",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        segmentId="segment-pydantic",
        kind="transcript_span",
        supportedClaim="该片段解释Pydantic校验、数据库持久化以及CRUD数据流。",
        sourceRange=EvidenceSourceRange(
            locatorType="transcript_chars",
            startOffset=901,
            endOffset=1800,
        ),
        verificationStatus="verified",
    )
    routing_segment = ContentSegment(
        id="segment-routing",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        startSeconds=600,
        endSeconds=1200,
        contentSummary="HTTP方法、FastAPI Routing和CRUD端点设计。",
        topics=["HTTP", "Routing", "CRUD"],
        evidenceRefs=[routing_evidence.id],
    )
    pydantic_segment = ContentSegment(
        id="segment-pydantic",
        resourceId=resource.id,
        resourceFingerprint=resource.content_fingerprint,
        startSeconds=1200,
        endSeconds=1800,
        contentSummary="Pydantic数据校验、数据库持久化与CRUD数据流。",
        topics=["Pydantic", "Database", "CRUD"],
        evidenceRefs=[pydantic_evidence.id],
    )
    coverage_edges = [
        CoverageEdge(
            id="coverage-http-routing",
            knowledgeId="knowledge-http",
            segmentId=routing_segment.id,
            evidenceRefs=[routing_evidence.id],
            coverageType="explanation",
            coverageStrength="full",
            confidence=0.95,
            reason="字幕明确解释HTTP方法和状态语义。",
        ),
        CoverageEdge(
            id="coverage-routing",
            knowledgeId="knowledge-routing",
            segmentId=routing_segment.id,
            evidenceRefs=[routing_evidence.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.96,
            reason="字幕和演示覆盖FastAPI路由定义。",
        ),
        CoverageEdge(
            id="coverage-crud-routing",
            knowledgeId="knowledge-crud",
            segmentId=routing_segment.id,
            evidenceRefs=[routing_evidence.id],
            coverageType="introduction",
            coverageStrength="partial",
            confidence=0.88,
            reason="片段将四类CRUD操作映射到端点。",
        ),
        CoverageEdge(
            id="coverage-pydantic",
            knowledgeId="knowledge-pydantic",
            segmentId=pydantic_segment.id,
            evidenceRefs=[pydantic_evidence.id],
            coverageType="demonstration",
            coverageStrength="full",
            confidence=0.96,
            reason="字幕和代码演示覆盖Pydantic模型。",
        ),
        CoverageEdge(
            id="coverage-database",
            knowledgeId="knowledge-database",
            segmentId=pydantic_segment.id,
            evidenceRefs=[pydantic_evidence.id],
            coverageType="explanation",
            coverageStrength="full",
            confidence=0.9,
            reason="字幕解释校验数据如何进入持久化层。",
        ),
    ]
    evidence_graph = EvidenceGraph(
        artifactId="evidence-graph-fastapi-crud",
        knowledgeGraphRef=_ref("knowledge_graph", knowledge_graph),
        resources=[resource],
        segments=[routing_segment, pydantic_segment],
        evidence=[routing_evidence, pydantic_evidence],
        coverageEdges=coverage_edges,
    )

    selection = ContentSelection(
        artifactId="content-selection-fastapi-crud",
        scopeRef=_ref("learning_scope", scope),
        knowledgeGraphRef=_ref("knowledge_graph", knowledge_graph),
        evidenceGraphRef=_ref("evidence_graph", evidence_graph),
        selectedSegments=[
            SelectedSegment(
                id="selection-routing",
                segmentId=routing_segment.id,
                knowledgeRefs=["knowledge-http", "knowledge-routing", "knowledge-crud"],
                coverageEdgeRefs=["coverage-http-routing", "coverage-routing", "coverage-crud-routing"],
                evidenceRefs=[routing_evidence.id],
                viewingOrder=0,
                selectionReason="一个片段同时建立HTTP、Routing和CRUD端点映射。",
            ),
            SelectedSegment(
                id="selection-pydantic",
                segmentId=pydantic_segment.id,
                knowledgeRefs=["knowledge-pydantic", "knowledge-database"],
                coverageEdgeRefs=["coverage-pydantic", "coverage-database"],
                evidenceRefs=[pydantic_evidence.id],
                viewingOrder=1,
                selectionReason="集中覆盖数据校验和持久化数据流。",
            ),
        ],
        coverageGaps=[],
        totalDurationSeconds=0,
    )

    selected_by_knowledge = {
        "knowledge-http": ("selection-routing", routing_segment),
        "knowledge-routing": ("selection-routing", routing_segment),
        "knowledge-crud": ("selection-routing", routing_segment),
        "knowledge-pydantic": ("selection-pydantic", pydantic_segment),
        "knowledge-database": ("selection-pydantic", pydantic_segment),
    }
    content_items: list[LearningContentItem] = []
    for node in knowledge_nodes:
        selection_id, segment = selected_by_knowledge[node.id]
        content_items.append(
            LearningContentItem(
                knowledgeId=node.id,
                knowledgeName=node.name,
                knowledgeExplanation=node.explanation,
                whyRequired=node.why_required,
                recommendedContent=[
                    RecommendedContent(
                        selectionId=selection_id,
                        resourceId=resource.id,
                        segmentId=segment.id,
                        videoTitle=resource.title,
                        segmentSummary=segment.content_summary,
                        durationSeconds=segment.end_seconds - segment.start_seconds,
                        recommendationReason="该已验证片段直接覆盖当前知识点。",
                    )
                ],
            )
        )
    content_plan = LearningContentPlan(
        artifactId="learning-content-plan-fastapi-crud",
        scopeRef=_ref("learning_scope", scope),
        knowledgeGraphRef=_ref("knowledge_graph", knowledge_graph),
        evidenceGraphRef=_ref("evidence_graph", evidence_graph),
        contentSelectionRef=_ref("content_selection", selection),
        items=content_items,
        totalDurationSeconds=0,
        evidenceGaps=[],
    )
    quality_report = LearningQualityReport(
        artifactId="learning-quality-fastapi-crud",
        targetRef=_ref("learning_content_plan", content_plan),
        scopeRef=_ref("learning_scope", scope),
        capabilityGraphRef=_ref("capability_graph", capability_graph),
        knowledgeGraphRef=_ref("knowledge_graph", knowledge_graph),
        evidenceGraphRef=_ref("evidence_graph", evidence_graph),
        contentSelectionRef=_ref("content_selection", selection),
        hardRulesPassed=True,
        issues=[],
        remainingGaps=[],
    )
    return FastApiLearningFixture(
        scope=scope,
        capability_graph=capability_graph,
        knowledge_graph=knowledge_graph,
        evidence_graph=evidence_graph,
        content_selection=selection,
        content_plan=content_plan,
        quality_report=quality_report,
    )


__all__ = ["FastApiLearningFixture", "build_fastapi_crud_learning_fixture"]
