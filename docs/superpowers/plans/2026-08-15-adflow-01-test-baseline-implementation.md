# AdFlow Test Baseline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前后端失败测试恢复为可信、可重复、不会访问正式数据库的基线。

**Architecture:** 保留 SQLite 作为单元和 API 测试数据库，真实 PostgreSQL 留到迁移验收。修正已经与异步提示词行为脱节的测试、SQLAlchemy UUID 类型错误和 FFmpeg 环境识别，不为通过测试改变正确业务行为。

**Tech Stack:** pytest、FastAPI TestClient、SQLAlchemy 2、SQLite、FFmpeg/FFprobe。

## Global Constraints

- 所有 pytest 进程必须在导入 `app.db.session` 前设置隔离 SQLite URL。
- 测试不得连接或清理 `.env` 中的 PostgreSQL。
- 异步提示词测试断言“入队与 Worker 完成”，不得恢复 HTTP 内同步调用。
- SQLAlchemy UUID 列只能与 `UUID` 对象比较。
- FFmpeg/FFprobe 缺失或不可执行时明确跳过；完整本机验收要求跳过数为 0。
- 不修改业务代码来迁就过期断言。
- 本计划允许且只允许 3 个严格模式缺陷使用 `pytest.mark.xfail(strict=True)`；计划 2 必须移除标记并修复。

---

## File Map

- Modify: `backend/tests/conftest.py` — 隔离数据库创建、销毁和媒体工具能力标记。
- Modify: `backend/tests/test_media.py` — 使用能力标记运行真实媒体测试。
- Modify: `backend/tests/test_timeline_api.py` — 使用相同媒体能力标记。
- Modify: `backend/tests/test_projects_api.py` — UUID 类型、生成队列与严格产品锁断言。
- Modify: `backend/tests/test_dual_product_workflows.py` — UUID 类型和严格模式当前行为断言。
- Modify: `backend/tests/test_prompting.py` — 人工版本同步、AI 版本异步。
- Modify: `backend/tests/test_simplified_prompt_workflow.py` — 通过 Worker 验证 AI 提示词。
- Test: `backend/tests/test_database_connection.py` — 保留隔离库断言。

### Task 1: Capture and classify the failing baseline

**Files:**
- Read: `backend/.pytest_cache/v/cache/lastfailed`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_database_connection.py`

**Interfaces:**
- Consumes: `DATABASE_URL` and `MEDIA_ROOT` environment variables read by `Settings`.
- Produces: `media_tools` pytest marker and one fresh SQLite schema per test session.

- [ ] **Step 1: Run the complete backend suite and preserve the exact failure list**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest -q
```

Expected: current baseline reports failures; record failing node IDs in the task notes before edits.

- [ ] **Step 2: Add deterministic media capability detection to `conftest.py`**

```python
import subprocess

import pytest


def _tool_runs(name: str) -> bool:
    executable = shutil.which(name)
    if not executable:
        return False
    try:
        subprocess.run([executable, "-version"], check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False
    return True


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "media_tools: requires executable ffmpeg and ffprobe")


def pytest_collection_modifyitems(items) -> None:
    if _tool_runs("ffmpeg") and _tool_runs("ffprobe"):
        return
    marker = pytest.mark.skip(reason="FFmpeg/FFprobe is not executable in this environment")
    for item in items:
        if "media_tools" in item.keywords:
            item.add_marker(marker)
```

Keep the existing environment assignment and `pytest_sessionfinish()` cleanup unchanged.

- [ ] **Step 3: Mark only tests that execute real FFmpeg binaries**

Add `@pytest.mark.media_tools` to `video_31_seconds`, `video_with_cut`, and `test_analysis_start_creates_ai_timeline_from_real_candidate_cuts`. Do not mark tests that mock `probe_video`, `clip_video`, or subprocess calls.

- [ ] **Step 4: Verify database isolation and media collection**

```powershell
python -m pytest tests/test_database_connection.py tests/test_media.py tests/test_timeline_api.py -q
```

Expected on the configured workstation: PASS with 0 skipped. In a restricted sandbox: only explicitly marked real-media tests may be skipped.

- [ ] **Step 5: Commit the environment-safe baseline fixture**

```powershell
git add backend/tests/conftest.py backend/tests/test_media.py backend/tests/test_timeline_api.py
git commit -m "test: make media baseline environment aware"
```

### Task 2: Correct SQLAlchemy UUID usage in tests

**Files:**
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_dual_product_workflows.py`
- Test: those two files

**Interfaces:**
- Consumes: API response IDs as strings.
- Produces: `UUID(response_json["id"])` before ORM lookup or UUID-column predicates.

- [ ] **Step 1: Add a focused regression assertion**

In `test_generation_api_queues_work_without_calling_provider`, convert the response ID once and use it for `session.get`:

```python
generation_id = UUID(response.json()["id"])
with SessionLocal() as session:
    generation = session.get(Generation, generation_id)
    assert generation is not None
```

- [ ] **Step 2: Run the test to expose remaining string/UUID comparisons**

```powershell
python -m pytest tests/test_projects_api.py::test_generation_api_queues_work_without_calling_provider -q
```

Expected before all replacements: FAIL with UUID coercion/binding error.

- [ ] **Step 3: Convert every ORM-facing project, shot, generation, and revision ID in the two files**

Use this exact pattern:

```python
project_id = UUID(project["id"])
shot_id = UUID(timeline["shots"][0]["id"])
generation_id = UUID(response.json()["id"])

asset = session.scalar(select(Asset).where(
    Asset.project_id == project_id,
    Asset.kind == "reference_video",
))
shot = session.get(Shot, shot_id)
generation = session.get(Generation, generation_id)
```

Import `UUID` from `uuid` at the top of each changed test file. API URLs and JSON payloads continue using strings.

- [ ] **Step 4: Run both test modules**

```powershell
python -m pytest tests/test_projects_api.py tests/test_dual_product_workflows.py -q
```

Expected: UUID binding errors are gone; remaining failures must be behavior assertions handled in Task 3.

- [ ] **Step 5: Commit UUID-safe tests**

```powershell
git add backend/tests/test_projects_api.py backend/tests/test_dual_product_workflows.py
git commit -m "test: use UUID values in ORM assertions"
```

### Task 3: Align prompt tests with the existing asynchronous contract

**Files:**
- Modify: `backend/tests/test_prompting.py`
- Modify: `backend/tests/test_simplified_prompt_workflow.py`
- Modify: `backend/tests/test_dual_product_workflows.py`
- Modify: `backend/tests/test_projects_api.py`
- Test: those four files

**Interfaces:**
- Consumes: `POST /api/projects/{id}/prompts`, `Job(kind="final_prompt_generation")`, `execute_final_prompt_job`.
- Produces: separate tests for synchronous manual prompt saves and asynchronous AI prompt jobs.

- [ ] **Step 1: Replace the stale default-AI assertion with an explicit manual save**

```python
response = client.post(
    f"/api/projects/{project['id']}/prompts",
    json={
        "product_profile": "透明精华瓶，银色瓶盖",
        "visual_direction": "清晨窗边的产品特写",
        "audio_mode": "add_style",
        "audio_style": "轻盈钢琴",
        "use_ai": False,
    },
)

assert response.status_code == 201
assert response.json() == {
    "version": 1,
    "text": "清晨窗边的产品特写",
    "status": "completed",
}
```

- [ ] **Step 2: Run the two prompt regression tests before updating the Worker assertion**

```powershell
python -m pytest tests/test_prompting.py::test_prompt_revisions_are_saved_with_audio_choice tests/test_simplified_prompt_workflow.py::test_final_prompt_uses_only_confirmed_edit_and_can_be_saved_manually -q
```

Expected: manual test passes; stale synchronous AI test fails because the route now returns `queued`.

- [ ] **Step 3: Assert AI prompt creation queues work and the Worker completes the revision**

Use the actual queue contract:

```python
response = client.post(
    f"/api/projects/{project['id']}/prompts",
    json={"visual_direction": "保持原节奏", "use_ai": True},
)
assert response.status_code == 201
assert response.json()["status"] == "queued"

with SessionLocal() as session:
    revision = session.scalar(select(PromptRevision).where(
        PromptRevision.project_id == UUID(project["id"]),
        PromptRevision.version == response.json()["version"],
    ))
    job = session.scalar(select(Job).where(
        Job.project_id == UUID(project["id"]),
        Job.kind == "final_prompt_generation",
    ))
    with patch("app.services.final_prompt.generate_final_prompt", return_value="00:00.00–00:03.20\n人工确认动作") as generate:
        execute_final_prompt_job(session, job, Settings())
    session.refresh(revision)
    assert revision.status == "completed"
    assert generate.call_args.kwargs["shots"][0]["facts"]["people"] == "人工确认人物"
```

Add the required imports: `UUID`, `Settings`, `Job`, `PromptRevision`, `execute_final_prompt_job`, and `select`.

- [ ] **Step 4: Separate route validation from asynchronous generated-text assertions**

Set `"use_ai": False` only in tests whose assertion is an immediate HTTP validation error. Tests that inspect GPT-produced text keep `use_ai=True` and execute the queued Worker job as shown in Step 3.

After the stale-contract and UUID fixes, mark only these still-valid known defects:

```python
STRICT_MODE_DEFECT = pytest.mark.xfail(
    strict=True,
    reason="strict product rule is implemented in plan 02",
)
```

Place `@STRICT_MODE_DEFECT` immediately above the existing definitions of `test_page_two_rejects_incompatible_actions_with_chinese_shot_details`, `test_preserve_product_mode_rejects_replacement_during_prompt_creation`, and `test_prompt_save_cannot_bypass_product_lock`; their complete bodies remain unchanged. Do not mark missing-product, timeline, UUID, media, queueing, database tests, or `test_page_two_applies_target_product_to_every_product_shot`.

- [ ] **Step 5: Run the prompt and dual-mode modules**

```powershell
python -m pytest tests/test_prompting.py tests/test_simplified_prompt_workflow.py tests/test_dual_product_workflows.py tests/test_projects_api.py -q
```

Expected: no unexpected failures; exactly the three named strict-mode tests are XFAIL, with no synchronous provider patch on the HTTP route.

- [ ] **Step 6: Commit the asynchronous test contract**

```powershell
git add backend/tests/test_prompting.py backend/tests/test_simplified_prompt_workflow.py backend/tests/test_dual_product_workflows.py backend/tests/test_projects_api.py
git commit -m "test: align prompt assertions with worker jobs"
```

### Task 4: Establish the green baseline gate

**Files:**
- Test: all `backend/tests`

**Interfaces:**
- Consumes: Tasks 1—3.
- Produces: a recorded full-suite baseline for the next plan.

- [ ] **Step 1: Run the complete backend suite**

```powershell
Set-Location E:\工具-商用\backend
python -m pytest -q
```

Expected: exit code 0, `101 passed, 3 xfailed, 0 failed/errors/skipped`, and on the real workstation skipped count 0.

- [ ] **Step 2: Run the existing frontend gate to prove no cross-stack regression**

```powershell
Set-Location E:\工具-商用\frontend
npm.cmd test
npm.cmd run lint
npm.cmd run build
```

Expected: all three commands exit 0.

- [ ] **Step 3: Record the exact counts in the implementation task notes**

Record `passed`, `xfailed`, `failed`, `errors`, and `skipped` from pytest plus the Vitest test count. Confirm `passed == 101`, `xfailed == 3`, `failed == 0`, `errors == 0`, and `skipped == 0`. Do not create a code commit when this step changes no files.
