# target-blackbaud

A Singer target for Blackbaud SKY API, built on `hotglue-singer-sdk`.
Native streams accept Blackbaud-shaped records plus Hotglue identifiers.

## Install and run

Use Python 3.10:

```bash
poetry install
poetry run target-blackbaud --about --format=json
poetry run target-blackbaud --config /secure/config.json < /secure/data.singer
poetry run pytest -q
```

Input is Singer JSONL (`SCHEMA`, then `RECORD` messages), not raw API requests.
Inspect emitted per-record bookmarks and summary failures, not just exit status.
Keep credentials, input and state outside Git.

## Configuration

| Setting | Purpose |
| --- | --- |
| `subscription_key` | Required Blackbaud SKY subscription key. |
| `access_token` | Current OAuth access token. |
| `expires_in` | Absolute Unix expiry timestamp, as stored by Hotglue (not a relative duration). |
| `_refresh_token_via_hg_api` | Defaults to `true`; refresh through the linked Hotglue connection. |
| `client_id`, `client_secret`, `refresh_token` | Standalone OAuth refresh credentials; set `_refresh_token_via_hg_api: false` for standalone use. |
| `gift_write_mode` | `direct` (default) or `batch`. |
| `default_fund_id` | Fallback fund ID when a gift has no `gift_splits`. Explicit splits take precedence. |
| `gift_batch_id` | Existing batch for batch-mode gifts without a per-record `batch_id`. |
| `gift_batch_description` | Description for the automatically created batch; defaults to `hotglue gifts import`. |

Hotglue-managed refresh requires the linked connection and runtime environment
variables `ENV_ID`, `FLOW`, `TENANT`, `TARGET`, and `API_KEY`. OAuth authorization
and initial code exchange belong to connection setup, not Singer records.

## Native streams

Include a stable, nonempty `externalId` on every record. It identifies the source
record in Hotglue state; it is not sent to Blackbaud. `id`, when supplied or
resolved from a snapshot, is the destination system record ID, not a source ID.
Blackbaud IDs are sent as strings. Other native fields pass through unless noted.

| Stream | Behavior |
| --- | --- |
| `constituents` | Create; resolve by explicit/snapshot ID or strict email search. Multiple email matches fail rather than choosing a donor. Existing donors retain `first`, `middle`, `last`, `name`, `type`, `email`, `phone` and `address`; only other mapped fields are patched. Identity-only updates skip. New constituents default to type `Individual`. |
| `addresses` | Requires `constituent_id`. List all existing addresses and add only if the normalized address differs. Existing addresses are never edited. The new address is preferred only if it is the first. |
| `phones` | Requires `constituent_id`. Add only when the digits-normalized number is absent. Existing phone records are never edited. |
| `constituent_custom_fields` | `parent_id`, `category`, `value` create or match a value on the constituent; an explicit `id` identifies the custom-field value to patch. Ambiguous categories fail. |
| `gift_custom_fields` | Same semantics for a direct gift. |
| `gifts` in direct mode | POST native gifts: donations, other charges, recurring schedules and linked recurring payments. Existing IDs use the restricted gift PATCH contract below. |
| `recurring_gift_statuses` | Requires `gift_id` and `gift_status`: `Active`, `Held`, or `Cancelled`; PUT to the recurring-gift status endpoint. |
| `gift_batches` | Create a batch using native fields such as `batch_description`, `batch_number`, `expected_number`, `expected_batch_total`. Existing IDs skip. |
| `gifts` in batch mode | Add up to 50 gifts per request to the specified batch. Existing gift IDs skip; this mode does not patch gifts. |

Custom-field values preserve JSON types, including `false`, zero, decimals,
ISO date strings and fuzzy-date objects. A patch sends only `value`. The path ID
is the custom-field value ID, not the parent ID. Categories/types must already
exist in Blackbaud. A sample mapping key such as `bw_donor_id` is not automatically
a Blackbaud constituent field: map to a documented native property or export a
separate custom-field record.

The existing `contacts`, `opportunities`, `actions`, and `unsubscribes` streams
remain available with their existing unified/native mappings. Use `constituents`
for the conservative BetterWorld donor policy; `contacts` is the legacy CRM path.

### Relationships and repeat imports

Export parents before children. Top-level `constituent_id`, custom-field
`parent_id`, status `gift_id`, batch-mode `batch_id`, and direct-mode
`linked_gifts` entries can refer to source `externalId` values resolved through
successful same-run bookmarks or Hotglue snapshots. Alternatively, supply known
Blackbaud IDs. Failed parent records must not route child writes.

The SDK reads snapshots from `SNAPSHOT_DIR` (otherwise the Hotglue job snapshot
directory), named `<stream>_<FLOW>.snapshot.csv` or `.snapshot.parquet`, with
`InputId` and `RemoteId`. Reuse state with `--state /secure/state.json` as well as
snapshots to skip successfully written, unchanged native records. Changed records
follow each stream's update/skip policy; failed records remain retryable.

Do not switch gift modes while reusing incompatible gift snapshots. Batch-gift
IDs are not approved direct-gift IDs. A same-run batch result must explicitly
return `approved_gift_id` before it can route a direct gift custom-field/status
operation; batch snapshots alone cannot establish that mapping. Supply a verified
approved direct-gift ID for later operations. Put custom fields directly inside
batch gifts' `custom_fields` rather than writing them through the direct stream.

### Direct gift updates are intentionally restricted

Blackbaud's published `GiftEdit` contract permits only:

```text
lookup_id, date, is_anonymous, reference, gift_status,
recurring_gift_status_date, gift_code, subtype, constituency, marketing_details
```

Send an update containing `id`, `externalId` and only the changed supported
fields. Resending the full create body, including `amount`, `payments`, `type`,
or `gift_splits`, fails explicitly instead of silently dropping financial edits.
Posted gifts cannot be edited. `gift_status` updates are restricted by Blackbaud
to applicable gift types/transitions. Refunds and financial corrections need a
separately agreed Blackbaud workflow; full-body PATCH is not supported by the
published API contract.

### Batch operations

Batch creation uses `POST /gift-batch/v1/giftbatches`; insertion uses
`POST /gift/v1/giftbatches/{id}/gifts` with `{"gifts": [...]}`. Provide a per-record
`batch_id` (including a preceding `gift_batches.externalId`) to route multiple
batches in one job. Otherwise use `gift_batch_id`, or one batch is created per job.

Each submitted batch gift needs a distinct `lookup_id`; if omitted, it is filled
from `externalId`. A missing `reference` defaults to that lookup ID. SKY can
replace submitted lookup IDs with generated UUIDs: results are matched by echoed
lookup ID or, if it was replaced, a reference unique within the submitted group.
Use distinct references for batch gifts. Array position is never used.
Missing/ambiguous results, root errors and gift-level errors are not
reported as successful. Returned `approved_gift_id` is preserved separately.
For recurring card payments in batches, `CreditCard` is converted to `Other`.
Missing posting status defaults to `DoNotPost` with a null posting date; receipt
defaults are left to Blackbaud.

Daily grouping by organization, gift type and local date; timezone conversion;
fund/appeal/package selection; category validation; and delayed status changes
(such as 48 hours) belong to the application/ETL orchestration. This target does
not schedule work, approve batches, or fetch reference catalogs. Reference reads
remain tap work.

## Failure and duplicate safety

Requests time out after 60 seconds. HTTP 429 receives bounded retries honoring
`Retry-After` (cooldowns over five minutes fail rather than retrying early).
Transient connection errors and 5xx responses are retried only for GETs.
Authorization errors and all other non-2xx responses fail.

Writes are **not** automatically retried after timeouts, connection failures or
5xx responses: Blackbaud may already have created the record. Reconcile the
remote write before replaying an ambiguous failure or losing local state. A
lookup/reference ID is useful evidence, not a documented idempotency guarantee;
gift lists have delayed visibility and absence from a list does not prove failure.

Local mocked Singer tests validate payloads, state, ordering and retries. They
do not establish live Blackbaud acceptance or successful Hotglue deployment.
