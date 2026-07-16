from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

from app.config import get_settings
from app.services.provider_task_receipt_store import ProviderTaskReceiptStore


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List ambiguous paid provider submissions and explicitly release one.",
    )
    parser.add_argument("--release-scope")
    parser.add_argument("--operation")
    parser.add_argument("--fingerprint")
    parser.add_argument(
        "--i-checked-provider",
        action="store_true",
        help="required acknowledgement that provider billing/task history was checked",
    )
    args = parser.parse_args()
    settings = get_settings()
    store = ProviderTaskReceiptStore(
        settings.provider_task_receipt_store_path,
        max_records=settings.provider_task_receipt_store_max_records,
    )
    before = datetime.now(UTC) - timedelta(
        seconds=settings.provider_task_admission_stale_seconds
    )
    stale = store.stale_admissions(before=before, limit=1000)

    releasing = any((args.release_scope, args.operation, args.fingerprint))
    if not releasing:
        print(
            json.dumps(
                [
                    {
                        "scope": item.scope_key,
                        "provider": item.provider,
                        "operation": item.operation,
                        "fingerprint": item.payload_fingerprint,
                        "updatedAt": item.updated_at.isoformat(),
                    }
                    for item in stale
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if stale else 0

    if not all((args.release_scope, args.operation, args.fingerprint)):
        parser.error("release requires --release-scope, --operation and --fingerprint")
    if not args.i_checked_provider:
        parser.error("release requires --i-checked-provider")
    matches = [
        item
        for item in stale
        if item.scope_key == args.release_scope
        and item.operation == args.operation
        and item.payload_fingerprint == args.fingerprint
    ]
    if len(matches) != 1:
        parser.error(f"expected exactly one stale admission, found {len(matches)}")
    if not store.release_stale_admission(matches[0], before=before):
        parser.error("admission changed before release")
    print("released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
