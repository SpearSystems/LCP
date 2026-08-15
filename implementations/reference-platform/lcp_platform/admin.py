"""Administrative CLI for local/reference deployments."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path

from .config import PlatformConfig
from .router import Platform


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage an LCP reference platform")
    parser.add_argument("--database", default=None, help="SQLite path; defaults to LCP_DATABASE_PATH")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("init", help="Initialize the database")

    credential = commands.add_parser("credential", help="Manage sender credentials")
    credential_sub = credential.add_subparsers(dest="credential_command", required=True)
    add_credential = credential_sub.add_parser("upsert")
    add_credential.add_argument("--sender-id", required=True)
    add_credential.add_argument("--tenant-id", default="default")
    add_credential.add_argument("--scope", action="append", dest="scopes")
    add_credential.add_argument("--hmac-secret")
    add_credential.add_argument("--previous-hmac-secret")
    add_credential.add_argument("--api-key")

    privacy = commands.add_parser("privacy", help="Perform controlled privacy operations")
    privacy_sub = privacy.add_subparsers(dest="privacy_command", required=True)
    erase_lead = privacy_sub.add_parser("erase-lead")
    erase_lead.add_argument("--lead-id", required=True)
    erase_lead.add_argument("--actor-id", default="operator")

    offer = commands.add_parser("offer", help="Manage buyer offers")
    offer_sub = offer.add_subparsers(dest="offer_command", required=True)
    upsert_offer = offer_sub.add_parser("upsert")
    upsert_offer.add_argument("--file", required=True, type=Path)

    args = parser.parse_args()
    config = PlatformConfig.from_env()
    if args.database:
        config = replace(config, database_path=Path(args.database))
    platform = Platform(config)
    try:
        if args.command == "init":
            print(f"Initialized {config.database_path}")
        elif args.command == "credential":
            hmac_secret = args.hmac_secret or os.environ.get("LCP_ADMIN_HMAC_SECRET")
            if not hmac_secret and not args.api_key:
                raise SystemExit("Provide --hmac-secret, LCP_ADMIN_HMAC_SECRET, or --api-key")
            platform.upsert_credential(
                args.sender_id,
                tenant_id=args.tenant_id,
                scopes=args.scopes or ["*"],
                hmac_secret=hmac_secret,
                previous_hmac_secret=args.previous_hmac_secret,
                api_key=args.api_key,
            )
            print(f"Upserted credential for {args.sender_id}")
        elif args.command == "privacy":
            if args.privacy_command == "erase-lead":
                platform.erase_lead(args.lead_id, actor_id=args.actor_id)
                print(f"Erased lead {args.lead_id}")
        elif args.command == "offer":
            with args.file.open(encoding="utf-8") as handle:
                offer_data = json.load(handle)
            platform.upsert_offer(offer_data)
            print(f"Upserted offer {offer_data['offer_id']}")
    finally:
        platform.close()


if __name__ == "__main__":
    main()
