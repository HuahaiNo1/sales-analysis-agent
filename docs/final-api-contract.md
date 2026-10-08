# Final backend/frontend contract (2026-10-07)

This file is the implementation contract, not a claim of completed validation.

## Bounded review

`POST /api/conversations/{id}/runs` preserves `message`, `client_request_id`, and `expected_state_version`. Optional `review` changes the run kind to review:

```json
{"period":{"start":"2025-09-01","end":"2025-10-01"},"comparison":{"start":"2025-08-01","end":"2025-09-01"},"metric":"sales_amount","filters":[],"drill_down":true}
```

- Period end is exclusive. Existing period, coverage and filter validation applies
- `metric`: sales_amount | units_sold | gross_profit. These are the existing additive metrics; ordinary queries keep all five metrics
- `filters`: existing DimensionFilter[], default []; maximum 7 to leave a bounded drill filter slot
- `drill_down`: boolean, default false
- Server-owned `sales_review_v1` template: overall comparison → category contribution → store contribution → optional largest-absolute category → product contribution
- At most 4 QuerySpec executions (not 4 SQL statements); no recursive drill or arbitrary plan. The main DeepAgent coordinates allowed queries; exactly one registered analysis child reads authorized deterministic evidence
- Early stop: no matching data, no change, drill disabled, ambiguous/filtered category, no drill candidate, or query limit. Errors/cancellation publish no partial result
- Existing poll/cancel/idempotency/conversation version remain in force. Existing `result` is the overall result for compatibility

`GET /api/runs/{id}` adds `kind: "query" | "review"`, and `review: ReviewPayload | null`. Active stage values may be `review_overall`, `review_category`, `review_store`, `review_drill`, `analyzing`.

```ts
type Evidence = {
  evidence_id: string; role: 'overall'|'category'|'store'|'drill';
  title: string; result: QueryResult;
}
type ReviewPayload = {
  template_id: 'sales_review_v1'; template_version: '1';
  request: ReviewRequest; evidence: Evidence[];
  findings: {text:string; evidence_ids:string[]}[];
  flags: {rule_id:string; label:string; severity:'info'|'attention'; text:string; evidence_ids:string[]}[];
  assumptions: string[]; suggestions: string[];
  rule_config: {version:string; relative_change_threshold_pct:string; concentration_threshold_pct:string};
  query_count:number; max_queries:4; stop_reason:string;
}
```

All numbers/observations/flags come from deterministic aggregation and Decimal calculations. Suggestions are follow-up ideas, not causal claims or automatically scheduled actions. Evidence embeds aggregate rows, totals, chart, QuerySpec, versions and result identifiers.

## Independent persisted reports

`POST /api/reports` JSON `{run_id:string,title:string,comment?:string,suggestions?:string,client_request_id:string}` → full report, HTTP 201 (repeat same ID/body returns same report). Only succeeded/no_data runs with currently valid source evidence may be saved. Same ID with a different normalized body: 409. `title` trimmed 1–120 chars; `comment` and `suggestions` max4000 chars each; client ID8–80.

`GET /api/reports?deleted=false&limit=30&offset=0` → `{items: ReportSummary[],limit:number,offset:number}`. Deleted false is default; deleted true lists the trash. Summary: `{id,title,kind,revision,created_at,updated_at,deleted_at,source_run_id,scope_label}`.

`GET /api/reports/{id}` → full report including deleted status (trash can be inspected/restored).

Full report extends summary: `{comment:string,suggestions:string,facts:ReportFacts}`:

```ts
type ReportFacts = {
  schema_version:'report_v1'; kind:'query'|'review';
  generated_at:string; dataset_version:string; metric_version:string;
  scope:{allowed_store_ids:number[];scope_label:string;scope_version:string};
  metric_definitions: {id:string;label:string;formula:string;unit:string;description:string}[];
  result: QueryResult;
  review: ReviewPayload|null;
}
```

The report owns complete frozen JSON facts, independent of source run/result lifetime or current data version. Facts cannot be PATCHed. Metadata labels (including embedded original result expires_at) are historical; report itself never expires. Maximum serialized frozen snapshot1MiB, evidence≤4, each result≤101 displayed aggregate rows. Stored snapshots are retained until user deletion; deletion is reversible trash, not timed expiry or hard purge.

`PATCH /api/reports/{id}` JSON `{expected_revision:number,title?:string,comment?:string,suggestions?:string}` → updated full report; stale revision409; empty change422; cannot edit deleted report409. Revision starts1 and increments per effective edit/trash/restore.

`DELETE /api/reports/{id}?expected_revision=N` → full report with deleted_at set. `POST /api/reports/{id}/restore` JSON `{expected_revision:N}` → full report restored. Repeating current-state transition is a no-op but expected_revision must still match.

`GET /api/reports/{id}/export?format=markdown|html` → attachment; default markdown. Deleted reports must be restored before export (409). Escaped deterministic Markdown or standalone HTML with optional code-generated static SVG; no external scripts/assets or model calls. Includes title, periods, definitions, immutable findings/evidence, assumptions, suggestions, author comments and editable proposal text with labels separating facts from annotations. Existing result CSV unchanged.

Every list/read/edit/trash/restore/export uses owner and current-account scope containment. If current allowed store set no longer contains the report's entire frozen store scope, report is absent from lists and detail/action/export returns404. Reports are not shared between demo accounts. No production identity claim. Rerun uses existing run API with a new request ID and saving creates a new report; original report facts never update.
