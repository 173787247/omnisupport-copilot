# Handling Plan Card Skill

## Purpose

Expose a structured webhook troubleshooting plan card for support agents.

## Inputs

- `question` (required)
- optional `ticket_id`, `product_line`, `retrieval_mode`

## Outputs

`HandlingPlanCard` schema (`contracts/product/handling_plan_card.schema.json`).

## Failure modes

- no evidence → abstain/clarify
- RAG unavailable → HTTP 502, audited
- financial request → HITL, never auto-executes

## Permissions

Product API authenticated roles (`support_agent` / `admin`). Actions still go through `ticket_update` controls.

## Version

`handling-plan-card-v1`
