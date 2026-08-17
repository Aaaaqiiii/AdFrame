# Product Image Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户在现有产品图片卡片中保存预设角度或自定义名称，并在刷新项目后恢复。

**Architecture:** 后端继续把逐图名称存入 `Asset.profile_json`，通过单张图片 PATCH 接口修改，不增加数据库字段。前端在 `ProductReferenceCard` 原位编辑，成功后由 `App` 更新现有 material state。

**Tech Stack:** FastAPI、Pydantic、SQLAlchemy、React 19、TypeScript、Vitest、pytest

## Global Constraints

- 保留现有卡片布局、上传流程和 AI 分析结果。
- 名称修改不得创建 Job、改变图片分析状态或要求数据库迁移。
- 自定义名称去除首尾空格，长度为 1–40 个字符。
- 不增加第三方依赖。

---

### Task 1: Persist per-image display names

- [x] Add failing API coverage for persistence, validation, ownership, unchanged analysis state, and no new Job.
- [x] Add `display_name` to project responses and a validated product-image PATCH route.
- [x] Run focused tests and commit the backend behavior.

### Task 2: Edit names inside the existing image cards

- [x] Add failing label-resolution coverage.
- [x] Add the PATCH API helper, restored state, callback wiring, and inline editor.
- [x] Add local card styling and run frontend/backend release gates.
- [x] Commit the frontend integration.
